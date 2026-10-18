#!/usr/bin/env python3
"""Probe public odds pages for saved matches; never invent or relabel prematch prices as live history."""
import json, re, time, html
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs
from urllib.request import Request, urlopen
from html.parser import HTMLParser

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
ROOT = Path(__file__).resolve().parent
SAVED = ROOT / "saved.json"
OUT = ROOT / "odds_probe.json"

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]
    def handle_data(self, data):
        if data.strip(): self.parts.append(data.strip())

def get(url, timeout=18):
    req=Request(url, headers={"User-Agent": UA, "Accept-Language":"it-IT,it;q=0.9,en;q=0.8"})
    with urlopen(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read(2_500_000).decode("utf-8", "replace")

def search(query):
    # DuckDuckGo HTML results are only used to discover public match pages.
    url="https://html.duckduckgo.com/html/?q="+quote(query)
    try:
        _, final, body=get(url)
        found=[]
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.I|re.S):
            href=html.unescape(m.group(1))
            title=re.sub("<.*?>","",m.group(2)); title=html.unescape(title)
            if "uddg=" in href:
                href=parse_qs(urlparse(href).query).get("uddg",[href])[0]
            if any(d in urlparse(href).netloc.lower() for d in ("totalcorner.com","soccerpunter.com","oddsdigger.com","aiscore.com")):
                found.append({"title":title[:180],"url":href})
        return found[:8], None
    except Exception as e:
        return [], type(e).__name__+": "+str(e)[:180]

def inspect_page(url):
    try:
        status, final, body=get(url)
        p=Text(); p.feed(body); txt=" ".join(p.parts)
        low=txt.lower()
        title=re.search(r"<title[^>]*>(.*?)</title>",body,re.I|re.S)
        title=html.unescape(re.sub(r"\s+"," ",title.group(1))).strip()[:180] if title else ""
        # These are indicators only; no price is accepted unless a historical in-play series can be verified.
        indicators={
            "over_under_market": bool(re.search(r"over.?/?.?under|total goals|totale gol|goal line|大小球",low)),
            "inplay_words": bool(re.search(r"in.play|inplay|live odds|quote live|live-odds|即时",low)),
            "history_words": bool(re.search(r"odds history|odds movement|fluctuation|fluttuazione|historical odds|cronologia",low)),
            "minute_markers": bool(re.search(r"\b(45|46|47|48|49|50|55|60|65|70|75|80|85|90)['’′]\b",txt)),
        }
        # Store only bounded text excerpts for debugging; never treat them as verified historical odds.
        return {"url":final,"http":status,"title":title,"indicators":indicators,"excerpt":txt[:850]}
    except Exception as e:
        return {"url":url,"error":type(e).__name__+": "+str(e)[:180]}

def main():
    data=json.loads(SAVED.read_text(encoding="utf-8"))
    def stamp(e):
        m=re.match(r"(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2})",e.get("ora",""))
        return tuple(map(int,m.groups()[::-1])) if m else (0,)
    # Prioritize the most recent completed alerts so the first pass is quick and useful.
    matches=[(mid,e) for mid,e in data.items() if isinstance(e.get("finale"),list) and isinstance(e.get("gol"),list)]
    matches.sort(key=lambda x: x[1].get("ora",""), reverse=True)
    matches=matches[:12]
    report={"generated_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
            "purpose":"source discovery only; no odds are marked verified unless exact in-play time/line/price are exposed",
            "checked_matches":len(matches),"results":[]}
    for mid,e in matches:
        q='"'+e.get("casa","")+'" "'+e.get("ospite","")+'" football odds'
        candidates, err=search(q)
        # Search each source separately if general discovery doesn't return a useful page.
        if not candidates:
            candidates, err2=search('"'+e.get("casa","")+'" "'+e.get("ospite","")+'" site:oddsdigger.com/it/football')
            err=err or err2
        pages=[]
        seen=set()
        for c in candidates:
            if c["url"] in seen: continue
            seen.add(c["url"])
            pages.append({"search_title":c["title"],**inspect_page(c["url"])})
            if len(pages)>=4: break
        report["results"].append({
            "fotmob_id":mid,"match":e.get("casa","")+" - "+e.get("ospite",""),
            "date_alert":e.get("ora"),"alert_minute":e.get("min"),"score_at_alert":e.get("gol"),
            "final_score":e.get("finale"),"target_over_line":str(sum(e.get("gol",[0,0]))+0.5),
            "search_error":err,"pages":pages,"verified_live_quote":None
        })
        time.sleep(1.2)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Analizzate",len(matches),"partite concluse; fonti candidate:",sum(len(x["pages"]) for x in report["results"]))
    print("Report scritto in odds_probe.json; nessuna quota viene inventata o scambiata per quota live.")

if __name__=="__main__":
    main()
