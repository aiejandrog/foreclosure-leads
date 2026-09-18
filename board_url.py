"""board_url — the ONE place that knows where the published board lives.

WHY THIS EXISTS (2026-09-18)
On 2026-09-17 the engine and the site became two repos and the board moved from
`aiejandrog.github.io/foreclosure-leads/` to `aiejandrog.github.io/dealflow-board/`. Commit
7a694ac edited both hardcoded copies in tracker_template.html, README, CLAUDE.md, HOW-TO-USE,
MACHINE-HANDOFF, access_codes.py and the freshness watchdog — nine files, by hand, in one pass.

It still broke. The board that was ALREADY BUILT kept the old string baked into it, and because
`publish_site.py` had never successfully run, that build stayed live. On the morning of 09-18 the
Morning Worker finished its queue and offered "Call these 30 now" pointing at a URL that 404s,
because `foreclosure-leads` is private with Pages off. A hand-edit cannot reach a page that was
generated yesterday; only a rebuild can, and nothing checked.

So the address is a value now, baked at build time through `subst_build_facts()` like every other
fact Python owns, and `check_board_urls()` refuses to ship a page carrying any other one. The next
move only has to change this file, and a board built before the move can no longer be published.

Nothing here reads the network. It is a string and a regex.
"""
import re

# The canonical published board. Trailing slash is load-bearing: callers append 'call/' to it.
BOARD_URL = 'https://aiejandrog.github.io/dealflow-board/'

# Addresses this project has published from and has since abandoned. A page carrying one of these
# is a stale BUILD, not a typo — see the module docstring. Keep old entries forever; the whole
# point is to recognise a board that was generated before a move.
RETIRED = (
    'https://aiejandrog.github.io/foreclosure-leads/',   # engine repo, went private 2026-09-17
    'https://aiejandrog.github.io/dealflow/',            # never existed; was the repo homepage field
)

# Any GitHub Pages URL under this account, so an address nobody predicted is still caught.
_PAGES = re.compile(r'https?://aiejandrog\.github\.io/[A-Za-z0-9._-]*/?')


def call_url(seat=''):
    """Call Mode's published address, optionally for a named seat ('carlos')."""
    seg = str(seat or '').strip().strip('/')
    return BOARD_URL + 'call/' + (seg + '/' if seg else '')


def offenders(text):
    """Every Pages URL in `text` that is not under BOARD_URL, de-duplicated, in page order.

    Matching is on the account's whole github.io space rather than the RETIRED list alone: the
    failure being guarded against is "this page was built before the move", and the next move's
    old address is not in RETIRED until someone adds it.
    """
    seen, out = set(), []
    for m in _PAGES.finditer(str(text or '')):
        u = m.group(0)
        if u.rstrip('/') + '/' == BOARD_URL:
            continue
        if u.startswith(BOARD_URL):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def check_board_urls(text, where='the built page'):
    """Raise SystemExit if `text` points anywhere but the live board. Called from the build."""
    bad = offenders(text)
    if bad:
        raise SystemExit(
            'BUILD ABORTED: %s carries %d board URL(s) that are not the live board.\n'
            '   live board : %s\n'
            '   found      : %s\n'
            'A published page linking a retired address is a dead button for the operator — the '
            "Morning Worker's \"Call these N now\" shipped exactly that on 2026-09-18. Fix the "
            'address in board_url.py and rebuild; never hand-edit the built page.'
            % (where, len(bad), BOARD_URL, ', '.join(bad)))
    return True


if __name__ == '__main__':
    print('BOARD_URL:', BOARD_URL)
    print('call     :', call_url())
    print('carlos   :', call_url('carlos'))
    print('retired  :', ', '.join(RETIRED))
