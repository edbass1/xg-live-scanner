#!/usr/bin/env python3
"""Discover public odds pages and, when configured, retrieve TotalCorner historical live goal-line odds."""
import json, re, time, html, os, unicodedata
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs, unquote
from urllib.request import Request, urlopen
from html.parser import HTMLParser
from datetime import datetime

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
ROOT = Path(__file__).resolve().parent
SAVED = ROOT / "saved.json"
OUT = ROOT / "odds_probe.json"
TOKEN = os.environ.get("TOTALCORNER_TOKEN", "").strip()
SOURCES = ("totalcorner.com", "soccerpunter.com", "oddsdigger.com", "aiscore.com", "matchatlas.com")

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]
    def handle_data(self, data):
        if data.strip(): self.parts.append(data.strip())

class Anchors(HTMLParser):
    def __init__(self):
        super().__init__(); self.items=[]
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            a=dict(attrs)
            if a.get("href"): self.items.append((a["href"], a.get("class","")))

def get(url, timeout=18, json_mode=False):
    req=Request(url, headers={"User-Agent": UA, "Accept-Language":"it-IT,it;q=0.9,en;q=0.8", "Accept":"application/json,text/html,application/xhtml+xml,*/*"})
    with urlopen(req, timeout=timeout) as r:
        body=r.read(3_000_000).decode("utf-8", "replace")
        return r.status, r.geturl(), (json.loads(body) if json_mode else body)

def tc_api(path, params=None):
    from urllib.parse import urlencode
    p=dict(params or {}); p["token"]=TOKEN
    _, _, data=get("https://api.totalcorner.com/v1/"+path+"?"+urlencode(p), timeout=18, json_mode=True)
    if not data.get("success"):
        err=data.get("error") or {}
        raise RuntimeError("TotalCorner API: "+str(err.get("code","errore"))+" "+str(err.get("message","")))
    return data.get("data", [])

def norm(s):
    s=unicodedata.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode().lower()
    s=re.sub(r"\b(fc|cf|sc|afc|club|women|w)\b"," ",s)
    return re.sub(r"[^a-z0-9]+","",s)

def team_match(a,b):
    a,b=norm(a),norm(b)
    return bool(a and b and (a==b or a in b or b in a))

def score_match(item, e):
    home=item.get("h") or item.get("home") or ""
    away=item.get("a") or item.get("away") or ""
    return team_match(home,e.get("casa")) and team_match(away,e.get("ospite"))

def totalcorner_lookup(e, schedule_cache):
    """Returns only a row backed by a matching fixture, goal line, score and nearby match minute."""
    dt=datetime.strptime(e["ora"], "%d/%m/%Y %H:%M")
    date_key=dt.strftime("%Y%m%d")
    if date_key not in schedule_cache:
        all_matches=[]
        for page in range(1,5):
            chunk=tc_api("match/schedule", {"date":date_key,"page":page,"columns":"events,goalLine"})
            if isinstance(chunk,dict):
                chunk=chunk.get("matches",[]) or []
            all_matches.extend(chunk if isinstance(chunk,list) else [])
            if len(chunk)<30: break
        schedule_cache[date_key]=all_matches
    matches=[m for m in schedule_cache[date_key] if score_match(m,e)]
    if not matches:
        return {"status":"not_found_in_schedule","reason":"Partita non trovata nel calendario TotalCorner per la data dell'allarme."}
    # If duplicate fixture candidates exist, prefer one with the alert score.
    target_score=e.get("gol") or []
    def score_distance(m):
        try: return abs(int(m.get("hg",0))-int(target_score[0]))+abs(int(m.get("ag",0))-int(target_score[1]))
        except Exception: return 99
    matches.sort(key=score_distance)
    fixture=matches[0]
    match_id=str(fixture.get("id",""))
    if not match_id:
        return {"status":"no_match_id","reason":"La scheda trovata non contiene un ID TotalCorner."}
    odds=tc_api("match/odds/"+match_id, {"columns":"goalList"})
    if isinstance(odds,dict): odds=[odds]
    if not odds:
        return {"status":"no_odds_history","totalcorner_match_id":match_id,"reason":"Nessuna cronologia quote restituita dall'API."}
    record=odds[0] if isinstance(odds[0],dict) else {}
    rows=record.get("goal_list") or record.get("goalList") or []
    target_line=sum(target_score)+0.5
    alert_min=int(e.get("min") or 0)
    possible=[]
    for row in rows:
        # TotalCorner goalList rows: [minute, line, over, under, time, home score, away score, closed]
        if not isinstance(row,list) or len(row)<7: continue
        try:
            minute=int(str(row[0]).replace("'","")) if row[0] is not None else None
            line=float(row[1]); over=float(row[2]) if row[2] is not None else None
            hs=int(float(row[5])); aws=int(float(row[6]))
        except (ValueError,TypeError): continue
        if abs(line-target_line)>0.001 or over is None: continue
        if [hs,aws] != [int(target_score[0]),int(target_score[1])]: continue
        if minute is None or abs(minute-alert_min)>3: continue
        possible.append({"minute":minute,"line":line,"over_odds":over,"under_odds":float(row[3]) if row[3] is not None else None,
                         "timestamp":row[4],"score":[hs,aws],"closed":bool(row[7]) if len(row)>7 else False})
    if not possible:
        return {"status":"fixture_found_no_exact_quote","totalcorner_match_id":match_id,
                "reason":"Trovata la partita ma nessuna riga corrisponde a linea Over, punteggio e minuto dell'allarme."}
    possible.sort(key=lambda x:(abs(x["minute"]-alert_min),x["closed"]))
    q=possible[0]
    return {"status":"verified_historical_live_quote","totalcorner_match_id":match_id,
            "bookmaker":"Bet365 (TotalCorner Match Odds)","verified_live_quote":q["over_odds"],
            "quote_line":q["line"],"quote_minute":q["minute"],"quote_timestamp":q["timestamp"],
            "quote_score":q["score"],"under_quote":q["under_odds"],"market_closed":q["closed"],
            "verification":"Matched TotalCorner historical goal-line row by fixture, Over line, score and minute within ±3."}

def add_url(found, title, href):
    href=html.unescape(href or "").strip()
    if "uddg=" in href:
        href=parse_qs(urlparse(href).query).get("uddg",[href])[0]
    href=unquote(href)
    host=urlparse(href).netloc.lower().split(":")[0]
    if href.startswith("http") and any(host == d or host.endswith("."+d) for d in SOURCES):
        if not any(x["url"] == href for x in found):
            found.append({"title":html.unescape(re.sub(r"<[^>]+>","",title or "")).strip()[:180], "url":href})

def search(query):
    found=[]; errors=[]
    for engine, url in [
        ("bing-rss", "https://www.bing.com/search?format=rss&q="+quote(query)),
        ("ddg", "https://html.duckduckgo.com/html/?q="+quote(query))
    ]:
        try:
            _, _, body=get(url, timeout=14)
            if engine == "bing-rss":
                for item in re.findall(r"<item>(.*?)</item>", body, re.I|re.S):
                    title=re.search(r"<title>(.*?)</title>",item,re.I|re.S)
                    link=re.search(r"<link>(.*?)</link>",item,re.I|re.S)
                    if link: add_url(found, title.group(1) if title else "", link.group(1).strip())
            else:
                p=Anchors(); p.feed(body)
                for href, cls in p.items:
                    if "result__a" in cls: add_url(found, "", href)
            if found: break
        except Exception as e:
            errors.append(engine+" "+type(e).__name__+": "+str(e)[:120])
    return found[:10], ("; ".join(errors) if errors and not found else None)

def inspect_page(url):
    try:
        status, final, body=get(url)
        p=Text(); p.feed(body); txt=" ".join(p.parts); low=txt.lower()
        title=re.search(r"<title[^>]*>(.*?)</title>",body,re.I|re.S)
        title=html.unescape(re.sub(r"\s+"," ",title.group(1))).strip()[:180] if title else ""
        indicators={"over_under_market":bool(re.search(r"over.?/?.?under|total goals|totale gol|goal line|大小球",low)),
                    "inplay_words":bool(re.search(r"in.play|inplay|live odds|quote live|live-odds|即时",low)),
                    "history_words":bool(re.search(r"odds history|odds movement|fluctuation|fluttuazione|historical odds|cronologia",low)),
                    "minute_markers":bool(re.search(r"\b(45|46|47|48|49|50|55|60|65|70|75|80|85|90)['’′]\b",txt))}
        return {"url":final,"http":status,"title":title,"indicators":indicators,"excerpt":txt[:850]}
    except Exception as e: return {"url":url,"error":type(e).__name__+": "+str(e)[:180]}

def main():
    data=json.loads(SAVED.read_text(encoding="utf-8"))
    def stamp(e):
        try: return datetime.strptime(e.get("ora",""), "%d/%m/%Y %H:%M")
        except (ValueError,TypeError): return datetime.min
    matches=[(mid,e) for mid,e in data.items() if isinstance(e.get("finale"),list) and isinstance(e.get("gol"),list)]
    matches.sort(key=lambda x:stamp(x[1]),reverse=True); matches=matches[:12]
    report={"generated_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"checked_matches":len(matches),
            "method":"TotalCorner historical API when TOTALCORNER_TOKEN is configured; otherwise public page discovery only.",
            "totalcorner_api_configured":bool(TOKEN),"results":[]}
    schedule_cache={}
    for mid,e in matches:
        result={"fotmob_id":mid,"match":e.get("casa","")+" - "+e.get("ospite",""),"date_alert":e.get("ora"),
                "alert_minute":e.get("min"),"score_at_alert":e.get("gol"),"final_score":e.get("finale"),
                "target_over_line":sum(e.get("gol",[0,0]))+0.5,"pages":[],"verified_live_quote":None}
        if TOKEN:
            try:
                tc=totalcorner_lookup(e,schedule_cache); result["totalcorner"]=tc
                if tc.get("status")=="verified_historical_live_quote": result["verified_live_quote"]=tc["verified_live_quote"]
            except Exception as ex:
                result["totalcorner"]={"status":"api_error","reason":type(ex).__name__+": "+str(ex)[:220]}
        if not TOKEN or (result.get("totalcorner",{}).get("status") not in ("verified_historical_live_quote",)):
            home=e.get("casa",""); away=e.get("ospite",""); base='"'+home+'" "'+away+'"'
            queries=[base+" football odds",base+" site:totalcorner.com/odds",base+" site:soccerpunter.com",
                     base+" site:oddsdigger.com/it/football",base+" site:aiscore.com"]
            candidates=[]; errors=[]
            for q in queries:
                found,err=search(q)
                if err: errors.append(err)
                for c in found:
                    if not any(x["url"]==c["url"] for x in candidates): candidates.append(c)
                if len(candidates)>=5: break
            for c in candidates[:5]:
                result["pages"].append({"search_title":c["title"],**inspect_page(c["url"])})
                time.sleep(0.2)
            if errors and not candidates: result["search_error"]="; ".join(dict.fromkeys(errors))[:500]
        report["results"].append(result); time.sleep(0.25)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Partite controllate:",len(matches))
    print("API TotalCorner configurata:",bool(TOKEN))
    print("Quote live storiche verificate:",sum(x["verified_live_quote"] is not None for x in report["results"]))
    if not TOKEN: print("Per l'estrazione automatica delle quote storiche serve TOTALCORNER_TOKEN (API disponibile con membership VIP).")
    print("Report scritto in odds_probe.json; nessuna quota viene inventata.")

if __name__=="__main__": main()
