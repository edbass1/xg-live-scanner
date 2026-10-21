import json, os, sys, urllib.request, urllib.parse

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")
if not TOKEN or not CHAT:
    print("Telegram non configurato, salto."); sys.exit(0)

def load(p):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

old, new = load("/tmp/saved_old.json"), load("saved.json")
added = [k for k in new if k not in old]
if not old:
    print("Nessuno storico precedente, salto per evitare spam."); sys.exit(0)

f = lambda x: f"{float(x):.2f}".replace(".", ",")
for k in added:
    e = new[k]
    txt = (f"⚽ Nuova partita xG Live Scanner\n"
           f"{e['casa']} - {e['ospite']}\n{e['lega']}\n"
           f"Minuto {e['min']}' · Risultato {e['gol'][0]}-{e['gol'][1]}\n"
           f"xG {f(e['xg'][0])} - {f(e['xg'][1])} · Tiri in porta {int(e['sot'])}/{int(e['tiri'])}\n"
           f"Prob. gol {e['p']*100:.1f}% · Entra da quota ≥ {f(e['min_odd'])}\n"
           f"Affidabilità {e.get('rel','')}\n{e['url']}")
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": txt, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=15)
        print("Inviata:", k)
    except Exception as ex:
        print("Errore invio", k, ex)
