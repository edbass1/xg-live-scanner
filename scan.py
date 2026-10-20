"""Scanner live FotMob -> data.json (modello xg_live_ogni_minuto)."""
from zoneinfo import ZoneInfo
import json, math, re, time, datetime, urllib.request
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
B = [-2.412608096676839, 1.005601849105514, 1.9986984621086132, 0.2625736292898376]; MED = 2.9770884993969213
def get(p):
    for i in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request("https://www.fotmob.com/api/data"+p, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=25))
        except Exception:
            if i == 2: raise
            time.sleep(3)
def mult(a, b):
    d = abs(a-b); return (1 if a+b == 0 else 1.05) if d == 0 else 1.08 if d == 1 else .95 if d == 2 else .85
def rate90(mn, xg, sot, mu, a, b):
    m = max(mn, 1)
    return math.exp(B[0]+B[1]*math.log(xg/m*90+.3)+B[2]*math.log(mu)+B[3]*math.log1p(sot/m*90))*mult(a, b)
def minute(st):
    lt = (st.get("liveTime") or {}).get("short", "")
    if lt.upper() in ("HT", "PAUSE"): return 45, "Intervallo"
    m = re.match(r"(\d+)(?:\+(\d+))?", lt)
    return (int(m.group(1))+int(m.group(2) or 0), lt) if m else (None, lt)
def stats(det):
    out = {}
    periods = (((det or {}).get("content") or {}).get("stats") or {}).get("Periods") or {}
    for g in (periods.get("All") or {}).get("stats") or []:
        for s in g.get("stats") or []:
            k, v = s.get("key"), s.get("stats")
            if k in ("expected_goals", "ShotsOnTarget", "total_shots", "corners") and v and len(v) >= 2 and v[0] is not None and v[1] is not None and k not in out:
                try: out[k] = [float(v[0]), float(v[1])]
                except Exception: pass
    return out
def evaluate(mn, a, b, hx, ax, sot, sh):
    mu = MED; R = max(1, 94-mn); lam = R/90*rate90(mn, hx+ax, sot, mu, a, b); P = 1-math.exp(-lam)
    xm = max(hx, ax); tx = 1.4/60*mn; ts = 5/60*mn
    w = []
    if xm < mn/45: w.append(f"xG squadra migliore sotto {mn/45:.2f}")
    if sh < mn*12/45: w.append(f"tiri totali sotto {mn*12/45:.0f}")
    if sot < mn*2/45: w.append(f"meno di {mn*2/45:.1f} tiri in porta")
    if mn < 30: w.append("prima del 30': xG instabile")
    if mn >= 85: w.append("ultimi minuti: conta il recupero")
    ok = (xm >= tx) + (sot >= ts)
    v = "GIOCABILE" if ok == 2 and not w else "AL LIMITE" if ok >= 1 and len(w) <= 1 else "NO VALORE"
    z = a+b == 0
    rel = "ALTA" if z and 40 <= mn <= 65 else "MEDIA" if (z and 30 <= mn <= 75) or (not z and 40 <= mn <= 65 and abs(a-b) <= 1) else "BASSA"
    return {"p": round(P, 4), "fair": round(1/P, 2), "min_odd": round(1.05/P, 2), "lam": round(lam, 2), "verdict": v,
            "rel": rel, "warn": w, "thr_xg": round(tx, 2), "thr_sot": round(ts, 1), "xm": xm, "line": f"Over {a+b}.5"}
def is_womens_match(league, match):
    names = " ".join([str(league or ""), str((match.get("home") or {}).get("name") or ""), str((match.get("away") or {}).get("name") or "")]).lower()
    return bool(re.search(r"\\b(women|woman|femminil|femenin|femenina|femenino|nwsl|wsl|女子|女足|dames|frauen|damallsvenskan)\\b|\\(w\\)|liga f\\b", names, re.I))
def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    seen, out, noxg, status = set(), [], 0, {}
    try: saved = json.load(open("saved.json"))
    except Exception: saved = {}
    for day in {now.strftime("%Y%m%d"), (now-datetime.timedelta(hours=3)).strftime("%Y%m%d")}:
        try: d = get(f"/matches?date={day}&timezone=Europe%2FRome")
        except Exception as e: print("matches", day, e); continue
        for lg in d.get("leagues", []):
            for m in lg.get("matches", []):
                st = m.get("status") or {}; status[str(m["id"])] = st
                if m["id"] in seen or not st.get("started") or st.get("finished") or st.get("cancelled"): continue
                seen.add(m["id"]); mn, lt = minute(st)
                if mn is None or mn < 45 or mn >= 81 or is_womens_match(lg.get('name'), m): continue
                try: a, b = [int(x) for x in st.get("scoreStr", "0 - 0").split(" - ")]
                except Exception: a = b = 0
                try:
                  row = {"id": m["id"], "lega": lg.get("name"), "casa": m["home"]["name"], "ospite": m["away"]["name"],
                         "min": mn, "tempo": lt, "gol": [a, b], "url": "https://www.fotmob.com/match/"+str(m["id"])}
                  try: s = stats(get(f"/matchDetails?matchId={m['id']}"))
                  except Exception as e: print("det", m["id"], e); continue
                  if "expected_goals" not in s: noxg += 1; continue
                  (hx, ax), sot, sh = s["expected_goals"], sum(s.get("ShotsOnTarget", [0, 0])), sum(s.get("total_shots", [0, 0]))
                  row.update({"xg": [hx, ax], "sot": sot, "tiri": sh, "corner": sum(s.get("corners", [0, 0]))})
                  row.update(evaluate(mn, a, b, hx, ax, sot, sh)); out.append(row); time.sleep(.4)
                except Exception as e: print("partita saltata", m.get("id"), type(e).__name__, e); continue
    rome = now.astimezone(ZoneInfo("Europe/Rome")).strftime("%d/%m/%Y %H:%M")
    for r in out:
        k = str(r["id"])
        if r["verdict"] == "GIOCABILE" and k not in saved:
            saved[k] = {"ora": rome, "lega": r["lega"], "casa": r["casa"], "ospite": r["ospite"], "min": r["min"], "gol": r["gol"],
                        "xg": r["xg"], "sot": r["sot"], "tiri": r["tiri"], "corner": r["corner"], "p": r["p"], "min_odd": r["min_odd"],
                        "rel": r["rel"], "url": r["url"], "finale": None, "esito": None}
    for k, e in saved.items():
        st = status.get(k)
        if e["finale"] is None and st and st.get("finished"):
            try: fa, fb = [int(x) for x in st.get("scoreStr", "").split(" - ")]
            except Exception: continue
            e["finale"] = [fa, fb]; e["esito"] = "SÌ" if fa+fb > sum(e["gol"]) else "NO"
    json.dump(saved, open("saved.json", "w"), ensure_ascii=False, indent=1)
    out = [r for r in out if r["verdict"] == "GIOCABILE"]
    order = {"GIOCABILE": 0, "AL LIMITE": 1, "NO VALORE": 2}
    out.sort(key=lambda r: (order[r["verdict"]], -r["p"]))
    json.dump({"updated": now.isoformat(timespec="seconds"), "live_tot": len(seen), "senza_xg": noxg, "matches": out},
              open("data.json", "w"), ensure_ascii=False, indent=1)
    print(f"live {len(seen)}, con xG {len(out)}, senza xG {noxg}")
if __name__ == "__main__": main()
