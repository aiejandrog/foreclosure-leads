"""_phonesrctest -- WHOSE number is on the row, and how many of them survive the build.

Run:  python _phonesrctest.py    (exit 0 = safe; no network, no browser, no board, no real data)

WHAT THIS GUARDS (2026-09-19, reported by Alejandro: a 561 number on the owner dial queue was
called and answered by the property's Compass listing agent).

  1. NUMBERS WERE BEING THROWN AWAY. skiptrace.py keeps every number a provider returns, and the
     board then cut to the first FOUR (foreclosure_leads, both merges) and the call sheet sliced to
     four again on render. The Broward/Palm Beach merge did its cut on the provider's RAW order, so
     a lead whose first four numbers were landlines lost its mobile at build time -- and the
     phone_rank pass downstream can only rank what is still in the array.

  2. EVERY NUMBER LOOKED LIKE THE OWNER'S. Whitepages household residents and Person-layer
     (name-only) matches are merged into the same bare-string r.phones array, and their `resident`
     / `source` tags were dropped on the way in. Once a number was in that array nothing -- not the
     row, not the call sheet, not Call Mode, not the text batch -- could tell it from the owner's
     own cell. r.phsrc is the parallel array that fixes it, and these checks are what stop it being
     dropped again the way phtype was.

  3. NOBODY LOOKED ACROSS LEADS. An office number (realtor, property manager, defense firm) traced
     onto twenty different owners' rows read as twenty homeowners' cells.

WHAT IS ASSERTED WHERE. The Python passes are real module-level functions and are called directly.
The JS gates are EXTRACTED BY NAME from tracker_template.html and run under node, so what runs is
the code that ships rather than a copy of it -- and a rename fails loudly here, by name, instead of
silently on the live board.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import phone_src as FL
import phone_rank as PR

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, 'tracker_template.html')

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)) if extra else ''))
    if not ok:
        fails.append(name)


def extract(src, name):
    """`function NAME(...){...}` by balanced-brace scan -- same trick _wplinktest.py uses."""
    i = src.find('function ' + name + '(')
    if i < 0:
        raise SystemExit('ANCHOR GONE: function %s() is no longer in tracker_template.html. It was '
                         'renamed or removed -- update this test with it.' % name)
    j = src.index('{', i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise SystemExit('unbalanced braces extracting ' + name)


def code_only(js):
    """Strip // comments before asserting on shape. The comments in this repo explain the very
    defects being asserted against, so they contain the exact strings a naive substring check is
    looking for -- 'NO slice(0,4) HERE' is not a slice(0,4)."""
    return re.sub(r'(?m)^\s*//.*$', '', js).replace(' ', '')


def node(js):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(js)
        p = f.name
    try:
        r = subprocess.run(['node', p], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise SystemExit('node failed:\n' + (r.stderr or '')[:2000])
        return json.loads(r.stdout)
    finally:
        os.unlink(p)


TPL_SRC = open(TPL, encoding='utf-8').read()

# ============================================================ 1. phone_rank knows whose number it is
OWNER_LAND = {'number': '3050000001', 'type': 'Land Line', 'carrier': 'AT&T', 'src': 'st'}
HOUSE_MOBILE = {'number': '3050000002', 'type': 'Mobile', 'carrier': 'T-Mobile', 'src': 'hh'}
NAME_MOBILE = {'number': '3050000003', 'type': 'Mobile', 'carrier': 'T-Mobile', 'src': 'nm'}
OWNER_MOBILE = {'number': '3050000004', 'type': 'Mobile', 'carrier': 'T-Mobile', 'src': 'st'}
AGENT = {'number': '5612518742', 'type': 'Mobile', 'carrier': 'Verizon', 'src': 'ag'}
SHARED = {'number': '3059999999', 'type': 'Mobile', 'carrier': 'Verizon', 'src': 'xl'}
DNC = {'number': '3050000005', 'type': 'Mobile', 'carrier': 'T-Mobile', 'dnc': True, 'src': 'st'}

# THE HEADLINE CASE. Before the src penalty, mobile(+40) beat landline(+15) unconditionally, so a
# household member's mobile outranked the OWNER'S OWN number and the board printed "CALL FIRST"
# over it. That is the ranker actively steering the first dial at the wrong person.
rec("the owner's landline outranks a household member's mobile",
    PR.score_phone(OWNER_LAND)[0] > PR.score_phone(HOUSE_MOBILE)[0],
    'owner %d vs household %d' % (PR.score_phone(OWNER_LAND)[0], PR.score_phone(HOUSE_MOBILE)[0]))
rec("the owner's landline outranks a name-only match's mobile",
    PR.score_phone(OWNER_LAND)[0] > PR.score_phone(NAME_MOBILE)[0])
rec('among owner numbers the mobile still wins (the old rule survives)',
    PR.score_phone(OWNER_MOBILE)[0] > PR.score_phone(OWNER_LAND)[0])
rec('an untagged number scores as the owner (every pre-phsrc build is untagged)',
    PR.score_phone({'number': '3050000006', 'type': 'Mobile', 'carrier': 'T-Mobile'})[0]
    == PR.score_phone(OWNER_MOBILE)[0])

rec('a listing agent is labelled NOT THE OWNER', PR.score_phone(AGENT)[1] == 'NOT THE OWNER')
rec('a shared office number is labelled NOT THE OWNER', PR.score_phone(SHARED)[1] == 'NOT THE OWNER')

# NEVER_FIRST is not BLOCKED, and the difference is the point: these stay on the row and stay
# dialable (an agent is a real party to the deal), they are just never what best() points at.
for nm, p in (('listing agent', AGENT), ('shared office number', SHARED)):
    ok_, blocked_ = PR.rank([p])
    rec('a %s stays dialable, not blocked' % nm, len(ok_) == 1 and not blocked_)
    rec('best() refuses to open on a %s' % nm, PR.best([p]) is None)

rec('a DNC number is still BLOCKED, not merely never-first', PR.rank([DNC]) == ([], PR.rank([DNC])[1])
    and len(PR.rank([DNC])[1]) == 1)
rec('best() picks the owner over an agent on the same lead',
    (PR.best([AGENT, OWNER_MOBILE]) or {}).get('number') == '3050000004')
rec('best() picks the owner over a household member',
    (PR.best([HOUSE_MOBILE, OWNER_LAND]) or {}).get('number') == '3050000001')
rec('a lead whose only number is the agent has NO first number',
    PR.best([AGENT]) is None)
rec('rank() still orders a mixed lead owner-first',
    [r['number'] for r in PR.rank([AGENT, HOUSE_MOBILE, OWNER_MOBILE])[0]]
    == ['3050000004', '3050000002', '5612518742'])

# ==================================================== 2. one number across many different owners
def lead(owners, phones, **kw):
    d = {'owners': owners, 'phones': list(phones)}
    d.update(kw)
    return d


OFFICE = '9545550100'
shared_set = [lead('SMITH, JOHN', ['3051110001', OFFICE]),
              lead('GARCIA, MARIA', ['3051110002', OFFICE]),
              lead('JOSEPH, MILOUSE', [OFFICE])]
n_leads, n_nums = FL.tag_shared_numbers(shared_set)
rec('a number on three different owners is tagged on every one of them',
    n_leads == 3 and n_nums == 1, '%d lead(s), %d number(s)' % (n_leads, n_nums))
rec('the tag lands on the right index, not the whole row',
    shared_set[0]['phsrc'] == ['st', 'xl'], shared_set[0].get('phsrc'))

# THE FALSE POSITIVE THIS MUST NOT HAVE. A landlord foreclosing on four rentals is four leads and
# one phone. Counting LEADS would tag the numbers of the multi-property owners who are the best
# calls on the board; counting distinct OWNERS does not.
same_owner = [lead('SMITH, JOHN', ['3052220001']) for _ in range(6)]
n_leads, n_nums = FL.tag_shared_numbers(same_owner)
rec('one owner with six properties is NOT tagged as an office',
    n_leads == 0 and n_nums == 0, '%d lead(s)' % n_leads)

two_owners = [lead('SMITH, JOHN', ['3053330001']), lead('GARCIA, MARIA', ['3053330001'])]
rec('two owners sharing a number is under the threshold (a couple, a relative)',
    FL.tag_shared_numbers(two_owners) == (0, 0))
rec('the threshold is configurable and actually applied',
    FL.tag_shared_numbers(two_owners, min_owners=2)[0] == 2)

# Formatting must not create a second identity for the same number.
fmt_set = [lead('A A', ['(305) 444-0001']), lead('B B', ['+1 305-444-0001']),
           lead('C C', ['3054440001'])]
rec('the same number formatted three ways counts once',
    FL.tag_shared_numbers(fmt_set)[1] == 1)

blank_owner = [lead('', ['3055550001']), lead('', ['3055550001']), lead('', ['3055550001'])]
rec('rows with no owner name cannot manufacture a shared number',
    FL.tag_shared_numbers(blank_owner) == (0, 0))

# ================================================================= 3. the listing-agent cross-check
ag = [lead('SMITH, JOHN', ['3056660001', '5612518742'], zagentphone='(561) 251-8742'),
      lead('GARCIA, MARIA', ['3056660002'], zagentphone='(561) 251-8742'),
      lead('JOSEPH, MILOUSE', ['3056660003'])]
rec('the lead whose dial list carries its own listing agent is tagged',
    FL.tag_listing_agents(ag) == 1)
rec('the agent number is tagged, the owner number beside it is not',
    ag[0]['phsrc'] == ['st', 'ag'], ag[0].get('phsrc'))
rec('a lead with an agent on file but not in its dial list is untouched',
    'phsrc' not in ag[1])
rec('an agent number is NOT dropped from the row — it is a real party to the deal',
    ag[0]['phones'] == ['3056660001', '5612518742'])
rec('a junk zagentphone matches nothing',
    FL.tag_listing_agents([lead('X X', ['3056660009'], zagentphone='call the office')]) == 0)

# The two passes run back to back in make_tracker; the second must not erase the first.
both = [lead('SMITH, JOHN', ['3057770001', OFFICE], zagentphone='3057770001'),
        lead('GARCIA, MARIA', [OFFICE]), lead('JOSEPH, MILOUSE', [OFFICE])]
FL.tag_shared_numbers(both)
FL.tag_listing_agents(both)
rec('shared and agent tags survive each other on the same lead',
    both[0]['phsrc'] == ['ag', 'xl'], both[0].get('phsrc'))

# ================================================================ 4. the caps, as source shapes
# MAX_PHONES is one constant used by all three merges. It was three separate literals (4, 4, 8)
# and they had already drifted once -- phtype was kept on one path and dropped on the other.
rec('MAX_PHONES is at least the old Whitepages total of 8', FL.MAX_PHONES >= 8)
rec('no merge still cuts the phone arrays to a bare literal 4',
    not re.search(r"\['phones'\] = \[p\.get\('number'\) for p in .*\[:4\]", open(
        os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()))
FL_SRC = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
rec('the county merge sorts before it cuts (it took the provider order and kept the first four)',
    '_sph = sorted(' in FL_SRC)
rec('both merges tag their numbers as address-matched skip trace',
    FL_SRC.count("[PHSRC_TRACE] * len(") == 2)
rec('the public no-phone payload strips phsrc with phones',
    re.search(r"if k not in \([^)]*'phsrc'[^)]*\)", FL_SRC) is not None)

# ==================================================== 5. the SHIPPED JS gates, run under node
PRE = """
var notes = {};
function _textContactBlocked(){ return ''; }
function _isDNT(){ return false; }
"""
TEXTABLE = extract(TPL_SRC, 'textablePhones')

OWNER_ROW = {'case': 'X', 'phones': ['3050000001', '3050000002', '3050000003', '3050000004'],
             'phtype': ['mobile'] * 4, 'phdnc': [False] * 4,
             'phsrc': ['st', 'hh', 'nm', 'wp']}
out = node(PRE + TEXTABLE + '\nconsole.log(JSON.stringify(textablePhones(%s)));' % json.dumps(OWNER_ROW))
# The batch body names the property and says a case was filed at the courthouse. Sending that to a
# tenant, a namesake or an agent discloses this homeowner's foreclosure to a third party.
rec('the text batch keeps the owner numbers and drops household + name-match',
    out == ['3050000001', '3050000004'], out)

AGENT_ROW = dict(OWNER_ROW, phones=['5612518742', '3059999999', '3050000004'],
                 phtype=['mobile'] * 3, phdnc=[False] * 3, phsrc=['ag', 'xl', 'st'])
out = node(PRE + TEXTABLE + '\nconsole.log(JSON.stringify(textablePhones(%s)));' % json.dumps(AGENT_ROW))
rec('the text batch drops the listing agent and the shared office number',
    out == ['3050000004'], out)

LEGACY = {'case': 'X', 'phones': ['3050000001', '3050000002'], 'phtype': ['mobile', 'mobile'],
          'phdnc': [False, False]}
out = node(PRE + TEXTABLE + '\nconsole.log(JSON.stringify(textablePhones(%s)));' % json.dumps(LEGACY))
rec('a board built before phsrc existed still texts every number it used to',
    out == ['3050000001', '3050000002'], out)

# THE SEND LIST STAYS CAPPED. The display paths dropped their slice(0,4) so every ranked number
# reaches the row; this one must not, or a wider dial list silently multiplies outbound SMS per
# lead against a $500-$1,500-per-message statute.
rec('textablePhones still caps the SEND list at 4',
    'slice(0,4)' in code_only(TEXTABLE))

BLOCK = extract(TPL_SRC, '_contactBlockHtml')
rec('the call sheet no longer slices the display list to 4',
    'slice(0,4)' not in code_only(BLOCK))
rec('the call sheet passes each number its source', 'src:' in code_only(BLOCK))
# The row's own contact group is an inline loop, not a named function, so it is asserted as an
# anchored source shape. Named here so a rename fails loudly instead of quietly leaving the row
# on four numbers while the call sheet shows ten.
rec("the row's contact group reads r.phsrc too",
    "src: ((r.phsrc||[])[i])||''" in TPL_SRC.replace(' ', '').replace("src:((r.phsrc||[])[i])||''",
                                                                     "src: ((r.phsrc||[])[i])||''")
    or "((r.phsrc||[])[i])" in TPL_SRC.replace(' ', ''))
rec("the row's contact group renders every ranked number, not four",
    TPL_SRC.replace(' ', '').count('(r.phones||[]).slice(0,4)'.replace(' ', '')) == 1,
    'only textablePhones may still cap')

LINE = extract(TPL_SRC, '_contactLineHtml')
for tagsrc, label in (('ag', 'listing agent'), ('xl', 'shared number'),
                      ('hh', 'household'), ('nm', 'name match')):
    rec('a %r number renders the %r tag' % (tagsrc, label), label in LINE)
rec('an owner number renders no source tag (a tag on every line is a tag nobody reads)',
    "src==='st'" not in code_only(LINE) and "src==='wp'" not in code_only(LINE))
rec('Call/Text/WA gating reads the source array',
    '_srcOwner' in code_only(LINE) and 'canTxt' in code_only(LINE))

print()
if fails:
    print('FAILED: ' + ', '.join(fails))
    sys.exit(1)
print('PASSED -- all phone-source checks')
