"""listing_status.classify() — FOR_SALE disambiguation (added 2026-08-06).

THE BUG: classify() treated homeStatus=FOR_SALE as a real listing ONLY when listingTypeDimension was
in TRUE_LISTING_TYPES. Zillow frequently emits "Unknown Listed By" on genuine MLS listings, so real
listings were silently labeled OFF-MARKET. Caught live on 16298 90TH ST N, Loxahatchee: MLS#
B26047763 (BeachesMLS), listing agent Denisse Quinones, $699,999, 35 days on market -> OFF-MARKET.
Two real consequences: the board showed a for-sale property as off-market, and the Agent Outreach
feature (which only offers on LISTED/PENDING) could never fire on it.

THE GUARD THAT MUST SURVIVE: Zillow's OWN pre-foreclosure/auction data pages also carry
homeStatus=FOR_SALE. Those are not listings and must stay OFF-MARKET. Zillow exposes
isPreforeclosureAuction directly, so the fix keys off that instead of inferring from
listingTypeDimension — these tests pin BOTH behaviors so a future edit can't trade one for the other.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\olqbb\projects\foreclosure-leads")
from listing_status import classify

ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


# --- the exact live case that exposed the bug -------------------------------------------------
got = classify('FOR_SALE', 'Unknown Listed By', 699999, 35,
               mls_id='B26047763', agent='Denisse Quinones', prefc_auction=False)
rec('REGRESSION: real MLS listing with "Unknown Listed By" is LISTED', got == 'LISTED', got)

# --- the guard that must not be lost ----------------------------------------------------------
got = classify('FOR_SALE', 'Unknown Listed By', 0, 0,
               mls_id='', agent='', prefc_auction=True)
rec('GUARD: Zillow pre-foreclosure/auction page stays OFF-MARKET', got == 'OFF-MARKET', got)

got = classify('FOR_SALE', 'Unknown Listed By', 500000, 10,
               mls_id='B999', agent='Some Agent', prefc_auction=True)
rec('GUARD: preforeclosure flag WINS even if mls/agent present', got == 'OFF-MARKET', got)

got = classify('FOR_SALE', 'Unknown Listed By', 0, 0)
rec('CONSERVATIVE: FOR_SALE with no mls, no agent, unknown type -> OFF-MARKET',
    got == 'OFF-MARKET', got)

# --- existing happy paths must be untouched ---------------------------------------------------
rec('by-agent listing still LISTED',
    classify('FOR_SALE', 'For Sale by Agent', 400000, 5) == 'LISTED')
rec('by-owner listing still LISTED',
    classify('FOR_SALE', 'For Sale by Owner', 400000, 5) == 'LISTED')
rec('PENDING unchanged', classify('PENDING', '', 0, 0) == 'PENDING')
rec('SOLD unchanged', classify('RECENTLY_SOLD', '', 0, 0) == 'SOLD')
rec('FOR_RENT unchanged', classify('FOR_RENT', '', 0, 0) == 'RENTAL')
rec('OFF_MARKET unchanged', classify('OFF_MARKET', '', 0, 0) == 'OFF-MARKET')
rec('unknown homeStatus still returns empty (too thin to trust)',
    classify('SOMETHING_NEW', '', 0, 0) == '')

# --- either signal alone is enough --------------------------------------------------------------
rec('mls id ALONE proves a real listing',
    classify('FOR_SALE', 'Unknown Listed By', 0, 0, mls_id='X123') == 'LISTED')
rec('named agent ALONE proves a real listing',
    classify('FOR_SALE', 'Unknown Listed By', 0, 0, agent='Jane Doe') == 'LISTED')
rec('whitespace-only agent does NOT count as proof',
    classify('FOR_SALE', 'Unknown Listed By', 0, 0, agent='   ') == 'OFF-MARKET')

# --- backward compatibility ----------------------------------------------------------------------
rec('old 4-arg call signature still works (defaults preserve prior behavior)',
    classify('FOR_SALE', 'For Sale by Agent', 1, 1) == 'LISTED')

print(f"\n==== {len(ok)}/{len(ok)+len(bad)} zstatus classification checks passed ====")
if bad:
    print('FAILED:', bad)
    sys.exit(1)
