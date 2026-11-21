"""_voiceidtest -- one-mic speaker ID by spectrum: a phone speaker has no high band, a live mouth does.

Run:  python _voiceidtest.py   (exit 0 = safe; the VOICE-ID CORE block runs in node, no audio, no mic)

WHY (2026-11-20, Alejandro: "figure out a brilliant way to determine who, through the speaker").
Their voice passed a telephony codec and a 2-inch speaker: energy above ~5 kHz is gone. His live
voice carries sibilants at 6-11 kHz. hb = share of spectral energy above 5 kHz, per utterance.

Pins:
  - physics guard: hb above the hard line is YOU even with zero calibration
  - uncalibrated and ambiguous: '' (the text guess stays boss)
  - after 3+ YOU samples (from script read-backs): a flat utterance labels THEM
  - with both sides learned, the cut is the geometric midpoint, with a hysteresis band that
    keeps the last verdict instead of flapping
  - a 'you' sample with no high band is refused (a mislabeled text line must not poison the
    fingerprint), and so is a 'them' sample above the hard line
  - vidUtter needs 3 voiced frames in the window; silence returns null and null labels ''
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


page = open(os.path.join(HERE, 'call_coach_live.html'), encoding='utf-8').read()
m = re.search(r'/\* ===== VOICE-ID CORE ===== \*/(.*?)/\* ===== /VOICE-ID CORE ===== \*/', page, re.S)
rec('the VOICE-ID CORE block is in the page, marked for extraction', m is not None)
rec('the mic meter starts the tap', 'vidTap(stream)' in page)
rec('pushFinal consults the audio verdict and teaches it', 'vidTag(text)' in page and "vidLearn(S.vid,spk,S.vidHB)" in page)
if not m:
    print('\n%d failure(s)' % len(FAILS))
    sys.exit(1)

JS = m.group(1) + r"""
var out = {};
var V = vidNew();
/* physics: a bright utterance is YOU before any calibration */
out.hardYou = vidLabel(V, 0.30);
out.coldAmbig = vidLabel(V, 0.12);
out.coldFlat = vidLabel(V, 0.02);
/* calibration from 4 read-backs around hb 0.30 */
[0.28, 0.31, 0.26, 0.33].forEach(function(h){ vidLearn(V, 'you', h); });
out.youN = V.you.length;
out.flatIsThem = vidLabel(V, 0.02);
vidLearn(V, 'them', 0.02); vidLearn(V, 'them', 0.03); vidLearn(V, 'them', 0.025);
/* geometric midpoint of 0.295 and 0.025 is ~0.086: above*1.2 you, below*0.8 them */
out.above = vidLabel(V, 0.15);
out.below = vidLabel(V, 0.05);
V.last = 'them';
out.gray = vidLabel(V, 0.086);
/* poisoned samples are refused */
var before = V.you.length; vidLearn(V, 'you', 0.01);
out.refuseYou = V.you.length === before;
var beforeT = V.them.length; vidLearn(V, 'them', 0.4);
out.refuseThem = V.them.length === beforeT;
/* frames and windows */
var W = vidNew();
vidFrame(W, 1000, 0.3); vidFrame(W, 1100, 0.28);
out.twoFrames = vidUtter(W, 900, 1200);
vidFrame(W, 1200, 0.32);
out.threeFrames = vidUtter(W, 900, 1300);
out.nullLabel = vidLabel(V, null);
/* the 30s ring drops old frames */
vidFrame(W, 40000, 0.1);
out.ring = W.fr.length;
console.log(JSON.stringify(out));
"""
r = subprocess.run(['node', '-e', JS], capture_output=True, text=True, timeout=30)
if r.returncode != 0:
    rec('core runs in node', False, r.stderr[:300])
    print('\n%d failure(s)' % len(FAILS))
    sys.exit(1)
o = json.loads(r.stdout)
rec('physics guard: a bright utterance is YOU with zero calibration', o['hardYou'] == 'you', o)
rec('uncalibrated + ambiguous stays blank (text guess remains boss)', o['coldAmbig'] == '' and o['coldFlat'] == '', o)
rec('four read-backs calibrate', o['youN'] == 4, o)
rec('a flat utterance then labels THEM', o['flatIsThem'] == 'them', o)
rec('with both sides learned: above the cut is YOU, below is THEM', o['above'] == 'you' and o['below'] == 'them', o)
rec('the gray zone keeps the last verdict instead of flapping', o['gray'] == 'them', o)
rec('a flat "you" sample is refused (no poisoning from a bad text label)', o['refuseYou'], o)
rec('a bright "them" sample is refused too', o['refuseThem'], o)
rec('an utterance needs 3 voiced frames; fewer is null', o['twoFrames'] is None and abs(o['threeFrames'] - 0.3) < 0.03, o)
rec('a null measurement labels nothing', o['nullLabel'] == '', o)
rec('the frame ring stays 30 seconds deep', o['ring'] == 1, o)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
