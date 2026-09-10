"""A lead we already reached out to must not come back on the dial list.

WHY THIS EXISTS
Alejandro, 2026-09-10: *"why am I still getting leads that have been contacted by me already?"*

The dial list could only ever see ONE channel. `lastCall()` opened with
`if((t.ch||'') !== 'call') return;`, so an email, a text, a letter and a door knock all suppressed
exactly nothing. At the last good backup **684 of 1069 leads had outreach and never a call touch** —
every one of them stayed dialable, and to the person holding the phone they are all "people I
already contacted". The BOARD had always suppressed on any channel (`_lastTouchMs`), so two surfaces
read one notes store and gave opposite answers.

THE TENSION THIS FILE EXISTS TO PIN DOWN
"Any touch hides it" cannot be applied flatly, or it destroys the thing it is meant to serve:

  * the "Emailed / replied" lane exists precisely to CALL the people we emailed. Suppressing them
    there empties the one lane built for the job. So a lane is exempt from the soft tier for its
    OWN channel — and ONLY its own: a TEXT still hides a lead in the email lane.
  * a CALL is different. Once you have actually spoken to someone, every lane must respect it,
    the email lane included, or the exemption becomes the new source of the same complaint.
  * an INBOUND reply is not our outreach. `{ch:'email', out:'OWNER REPLIED'}` marks that THEY wrote
    to US; counting it would bury every replied lead for three days on the strength of their reply.
  * a WORKER breadcrumb is triage, not a message to a human. The board deliberately keeps `worker`
    out of its own `_OUTREACH_CH` for this reason.

Each of those is silent when it breaks: the list simply looks shorter, or a lane looks empty, and
nothing anywhere says why.

Run: python _contactsuptest.py   (needs node for the JS half; skips cleanly without it)
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import call_mode  # noqa: E402

FAIL, PASS = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  — ' + str(detail)[:130]) if detail else ''))


src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()

# ---- 1. the source promises --------------------------------------------------------------------
print('\nSOURCE')
rec('the outreach vocabulary matches the board byte-for-byte',
    'var OUTREACH_CH = {call:1, text:1, email:1, letter:1, door:1};' in src)
rec("'worker' is NOT outreach (a triage breadcrumb is not a message)",
    'worker' not in src.split('var OUTREACH_CH = {')[1].split('}')[0])
rec('inbound replies are excluded', 'function _inbound(' in src and '/replied|inbound/i' in src)
rec('a reply re-opens the lead', 'function _replyOpen(' in src)
rec('suppression is three tiers', "k:'hard'" in src and "k:'cool'" in src and "k:'soft'" in src)
rec('the lane is a PARAMETER, not the module global', 'function supReason(r, laneK)' in src)
rec('the two channel lanes declare their channel',
    "ch:'email'" in src and "ch:'worker'" in src)
rec('one cooldown fallback, shared with the board', 'var COOL_DEFAULT_H = 72;' in src)
rec('an untagged dial no longer writes cooldownH',
    "if(typeof n.cooldownH !== 'number') n.cooldownH = 6;" not in src
    and 'var PENDING_HOLD_H = 6;' in src)
rec('the per-outcome table is untouched (a no-answer still returns tomorrow)',
    "('noanswer',  'No answer',                24, False)" in src)
rec('tier 3 has its own counter, never folded into _SUPN', 'var _SUPT = 0;' in src)
rec('buttons read the shared per-lane pass', 'var _LANEN = {};' in src
    and '_LANEN[L.k] || {raw:0, net:0}' in src)
rec('the page never opens on the email lane', "lane = _net('worker') ? 'worker' : 'soon';" in src)
rec('the prior-contact banner exists and is not gated on the cooldown', 'function priorBar(' in src)

# ---- 2. the JS half ------------------------------------------------------------------------------
print('\nBEHAVIOUR')
have_node = subprocess.run(['node', '--version'], capture_output=True).returncode == 0
if not have_node:
    rec('node available', True, 'node missing — behaviour assertions skipped')
else:
    html = call_mode.build_html([], 0, {'stub': 1}, '2026-01-01T00:00', 's', 'b', sync_js='')
    js = max(re.findall(r'<script[^>]*>(.*?)</script>', html, re.S), key=len)
    p = os.path.join(HERE, '_contactsup_check.js')
    open(p, 'w', encoding='utf-8').write(js)
    chk = subprocess.run(['node', '--check', p], capture_output=True, text=True)
    rec('generated page JS parses', chk.returncode == 0, (chk.stderr or '')[:120])

    # Two slices, in dependency order. supReason/suppressed sit EARLIER in the file than the lane
    # table they call (function declarations hoist, so the page is fine); the harness has to put
    # them back in an order plain evaluation can follow.
    _a = js.find('var OUTREACH_CH')
    lanes = js[_a:js.find('TEAM SEATS', _a)].rsplit('/*', 1)[0]   # ... up to the TEAM SEATS banner
    blk = js[js.find('function supReason'):js.find('function dntSet(')]
    harness = '''
      var SEAT=null, SEAT_ALL=false, lane='soon', notes={}, ROWS=[], _SUPN=0, _SUPT=0, _LANEN={};
      var _OPTPH=null, _WQSET=null;
      function esc(s){return String(s==null?'':s);}
      function workerQ(){ return []; }
      function optPhones(){ return {}; }
      function nextLivePh(){ return 0; }
      function liveDays(r){ return (typeof r.d==='number' && r.d<9000) ? r.d : null; }
      function isBalloon(r){ return r.st==='BAL'; }
      function isBuyBox(r){ return !!r.bb; }
      function agoTxt(ms){ return Math.round((Date.now()-ms)/3600000)+'h ago'; }
      function hardSuppressed(r){ var n=notes[r.c]||{};
        if(n.wrongown) return 'wrong number reported';
        if(n.optout || n.status==='DO NOT CONTACT') return 'opted out';
        if(n.status==='Dead') return 'dead';
        return ''; }
      function lastCall(n){ var best=null;
        ((n&&n.touches)||[]).forEach(function(t){ if((t.ch||'')!=='call') return;
          var ts=+t.tsu||0; if(ts&&(!best||ts>best.ts)) best={ts:ts,out:t.out||'',by:t.by||''}; });
        ((n&&n.dials)||[]).forEach(function(d){ var ts=+d.tsu||0;
          if(ts&&(!best||ts>best.ts)) best={ts:ts,out:d.oc||'',by:d.by||''}; });
        return best; }
      var COOL_DEFAULT_H=72, PENDING_HOLD_H=6;
      %s
      %s
      var H=3600000, now=Date.now();
      function reset(){ notes={}; }
      function tier(c, laneK){ return supReason({c:c, d:20, p:['3055550101']}, laneK).k || ''; }
      var LANEKEYS = LANES.map(function(L){ return L.k; });
      function tiers(c){ var o={}; LANEKEYS.forEach(function(k){ o[k]=tier(c,k); }); return o; }
      var out={};

      reset(); notes['E']={touches:[{ch:'email', out:'emailed', tsu:now-2*H}]};
      out.email = tiers('E');

      reset(); notes['T']={touches:[{ch:'text', out:'Text sent', tsu:now-2*H}]};
      out.text = tiers('T');

      reset(); notes['C']={touches:[{ch:'call', out:'No answer', tsu:now-2*H}], cooldownH:24};
      out.call = tiers('C');

      reset(); notes['R']={touches:[{ch:'email', out:'OWNER REPLIED', tsu:now-2*H}]};
      out.inbound = tiers('R');

      reset(); notes['W']={touches:[{ch:'worker', out:'skipped', tsu:now-2*H}]};
      out.worker = tiers('W');

      /* They wrote back AFTER our last outbound: the cooldown's whole rationale is inverted, so the
         lead must be callable even though we called two hours before their reply landed. */
      reset(); notes['P']={replied:new Date(now-2*H).toISOString(),
                           touches:[{ch:'email', out:'emailed', tsu:now-9*H},
                                    {ch:'call', out:'No answer', tsu:now-4*H}], cooldownH:24};
      out.repliedOpen = tier('P','soon');
      notes['P'].optout = '2026-01-01';
      out.repliedStillHardBlocked = tier('P','soon');

      /* The converse: we already answered their reply. The cooldown applies again — otherwise a
         single reply would make a lead permanently immune to it. */
      reset(); notes['Q']={replied:new Date(now-4*H).toISOString(),
                           touches:[{ch:'call', out:'Talked', tsu:now-2*H}], cooldownH:72};
      out.repliedThenAnswered = tier('Q','soon');

      reset(); notes['N']={touches:[{ch:'call', out:'No answer', tsu:now-30*H}]};
      out.noCooldownField = tier('N','soon');          // 30h ago, no cooldownH -> 72h default hides

      reset(); notes['D']={dials:[{ts:'x', ph4:'0101', oc:'pending', tsu:now-2*H}]};
      out.pending2h = tier('D','soon');
      notes['D'].dials[0].tsu = now-7*H;
      out.pending7h = tier('D','soon');
      out.pendingWroteNoCooldown = (notes['D'].cooldownH === undefined);

      reset(); notes['S']={touches:[{ch:'email', out:'emailed', tsu:now-2*H}],
                           status:'Not interested'};
      out.softNoFloor = tier('S','soon');

      console.log(JSON.stringify(out));
    ''' % (lanes, blk)
    hp = os.path.join(HERE, '_contactsup_harness.js')
    open(hp, 'w', encoding='utf-8').write(harness)
    r2 = subprocess.run(['node', hp], capture_output=True, text=True)
    o = {}
    try:
        o = json.loads((r2.stdout or '').strip().splitlines()[-1])
    except Exception:
        pass
    rec('behaviour harness ran', bool(o), (r2.stderr or r2.stdout or 'no output')[:220])

    if o:
        DATE = ('urgent', 'soon', 'late', 'lp', 'bal', 'bb')
        em = o.get('email', {})
        rec('an email 2h ago hides the lead in every date lane',
            all(em.get(k) == 'soft' for k in DATE), em)
        rec('...but NOT in the Emailed lane, which exists to call them',
            em.get('email') == '', em.get('email'))
        rec('...and not in Worker either (different channel, own lane)',
            em.get('worker') == 'soft', em.get('worker'))

        tx = o.get('text', {})
        rec('a text hides the lead in the Emailed lane too — only a lane owns its OWN channel',
            tx.get('email') == 'soft', tx.get('email'))

        ca = o.get('call', {})
        rec('a call hides it in EVERY lane, email included',
            all(ca.get(k) == 'cool' for k in list(DATE) + ['email', 'worker']), ca)

        rec('an inbound reply suppresses nothing anywhere',
            not any(o.get('inbound', {}).values()), o.get('inbound'))
        rec('a worker triage breadcrumb suppresses nothing anywhere',
            not any(o.get('worker', {}).values()), o.get('worker'))

        rec('a lead who replied is re-opened despite a call inside its cooldown',
            o.get('repliedOpen') == '', o.get('repliedOpen'))
        rec('...but a reply never re-opens an opt-out',
            o.get('repliedStillHardBlocked') == 'hard', o.get('repliedStillHardBlocked'))
        rec('...and once we answer the reply the cooldown applies again',
            o.get('repliedThenAnswered') == 'cool', o.get('repliedThenAnswered'))

        rec('a call 30h ago with no stored cooldown is hidden (the 433-lead case)',
            o.get('noCooldownField') == 'cool', o.get('noCooldownField'))

        rec('an untagged dial holds the lead for 6h', o.get('pending2h') == 'cool',
            o.get('pending2h'))
        rec('...and releases it at 7h', o.get('pending7h') == '', o.get('pending7h'))
        rec('...and writes nothing to cooldownH', o.get('pendingWroteNoCooldown') is True)

        rec('a soft no holds other channels for 30 days too',
            o.get('softNoFloor') == 'soft', o.get('softNoFloor'))

for _s in ('_contactsup_check.js', '_contactsup_harness.js'):
    try:
        os.remove(os.path.join(HERE, _s))
    except OSError:
        pass

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
