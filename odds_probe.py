#!/usr/bin/env python3
"""Probe public SoccerPunter and other public pages for verifiable historical in-play goal-line odds."""
import json, re, time, html, unicodedata
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs, unquote
from urllib.request import Request, urlopen
from html.parser import HTMLParser
from datetime import datetime

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
ROOT = Path(__file__).resolve().parent
SAVED = ROOT / "saved.json"
OUT = ROOT / "odds_probe.json"
SOURCES = ("soccerpunter.com", "oddsdigger.com", "aiscore.com", "matchatlas.com")

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
    report={"generated_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
            "purpose":"ricerca di pagine pubbliche; nessuna quota viene verificata senza riscontro esplicito di linea, punteggio e minuto",
            "search_method":"SoccerPunter live-odds first, then public search fallbacks",
            "checked_matches":len(matches),"results":[]}
    for mid,e in matches:
        home=e.get("casa",""); away=e.get("ospite","")
        score=e.get("gol") or [0,0]
        target_line=sum(score)+0.5
        base='"'+home+'" "'+away+'"'
        queries=[
            'site:soccerpunter.com/live-odds/ '+base,
            'site:soccerpunter.com/live-odds/ "'+home+'" "'+away+'"',
            'site:soccerpunter.com/live-odds/ "'+away+'" "'+home+'"',
            'site:soccerpunter.com '+base+' odds history',
            'site:oddsdigger.com '+base+' over under odds',
            'site:aiscore.com '+base+' odds'
        ]
        candidates=[]; errors=[]
        for q in queries:
            found,err=search(q)
            if err: errors.append(err)
            for c in found:
                if not any(x["url"]==c["url"] for x in candidates): candidates.append(c)
            if len(candidates)>=8: break
        result={"fotmob_id":mid,"match":home+" - "+away,"date_alert":e.get("ora"),
                "alert_minute":e.get("min"),"score_at_alert":score,"final_score":e.get("finale"),
                "target_over_line":target_line,"pages":[],"verified_live_quote":None}
        for c in candidates[:8]:
            page=inspect_page(c["url"])
            page["search_title"]=c["title"]
            result["pages"].append(page)
            time.sleep(0.15)
        if errors and not candidates: result["search_error"]="; ".join(dict.fromkeys(errors))[:500]
        result["note"]="Le pagine trovate sono solo candidate: la quota resta non verificata finché la pagina pubblica non espone una riga che corrisponde a linea Over, punteggio e minuto dell'allarme."
        report["results"].append(result); time.sleep(0.2)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Partite controllate:",len(matches))
    print("Pagine pubbliche candidate:",sum(len(x["pages"]) for x in report["results"]))
    print("Quote live storiche verificate:",sum(x["verified_live_quote"] is not None for x in report["results"]))
    print("Nessuna API a pagamento o token esterno utilizzato.")
    print("Report scritto in odds_probe.json.")

if __name__=="__main__": main()
