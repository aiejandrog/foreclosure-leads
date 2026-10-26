"""dealflow_nav - the ONE page strip every DealFlow surface carries.

Before 2026-10-24 the board reached Call Mode, Carlos's seat, the Morning Worker, the Balloon Book
and the Playbook through five buttons scattered inside its filter cluster, and Call Mode had no
way back to the board at all except the browser's back button. The pages were built by different
modules and each one grew its own idea of where the others were.

This module is the list. The board's header (tracker_template.html, <nav class="dfnav">) is static
HTML because its items ARE the board's own buttons (same ids the JS wires); _navtest.py checks its
labels and order against ITEMS. Call Mode bakes strip() at build time (call_mode.build_html),
with relative links so the mirror, the Desktop twin and a local file all resolve the same way,
and no absolute board address ever enters a page except through board_url.py.

ITEMS: (key, label). Keys name a page; 'current' is which one the strip is on.
"""

ITEMS = [
    ('board',    'Board'),
    ('call',     'Call Mode'),
    ('carlos',   'Carlos'),
    ('worker',   'Worker'),
    ('balloon',  'Balloon Book'),
    ('playbook', 'Playbook'),
]
LABELS = [label for _k, label in ITEMS]

# docs/ layout: the board at the root, Call Mode under call/, Carlos under call/carlos/, the
# balloon book under hm-balloon-q7v3n8/. Links are relative to the page carrying the strip.
BALLOON_DIR = 'hm-balloon-q7v3n8/'


def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def links(current, depth):
    """href for every item from a page `depth` folders below docs/ (board 0, call 1, carlos 2)."""
    up = '../' * depth
    hrefs = {
        'board':    up or './',
        'call':     up + 'call/',
        'carlos':   up + 'call/carlos/',
        # #worker=open, never #worker=morning: that hash is the Task Scheduler's auto-send deep link
        # and on the armed runner a nav tap would start the unattended run. =open is manual-only.
        'worker':   (up or './') + '#worker=open',
        'balloon':  up + BALLOON_DIR,
        'playbook': (up or './') + '#playbook',
    }
    return hrefs


def strip(current, depth, target_blank=('board', 'balloon', 'worker', 'playbook')):
    """The nav strip as HTML. `current` gets aria-current and no link; the rest are plain anchors
    (no JS), so the strip works before the page unlocks and on a page whose script failed."""
    hrefs = links(current, depth)
    out = ['<nav class="dfnav" aria-label="DealFlow pages">']
    for key, label in ITEMS:
        cls = 'dfn' + (' on' if key == current else '')
        if key == current:
            out.append('<a class="%s" href="#" aria-current="page" onclick="return false">%s</a>' % (cls, _esc(label)))
            continue
        tgt = ' target="_blank" rel="noopener"' if key in target_blank else ''
        out.append('<a class="%s" href="%s"%s>%s</a>' % (cls, _esc(hrefs[key]), tgt, _esc(label)))
    out.append('</nav>')
    return ''.join(out)


# Dark-theme CSS for the pages that are not the board (Call Mode is navy; the board's own strip is
# styled in tracker_template.html against its light palette). Same shape, same names.
# WRAPS, never scrolls sideways (2026-10-26). It was a one-line strip with overflow-x:auto and the
# scrollbar hidden: at phone width Balloon Book and Playbook sat past the right edge with nothing on
# screen saying the strip could be swiped, so on a phone those two pages read as missing.
CSS_DARK = (
    '.dfnav{display:flex;flex-wrap:wrap;gap:4px;align-items:center;padding:4px;border-radius:12px;'
    'margin:0 0 10px;background:#0f1d3a;border:1px solid #2a3f6b}'
    '.dfnav .dfn{flex:0 0 auto;display:inline-flex;align-items:center;min-height:38px;padding:7px 13px;'
    'border-radius:9px;border:1px solid transparent;color:#94a7c8;font-size:13.5px;font-weight:600;'
    'text-decoration:none;white-space:nowrap;line-height:1.2;touch-action:manipulation}'
    '.dfnav .dfn.on{background:#c6a14b;color:#0b1730;border-color:#c6a14b}'
    '.dfnav .dfn:not(.on):active{background:#132244}'
)


# Light-theme CSS for the white standalone pages (the Balloon Book). Same shape as CSS_DARK.
CSS_LIGHT = (
    '.dfnav{display:flex;flex-wrap:wrap;gap:4px;align-items:center;padding:4px;border-radius:12px;'
    'margin:0 0 16px;background:#f3f6fb;border:1px solid #dbe3ef}'
    '.dfnav .dfn{flex:0 0 auto;display:inline-flex;align-items:center;min-height:36px;padding:6px 13px;'
    'border-radius:9px;border:1px solid transparent;color:#33415c;font-size:13px;font-weight:600;'
    'text-decoration:none;white-space:nowrap;line-height:1.2}'
    '.dfnav .dfn.on{background:#0B1730;color:#fff;border-color:#0B1730}'
    '.dfnav .dfn:not(.on):hover{background:#fff;border-color:#dbe3ef}'
)


def board_nav_labels(template_html):
    """The labels inside the board template's <nav class="dfnav">, in order - what _navtest.py
    compares against ITEMS. Button/anchor text only; badges (<span>) are stripped."""
    import re
    m = re.search(r'<nav class="dfnav"[^>]*>(.*?)</nav>', template_html, re.S)
    if not m:
        return []
    labels = []
    for el in re.findall(r'<(?:a|button)\b[^>]*>(.*?)</(?:a|button)>', m.group(1), re.S):
        el = re.sub(r'<span\b.*?</span>', '', el, flags=re.S)
        el = re.sub(r'<svg\b.*?</svg>', '', el, flags=re.S)
        el = re.sub(r'<[^>]+>', '', el)
        labels.append(el.replace('&middot;', '·').strip())
    return labels
