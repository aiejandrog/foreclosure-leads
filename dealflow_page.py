"""Render today.html — the DealFlow Today screen.

One page, three sections: Call / Mail / Knock. Phone-first, big tap targets, no build step,
no framework, no external requests. Reads the structure from dealflow_today.build_today().

Nothing here sends, mails, dials, prints or spends. It renders a list and writes one file.

Run:  python dealflow_page.py            -> today.html beside this script
      python dealflow_page.py --open     -> also open it in the default browser
"""

import html
import os
import sys
from datetime import date

import dealflow_today as T

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "today.html")

CSS = """
:root{--navy:#152747;--navy-deep:#0B1730;--gold:#F5C542;--ink:#0F172A;--mut:#64748B;
--line:#E2E8F0;--bg:#F6F8FB;--card:#FFFFFF;--red:#B42318;--amber:#B54708;--green:#067647}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
padding-bottom:80px}
header{position:sticky;top:0;z-index:10;background:linear-gradient(140deg,#0B1730 0%,#152747 52%,#203B6E 118%);
color:#fff;padding:14px 16px 12px;box-shadow:0 2px 10px rgba(11,23,48,.25)}
h1{margin:0 0 2px;font-size:17px;font-weight:700;letter-spacing:.2px}
.date{font-size:12px;color:#9FB3D1;margin-bottom:10px}
.counts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.cnt{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);border-radius:10px;
padding:8px 6px;text-align:center}
.cnt b{display:block;font-size:20px;line-height:1.1;color:var(--gold)}
.cnt span{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:#9FB3D1}
.cnt.od b{color:#FF8A80}
.tabs{display:flex;gap:6px;padding:12px 16px 4px;position:sticky;top:0;background:var(--bg);z-index:5}
.tab{flex:1;padding:11px 8px;border:1px solid var(--line);background:var(--card);border-radius:10px;
font-size:13px;font-weight:700;color:var(--mut);cursor:pointer;text-align:center}
.tab.on{background:var(--navy);color:#fff;border-color:var(--navy)}
.tab .n{display:inline-block;margin-left:5px;background:rgba(0,0,0,.08);border-radius:20px;
padding:1px 7px;font-size:11px}
.tab.on .n{background:rgba(255,255,255,.2)}
section{display:none;padding:8px 16px 40px}
section.on{display:block}
.grp{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;
color:var(--mut);margin:16px 0 8px}
.grp.od{color:var(--red)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;
margin-bottom:10px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.card.od{border-left:4px solid var(--red)}
.card.td{border-left:4px solid var(--amber)}
.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.nm{font-weight:700;font-size:15px;line-height:1.25}
.cs{font-size:11px;color:var(--mut);font-variant-numeric:tabular-nums;margin-top:1px}
.ad{font-size:12.5px;color:#334155;margin-top:6px}
.meta{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.pill{font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:20px;
background:#EEF2F7;color:#475569;border:1px solid var(--line)}
.pill.sale{background:#FEF3C7;color:#92400E;border-color:#FDE68A}
.pill.soon{background:#FEE4E2;color:#B42318;border-color:#FECDCA}
.pill.off{background:#F1F5F9;color:#94A3B8}
.pill.last{background:#ECFDF5;color:#067647;border-color:#A7F3D0}
.acts{display:flex;gap:8px;margin-top:11px}
.act{flex:1;padding:12px 8px;border-radius:10px;border:1px solid var(--line);background:#fff;
font-size:13px;font-weight:700;color:var(--navy);cursor:pointer;text-decoration:none;
text-align:center;display:block}
.act.pri{background:var(--navy);color:#fff;border-color:var(--navy)}
.act.gold{background:var(--gold);color:var(--navy-deep);border-color:var(--gold)}
.note{font-size:12px;color:#475569;margin-top:8px;padding-top:8px;border-top:1px dashed var(--line);
font-style:italic}
.empty{text-align:center;color:var(--mut);padding:50px 20px;font-size:14px}
.warn{background:#FFFBEB;border:1px solid #FDE68A;color:#92400E;border-radius:10px;
padding:10px 12px;font-size:12px;margin:12px 16px 0}
footer{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid var(--line);
padding:8px 16px;font-size:11px;color:var(--mut);display:flex;justify-content:space-between}
"""

JS = """
function tab(k, btn){
  document.querySelectorAll('section').forEach(s=>s.classList.toggle('on', s.id===k));
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('on', b===btn));
  window.scrollTo({top:0,behavior:'instant'});
}
"""


def esc(s):
    return html.escape(str(s or ""))


def _sale_pill(x):
    d = x.get("sale_days")
    if d is None:
        return ""
    if d < 0:
        return '<span class="pill off">sale passed</span>'
    cls = "soon" if d < 7 else "sale"
    return '<span class="pill %s">%d days to sale</span>' % (cls, d)


def card(x, channel, today):
    b = x["bucket"]
    cls = "card od" if b == "overdue" else ("card td" if b == "today" else "card")
    pills = []
    if not x["on_board"]:
        pills.append('<span class="pill off">not on board</span>')
    pills.append(_sale_pill(x))
    if x.get("status"):
        pills.append('<span class="pill">%s</span>' % esc(x["status"]))
    last = x.get("last")
    if last and last.get("d"):
        pills.append('<span class="pill last">last %s %s</span>'
                      % (esc(last["d"]), esc(last.get("ch") or "")))
    if b == "overdue" and x.get("due"):
        pills.append('<span class="pill soon">due %s</span>' % esc(x["due"]))

    acts = []
    if channel == "call":
        num = x["prep"]["number"]
        acts.append('<a class="act pri" href="tel:%s">Call %s</a>' % (esc(num), esc(num)))
        acts.append('<a class="act" href="sms:%s">Text</a>' % esc(num))
    elif channel == "letter":
        acts.append('<button class="act pri">Print %s</button>' % esc(x["prep"]["letter"]))
        acts.append('<button class="act">Mark mailed</button>')
    else:
        acts.append('<button class="act gold">Door sheet</button>')
        acts.append('<button class="act">Log outcome</button>')

    return """<div class="%s">
  <div class="row"><div>
    <div class="nm">%s</div>
    <div class="cs">%s</div>
  </div></div>
  <div class="ad">%s</div>
  <div class="meta">%s</div>
  %s
  %s
</div>""" % (
        cls, esc(x["name"] or "(no owner on file)"), esc(x["case"]),
        esc(x["addr"] or "(no address on this build)"),
        "".join(p for p in pills if p),
        '<div class="acts">%s</div>' % "".join(acts),
        '<div class="note">%s</div>' % esc(x["note"]) if x.get("note") else "",
    )


def section(items, channel, today):
    if not items:
        return '<div class="empty">Nothing here today.</div>'
    out = []
    for label, key, klass in (("Overdue", "overdue", "grp od"),
                              ("Due today", "today", "grp"),
                              ("New", "new", "grp")):
        grp = [x for x in items if x["bucket"] == key]
        if not grp:
            continue
        out.append('<div class="%s">%s &middot; %d</div>' % (klass, label, len(grp)))
        out.extend(card(x, channel, today) for x in grp)
    return "".join(out)


def render(t, today):
    h = t["header"]
    off = h.get("off_board", 0)
    warn = ""
    if off:
        warn = ('<div class="warn">%d work items are not on the 11-04 board build. '
                'They are listed anyway and flagged. Rebuild the board to fill in address and equity.'
                '</div>' % off)

    return """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Today &middot; %s</title>
<style>%s</style>
</head><body>
<header>
  <h1>Today</h1>
  <div class="date">%s &middot; %d board rows &middot; %d notes</div>
  <div class="counts">
    <div class="cnt"><b>%d</b><span>Calls</span></div>
    <div class="cnt"><b>%d</b><span>Letters</span></div>
    <div class="cnt"><b>%d</b><span>Doors</span></div>
    <div class="cnt od"><b>%d</b><span>Overdue</span></div>
  </div>
</header>
%s
<div class="tabs">
  <button class="tab on" onclick="tab('call',this)">Call<span class="n">%d</span></button>
  <button class="tab" onclick="tab('mail',this)">Mail<span class="n">%d</span></button>
  <button class="tab" onclick="tab('knock',this)">Knock<span class="n">%d</span></button>
</div>
<section id="call" class="on">%s</section>
<section id="mail">%s</section>
<section id="knock">%s</section>
<footer><span>DealFlow Today</span><span>read-only preview</span></footer>
<script>%s</script>
</body></html>""" % (
        esc(h["date"]), CSS, esc(h["date"]), h["board_rows"], h["notes"],
        h["calls"], h["letters"], h["doors"], h["overdue"],
        warn,
        h["calls"], h["letters"], h["doors"],
        section(t["call"], "call", today),
        section(t["mail"], "letter", today),
        section(t["knock"], "door", today),
        JS,
    )


def main(argv):
    today = date.today()
    t = T.build_today(today=today)
    page = render(t, today)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    h = t["header"]
    print("wrote %s (%d bytes)" % (OUT, len(page)))
    print("%d calls / %d letters / %d doors / %d overdue"
          % (h["calls"], h["letters"], h["doors"], h["overdue"]))
    if "--open" in argv:
        os.startfile(OUT)          # Windows
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))