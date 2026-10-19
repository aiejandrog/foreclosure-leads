#!/usr/bin/env python
"""propstream_copy — the approved text and letter wording, ready to paste into PropStream.

WHY THIS EXISTS
PropStream's own templates carry none of the wording this business is required to send: the
attorney-approved opt-out sentence on every text, and the three MARS / Reg O sentences plus the
EN/ES opt-out line on every letter (CLAUDE.md, "Disclaimers" and "OWNED SURFACE: suppression").
This prints that wording from its single source (the board's own text bodies in
tracker_template.html, plus bsg_mail_campaign / disclaimer for the letter), never retyped, with placeholders where PropStream merges the owner's name and street. If the
approved wording changes there, rerun this; do not edit the output.

NOT COVERED: a no-street letter to the owner's mailing address, and mailing Broward / Palm Beach
before a bankruptcy search, are not attorney-approved yet (CLAUDE.md). Export for sending with
`python export_to_propstream.py --scope send`, and mail Miami-Dade first.

Run:  python propstream_copy.py            (prints, and writes <DEALFLOW_DIR>/exports/propstream-copy.txt)
"""
import json
import os
import subprocess
import sys
import tempfile

import bsg_mail_campaign as BM
import outreach_copy as OC

FIRST, STREET = '[First Name]', '[Property Street]'
HERE = os.path.dirname(os.path.abspath(__file__))
# (label, node options). No days: the bodies then say "soon" / "coming up" instead of a number that
# PropStream cannot merge. 'lp' = a case just filed, no sale date yet (never say "court sale" there).
TEXTS = (('1st text, sale date set', "tone:'cold'"),
         ('1st text, case just filed (no sale date)', "tone:'cold', lp:true"),
         ('follow-up', "tone:'followup'"),
         ('last text', "tone:'t3'"))


def board_texts():
    """The board's live text bodies, run through node from tracker_template.html: the same bytes
    Call Mode and the Morning Worker send (call_mode.extract_text_js). Not retyped."""
    import call_mode as CM
    src = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
    block = CM.extract_text_js(src)
    calls = ',\n'.join("  %s: _textBodies({first:%r, sender:%r, stEN:%r, stES:%r, %s})"
                       % (json.dumps(lbl), FIRST, OC.SIGNER, STREET, STREET, opts) for lbl, opts in TEXTS)
    js = block + '\nconsole.log(JSON.stringify({\n' + calls + '\n}));\n'
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, 'texts.js')
        open(f, 'w', encoding='utf-8').write(js)
        r = subprocess.run(['node', f], capture_output=True, text=True, encoding='utf-8')
    if r.returncode:
        raise SystemExit('node could not run the board text bodies: %s' % r.stderr[:300])
    out = json.loads(r.stdout)
    for lbl, b in out.items():
        # a text without the approved let-me-know line, in either language, must never be printed
        assert OC.SMS_OPTOUT_EN.strip() in b['en'] and OC.SMS_OPTOUT_ES.strip() in b['es'], lbl
        assert 'undefined' not in b['en'] + b['es'], lbl
    return out


def build():
    texts = board_texts()
    letter = BM.letter({'addr': STREET + ', MIAMI, FL', 'first': FIRST})
    assert OC.OPTOUT_LINE_EN in letter and OC.OPTOUT_LINE_ES in letter, 'letter lost its opt-out line'
    assert 'not associated with the government' in letter, 'letter lost its MARS sentences'
    lines = ['PROPSTREAM COPY SHEET  (generated from the board\'s text bodies and bsg_mail_campaign; do not edit)',
             '',
             'Replace [First Name] and [Property Street] with PropStream merge fields. Send nothing',
             'to a lead the board holds: export with --scope send. Texts go out 8 AM - 8 PM only.',
             '']
    for lbl, b in texts.items():
        lines += ['=== TEXT: %s ===' % lbl, 'EN: ' + b['en'], 'ES: ' + b['es'], '']
    lines += ['=== LETTER (handwritten / printed; MARS sentences and EN+ES opt-out line included) ===',
              letter, '',
              'AFTER A REPLY: a stop, "not a good time", "no", "wrong number" or any hard no on a PropStream',
              'text or letter must be marked Do Not Contact in Call Mode or the board so it reaches',
              'optouts.json. PropStream does not write to the ledger.', '']
    return '\n'.join(lines)


def main():
    out = build()
    print(out)
    import paths as P
    d = P.out('exports')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, 'propstream-copy.txt')
    open(p, 'w', encoding='utf-8').write(out)
    print('wrote ' + p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
