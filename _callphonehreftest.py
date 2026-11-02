"""Offline regression checks for Call Mode phone-link normalization."""
from pathlib import Path
import subprocess


ROOT = Path(__file__).parent
SOURCE = (ROOT / "call_mode.py").read_text()

assert "function phoneDigits(d)" in SOURCE
assert "function smsHref(d)" in SOURCE

js = r"""
var DIALER='tel';
function phoneDigits(d){
  d=String(d==null?'':d).replace(/\D/g,'');
  if(d.length===11 && d.charAt(0)==='1') d=d.slice(1);
  return d;
}
function dialHref(d){
  d=phoneDigits(d); if(d.length!==10) return '';
  return DIALER==='gv' ? 'https://voice.google.com/u/0/calls?a=nc,%2B1'+d : 'tel:+1'+d;
}
function smsHref(d){
  d=phoneDigits(d); return d.length===10 ? 'sms:+1'+d : '';
}
function eq(actual, expected, label) {
  if (actual !== expected) throw new Error(label + ': ' + actual + ' != ' + expected);
}
eq(dialHref('19545550101'), 'tel:+19545550101', 'leading 1 dial');
eq(dialHref('(954) 555-0101'), 'tel:+19545550101', 'formatted dial');
eq(smsHref('1-954-555-0101'), 'sms:+19545550101', 'formatted sms');
eq(dialHref('954555010'), '', 'short dial rejected');
eq(smsHref('not a number'), '', 'invalid sms rejected');
console.log('ALL PASS');
"""

node = "/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node"
result = subprocess.run([node, "-e", js], check=False, capture_output=True, text=True)
print(result.stdout, end="")
if result.returncode:
    raise SystemExit(result.stderr or result.returncode)
