"""Comment-only personal-value check for tracker_template.html (F-site-05, F-site-06, F-sec-08, I-05).

The board page ships to a PUBLIC repo. Code comments are not inside the encrypted payload, so a real
phone, owner name, case number, street address or mailbox typed into a comment is published in clear
text. CLAUDE.md "Personal identifiers never ship": no real personal value is ever a literal, a comment
included. Examples in comments use invented values (100 NW 1st Ave, you@example.com, 555 numbers,
Owner Example, 2099-* case ids).

This test reads tracker_template.html, extracts COMMENT text only (// , /* */ , <!-- -->), and checks:
  (a) no consumer mailbox (gmail yahoo outlook hotmail icloud aol live msn) and no bare mailbox
      local part ("name@" with no domain), except obvious placeholders (you@ user@ someone@ test@
      example.com);
  (b) no 10-digit phone shape except 555-exchange numbers, repeating or sequential digit runs, and
      the company's public lines imported from the constants that already hold them;
  (c) every case id shape carries the invented year: 99 (court-prefixed, e.g. XXXX-99-NNNNNN) or 2099;
  (d) no street-address shape except invented examples starting "100 NW 1" / "100 Example".

Two places are exempt, found by name and not by line number:
  * the comment block above `function _optedOutIdentities`: it is the owned suppression surface
    (CLAUDE.md "OWNED SURFACE: suppression"); reported to Alex as DECISIONS-NEEDED D-G (b).
  * comments inside the Playbook section `id="pb-intel"` (the Martin File): Alex's own content,
    DECISIONS-NEEDED D-G (c). (That paragraph is page text, not a comment, so today nothing in it is
    scanned either way.)

Output is counts and line numbers only. A found value is never printed.
Run: python _commentpiitest.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, 'tracker_template.html')
BS = chr(92)


def comments(lines):
    """[(lineno, comment_text)] for every line that carries comment text. Quote-aware; block
    comments (/* */ and <!-- -->) span lines; '//' directly after ':' is a URL, not a comment."""
    out = []
    block = None
    for i, s in enumerate(lines, 1):
        pos = 0
        txt = []
        while True:
            if block:
                e = s.find(block, pos)
                if e < 0:
                    txt.append(s[pos:])
                    break
                txt.append(s[pos:e])
                pos = e + len(block)
                block = None
                continue
            q = None
            j = pos
            found = None
            while j < len(s):
                c = s[j]
                if q:
                    if c == BS:
                        j += 2
                        continue
                    if c == q:
                        q = None
                elif c in '\'"`':
                    q = c
                elif s.startswith('/*', j):
                    found = ('/*', j)
                    break
                elif s.startswith('<!--', j):
                    found = ('<!--', j)
                    break
                elif s.startswith('//', j) and (j == 0 or s[j - 1] != ':'):
                    found = ('//', j)
                    break
                j += 1
            if not found:
                break
            k, j = found
            if k == '//':
                txt.append(s[j + 2:])
                break
            block = '*/' if k == '/*' else '-->'
            pos = j + len(k)
        if txt:
            out.append((i, ' '.join(txt)))
    return out


# ---------------------------------------------------------------------------------------------
# allow-lists
# ---------------------------------------------------------------------------------------------
def _company_lines():
    """Digits of the company's public lines, imported from the constants that already hold them."""
    got = set()
    for mod, attr in (('outreach_copy', 'PHONE'), ('bsg_cards', 'PHONE'), ('outreach_mail', 'CALLBACK_PHONE')):
        try:
            m = __import__(mod)
            v = re.sub(r'\D', '', str(getattr(m, attr, '')))
            if len(v) == 11 and v[0] == '1':
                v = v[1:]
            if len(v) == 10:
                got.add(v)
        except Exception:
            pass
    return got


COMPANY = _company_lines()
PLACEHOLDER_LOCALS = re.compile(r'(?:you|user|someone|test|name|email|owner|person|first\.last|firstlast|a|x)[0-9]*', re.I)
ADDR_OK_PREFIX = ('100 NW 1', '100 Example')

PHONE = re.compile(r'(?<![\d])(?:\+?1[\s.\-]?)?\(?(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})(?![\d])')
CONSUMER = re.compile(r'([\w.+\-]+)@(?:gmail|yahoo|outlook|hotmail|icloud|aol|live|msn)\.[a-z]{2,}', re.I)
BARE_LOCAL = re.compile(r'(?<![\w.])([A-Za-z][\w.+\-]{2,})@(?![\w])')
CASE_SHAPES = (
    # (regex, group number holding the year token, the invented value that token must equal)
    (re.compile(r'(?<![\w\-])[A-Z]{4}-(\d{2})-\d{4,7}(?![\w\-])'), 1, '99'),
    (re.compile(r'(?<![\w\-])(\d{4})-\d{6}-[A-Z]{2}-\d{2}(?![\w\-])'), 1, '2099'),
    (re.compile(r'(?<![\w\-])\d{2}(\d{4})[A-Z]{2}\d{6}'), 1, '2099'),
    (re.compile(r'(?<![\w\-])(\d{4})-\d{3,5}[A-Z]{2}(?![\w\-])'), 1, '2099'),
    (re.compile(r'(?<![\w\-])(\d{4})-\d{6}(?![\w\-])'), 1, '2099'),
)
ADDR_A = re.compile(r'\b\d{3,5}\s+(?:NW|NE|SW|SE|N|S|E|W)\.?\s+\d+(?:st|nd|rd|th)?\b', re.I)
ADDR_B = re.compile(r'\b\d{3,5}\s+[A-Za-z]+\s+(?:St|Ave|Blvd|Rd|Dr|Ln|Ct|Way|Ter|Pl|Street|Avenue|Road|Drive|Blvd)\b', re.I)


def _trivial_digits(d):
    if len(set(d)) <= 2:
        return True
    seq = '01234567890123456789'
    return d in seq or d[::-1] in seq or '1234567' in d or '7654321' in d


def check_comment(text):
    """Rule letters violated by one comment's text (a set of 'a'..'d')."""
    bad = set()
    for m in CONSUMER.finditer(text):
        if not PLACEHOLDER_LOCALS.fullmatch(m.group(1)):
            bad.add('a')
    for m in BARE_LOCAL.finditer(text):
        if not PLACEHOLDER_LOCALS.fullmatch(m.group(1)):
            bad.add('a')
    for m in PHONE.finditer(text):
        d = m.group(1) + m.group(2) + m.group(3)
        if m.group(2) == '555' or _trivial_digits(d) or d in COMPANY:
            continue
        bad.add('b')
    for rx, g, want in CASE_SHAPES:
        for m in rx.finditer(text):
            if m.group(g) != want:
                bad.add('c')
    for rx in (ADDR_A, ADDR_B):
        for m in rx.finditer(text):
            if not m.group(0).upper().startswith(tuple(x.upper() for x in ADDR_OK_PREFIX)):
                bad.add('d')
    return bad


def exempt_lines(lines, cm):
    """Line numbers exempt by name: the comment run above _optedOutIdentities, the pb-intel section."""
    skip = set()
    clines = {i for i, _ in cm}
    fn = next((i for i, l in enumerate(lines, 1) if l.startswith('function _optedOutIdentities')), None)
    if fn:
        j = fn - 1
        gap = 0
        while j > 0 and j not in clines and gap < 4:   # step over the `let _OOP` style code line(s)
            j -= 1
            gap += 1
        while j > 0 and j in clines:
            skip.add(j)
            j -= 1
    start = next((i for i, l in enumerate(lines, 1) if 'pb-intel' in l and '<section' in l), None)
    if start:
        end = next((i for i in range(start, len(lines) + 1) if '</section>' in lines[i - 1]), start)
        skip.update(range(start, end + 1))
    return skip


def scan(path):
    lines = open(path, encoding='utf-8').read().split('\n')
    cm = comments(lines)
    skip = exempt_lines(lines, cm)
    hits = {'a': [], 'b': [], 'c': [], 'd': []}
    for i, t in cm:
        if i in skip:
            continue
        for r in check_comment(t):
            hits[r].append(i)
    return len(cm), len(skip), hits


FAILS = []


def check(name, ok, detail=''):
    if ok:
        print('PASS ' + name)
    else:
        print('FAIL ' + name + (' :: ' + detail if detail else ''))
        FAILS.append(name)


def main():
    # negative / positive controls on invented strings (never a real value)
    check('control: invented 305 number + case id is rejected',
          {'b', 'c'} <= check_comment('// call 305 482 9170 about CACE-26-000123'))
    check('control: 555 number and 2099 / 99 case ids pass',
          not check_comment('// call (305) 555-0142 about 2099-000001-CA-01, CACE-99-000001, 502099CA000001, 2099-0001TD'))
    check('control: invented address passes, other address is rejected',
          not check_comment('// "100 NW 1st Ave Apt 2" -> "100 NW 1 AVE 2"')
          and 'd' in check_comment('// the house at 4821 SW 77 AVE'))
    check('control: consumer mailbox and bare local part are rejected, placeholders pass',
          'a' in check_comment('// mail ann.smith@gmail.com') and 'a' in check_comment('// includes jdoe77@ and')
          and not check_comment('// to you@example.com or user@ and someone@example.com'))
    check('control: company public line is allowed when the constant is importable',
          all(not check_comment('// call ' + c) for c in COMPANY))
    check('control: comment extractor ignores code and reads block + html comments',
          [i for i, _ in comments(['var a = "x // y";', '/* one', 'two */ var b;', '<!-- c -->', 'u = "http://a.b";'])] == [2, 3, 4])

    if not os.path.exists(TEMPLATE):
        check('tracker_template.html present', False)
    else:
        n, nskip, hits = scan(TEMPLATE)
        check('comments were found and the two named exemptions resolve', n > 1000 and nskip > 0,
              'comment lines %d, exempt lines %d' % (n, nskip))
        names = {'a': 'mailbox in a comment', 'b': 'non-555 phone number in a comment',
                 'c': 'case id without 2099/99 year in a comment', 'd': 'street address in a comment'}
        for r in 'abcd':
            check('(%s) no %s' % (r, names[r]), not hits[r],
                  '%d comment line(s): %s' % (len(hits[r]), ' '.join(map(str, hits[r]))))
    print('%d failed' % len(FAILS) if FAILS else 'ALL PASS')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
