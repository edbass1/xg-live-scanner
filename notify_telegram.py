import json, os, sys, urllib.request, urllib.parse

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")

def load(p):
    try:
        with open(p, encoding="utf-8") as fh: return json.load(fh)
    except Exception: return {}

f = lambda x: f"{float(x):.2f}".replace(".", ",")

def analisi(e):
    mn = int(e["min"]); a, b = e["gol"]; hx, ax = e["xg"]
    sot, sh, cor, p, rel = int(e["sot"]), int(e["tiri"]), int(e.get("corner") or 0), e["p"], e.get("rel", "")
    xm, xt, gt = max(hx, ax), hx + ax, a + b
    best = e["casa"] if hx >= ax else e["ospite"]
    tx, ts = 1.4 / 60 * mn, 5 / 60 * mn
    pro, contro = [], []
    if xm >= tx: pro.append(f"{best} ha {f(xm)} xG, sopra la soglia di {f(tx)} per il {mn}'")
    if sot >= ts: pro.append(f"{sot} tiri in porta, sopra la soglia di {ts:.0f} per il minuto")
    if xt - gt >= 0.5: pro.append(f"xG totali {f(xt)} contro {gt} gol segnati: la partita ha creato più di quanto ha raccolto")
    if sh >= mn * 12 / 45: pro.append(f"ritmo alto, {sh} tiri totali")
    if cor >= mn / 9 and cor >= 5: pro.append(f"{cor} calci d'angolo, pressione costante")
    if p >= 0.85: pro.append(f"il modello stima {p*100:.0f}% di probabilità di almeno un altro gol")
    if rel == "ALTA": pro.append("situazione molto simile alle partite su cui il modello è tarato (0-0, minuto centrale)")
    if abs(a - b) >= 2: contro.append(f"una squadra è avanti di {abs(a-b)} gol e può abbassare il ritmo")
    if gt - xt >= 0.5: contro.append(f"già {gt} gol con solo {f(xt)} xG: si è segnato più del previsto")
    if min(hx, ax) < 0.4 and xt > 0: contro.append("pericoli quasi tutti da una sola squadra")
    if mn >= 70: contro.append(f"siamo al {mn}', resta poco tempo")
    if sh < mn * 12 / 45: contro.append(f"pochi tiri totali ({sh}) per il minuto")
    if sot < mn * 2 / 45: contro.append(f"pochi tiri in porta ({sot})")
    if rel == "BASSA": contro.append("affidabilità BASSA: situazione lontana dai dati su cui è tarato il modello")
    elif rel == "MEDIA" and gt > 0: contro.append("affidabilità MEDIA: il modello è tarato soprattutto su partite ancora 0-0")
    elif rel == "MEDIA": contro.append("affidabilità MEDIA: minuto ai margini della fascia migliore del modello")
    return pro[:4], contro[:3]

def testo(e):
    pro, contro = analisi(e)
    t = (f"⚽ XG PLUS INPLAY SCANNER\n"
         f"{e['casa']} - {e['ospite']}\n{e['lega']}\n"
         f"Minuto {e['min']}' · Risultato {e['gol'][0]}-{e['gol'][1]}\n"
         f"xG {f(e['xg'][0])} - {f(e['xg'][1])} · Tiri in porta {int(e['sot'])}/{int(e['tiri'])}\n"
         f"Prob. gol {e['p']*100:.1f}%\n"
         f"Affidabilità {e.get('rel','')}\n")
    t += "\n✅ Perché giocarla\n" + ("\n".join("• " + x for x in pro) if pro else "• nessun punto di forza evidente oltre al segnale dello scanner") + "\n"
    t += "\n⚠️ Contro\n" + ("\n".join("• " + x for x in contro) if contro else "• nessun contro evidente") + "\n"
    return t + f"\n{e['url']}"

def main():
    if not TOKEN or not CHAT:
        print("Telegram non configurato, salto."); return
    old, new = load("/tmp/saved_old.json"), load("saved.json")
    if not old:
        print("Nessuno storico precedente, salto per evitare spam."); return
    for k in [k for k in new if k not in old]:
        data = urllib.parse.urlencode({"chat_id": CHAT, "text": testo(new[k]), "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=15)
            print("Inviata:", k)
        except Exception as ex:
            print("Errore invio", k, ex)

if __name__ == "__main__": main()
