#!/usr/bin/env python3
"""Free historical in-play Over odds lookup via MoviOdds public API."""
import json, re, time, unicodedata
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = "https://moviodds.com"
UA = "Mozilla/5.0 (compatible; xg-live-scanner/1.0)"
ROOT = Path(__file__).resolve().parent
SAVED = ROOT / "saved.json"
OUT = ROOT / "odds_probe.json"
ROME = ZoneInfo("Europe/Rome")

def get_json(path, params=None, timeout=12):
    url = BASE + path
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as response:
        body = response.read(15_000_000).decode("utf-8", "replace")
        return json.loads(body)

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|sc|afc|club|women|w|u\d{2})\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)

def teams_match(a, b):
    a, b = norm(a), norm(b)
    return bool(a and b and (a == b or (len(a) >= 5 and a in b) or (len(b) >= 5 and b in a)))

def fixture_match(f, entry):
    h, a = f.get("home", ""), f.get("away", "")
    home, away = entry.get("casa", ""), entry.get("ospite", "")
    return (teams_match(h, home) and teams_match(a, away)) or (teams_match(h, away) and teams_match(a, home))

def parse_alert(entry):
    dt = datetime.strptime(entry["ora"], "%d/%m/%Y %H:%M")
    return dt.replace(tzinfo=ROME).astimezone(timezone.utc)

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def line_value(value):
    try:
        return float(str(value).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None

def moviodds_lookup(entry, result_cache):
    alert_utc = parse_alert(entry)
    score = entry.get("gol") or [0, 0]
    target_line = sum(int(x) for x in score) + 0.5
    # The result endpoint accepts a UTC date. Check adjacent dates for Italy/late-night matches.
    fixtures = []
    for day_offset in (-1, 0, 1):
        day = (alert_utc.date() + timedelta(days=day_offset)).isoformat()
        if day not in result_cache:
            payload = get_json("/results", {"sport": "soccer", "date": day}, timeout=12)
            result_cache[day] = payload.get("results", [])
        for f in result_cache[day]:
            if fixture_match(f, entry) and f.get("fixture_id"):
                if not any(str(x.get("fixture_id")) == str(f.get("fixture_id")) for x in fixtures):
                    fixtures.append(f)
    if not fixtures:
        return {"status": "fixture_not_found", "reason": "Partita non trovata nell'archivio pubblico MoviOdds per la data dell'allarme."}

    # Prefer fixture whose final score agrees with the stored result, where supplied.
    final = entry.get("finale") or []
    def final_distance(f):
        m = re.match(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str(f.get("final_score", "")))
        if not m or len(final) != 2:
            return 99
        fs = [int(m.group(1)), int(m.group(2))]
        return 0 if fs == final else (1 if fs == final[::-1] else 10)
    fixtures.sort(key=final_distance)
    # Keep the probe bounded: try at most two likely fixture IDs per match.
    fixtures = fixtures[:2]
    attempts = []
    for fixture in fixtures:
        fid = str(fixture["fixture_id"])
        try:
            # Fetch a narrow window around the alert rather than a large full-match history.
            payload = get_json("/history", {
                "fi": fid,
                "from": iso(alert_utc - timedelta(minutes=12)),
                "to": iso(alert_utc + timedelta(minutes=3)),
                "limit": "10000"
            }, timeout=15)
            points = payload.get("points", [])
            candidates = []
            for p in points:
                group = str(p.get("group_name", "")).lower()
                side = str(p.get("side", "")).strip().lower()
                line = line_value(p.get("line"))
                price = line_value(p.get("odds_dec"))
                if line is None or price is None:
                    continue
                is_over = side in ("o", "over") or "over" in str(p.get("label", "")).lower()
                is_goal_market = ("goal" in group and ("line" in group or "over" in group or "total" in group))
                if not is_over or not is_goal_market or abs(line - target_line) > 0.001:
                    continue
                try:
                    ts = datetime.fromisoformat(str(p["ts"]).replace("Z", "+00:00"))
                    if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                    delta = (ts.astimezone(timezone.utc) - alert_utc).total_seconds()
                except Exception:
                    continue
                if delta > 180 or delta < -720:
                    continue
                candidates.append((abs(delta), delta, p, ts))
            if candidates:
                candidates.sort(key=lambda x: (x[0], x[1] > 0))
                _, delta, p, ts = candidates[0]
                attempts.append({
                    "status": "verified_historical_live_quote",
                    "provider": "MoviOdds public archive",
                    "bookmaker": "bet365",
                    "fixture_id": fid,
                    "quote": float(p["odds_dec"]),
                    "line": target_line,
                    "quote_side": p.get("side"),
                    "quote_market": p.get("group_name"),
                    "quote_timestamp_utc": iso(ts),
                    "seconds_from_alert": int(delta),
                    "quote_suspended": bool(p.get("suspended", False)),
                    "verification": "Fixture and archived in-play Over line matched; nearest recorded price within -12/+3 minutes of alert.",
                    "score_validation": "The API history does not attach the exact home-away score to every price tick; the target line is derived from the alert score."
                })
            else:
                attempts.append({
                    "status": "no_matching_quote_in_window",
                    "fixture_id": fid,
                    "history_points_in_window": len(points),
                    "reason": "Archivio trovato, ma nessuna quota Over della linea richiesta è stata registrata nella finestra temporale."
                })
        except Exception as ex:
            attempts.append({"status": "history_error", "fixture_id": fid, "reason": type(ex).__name__ + ": " + str(ex)[:220]})
    verified = [x for x in attempts if x.get("status") == "verified_historical_live_quote"]
    if verified:
        verified.sort(key=lambda x: abs(x.get("seconds_from_alert", 999999)))
        return verified[0]
    return {"status": "no_verified_quote", "reason": "Nessuna quota storica verificabile trovata.", "attempts": attempts}

def main():
    data = json.loads(SAVED.read_text(encoding="utf-8"))
    def stamp(e):
        try: return datetime.strptime(e.get("ora", ""), "%d/%m/%Y %H:%M")
        except (ValueError, TypeError): return datetime.min
    matches = [(mid, e) for mid, e in data.items()
               if isinstance(e.get("finale"), list) and isinstance(e.get("gol"), list)]
    matches.sort(key=lambda x: stamp(x[1]), reverse=True)
    matches = matches[:4]
    report = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": "Ricerca gratuita di quote storiche live Over; nessuna quota viene inventata.",
        "source": "MoviOdds public API, historical bet365 odds archive",
        "checked_matches": len(matches), "results": []
    }
    result_cache = {}
    for mid, e in matches:
        score = e.get("gol") or [0, 0]
        item = {
            "fotmob_id": mid, "match": e.get("casa", "") + " - " + e.get("ospite", ""),
            "date_alert": e.get("ora"), "alert_minute": e.get("min"),
            "score_at_alert": score, "final_score": e.get("finale"),
            "target_over_line": sum(score) + 0.5, "verified_live_quote": None
        }
        try:
            found = moviodds_lookup(e, result_cache)
            item["lookup"] = found
            if found.get("status") == "verified_historical_live_quote":
                item["verified_live_quote"] = found.get("quote")
        except Exception as ex:
            item["lookup"] = {"status": "source_error", "reason": type(ex).__name__ + ": " + str(ex)[:250]}
        report["results"].append(item)
        print(item["match"], "=>", item.get("lookup", {}).get("status"))
        time.sleep(0.1)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Partite controllate:", len(matches))
    print("Quote live storiche verificate:", sum(x["verified_live_quote"] is not None for x in report["results"]))
    print("Report scritto in odds_probe.json.")

if __name__ == "__main__":
    main()
