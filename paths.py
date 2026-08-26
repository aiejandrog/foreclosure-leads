"""paths — where DealFlow writes the things git does not carry.

WHY THIS EXISTS
Twenty modules each built their own copy of the output path, and every one of them hardcoded
`~/OneDrive/Desktop/DEALFLOW`. That folder is inside OneDrive, so everything written there was
replicated to consumer cloud storage on a timer: the plaintext ungated board (~1,800 owner names
and addresses, ~1,000 phone numbers), the property photo set, the call lists, the door logs, the
action plans. The 18MB WeTransfer bundle that got moved off OneDrive on 2026-08-22 was a one-time
exposure. This one refreshed itself every morning.

Moved 2026-08-22 to `~/DEALFLOW` — same user profile, one level up, NOT synced by anything. A
shortcut at `OneDrive\\Desktop\\DEALFLOW.lnk` keeps the double-click workflow; a .lnk is a path,
not the data, so syncing it costs nothing.

WHAT DID NOT CHANGE
  * `DEALFLOW_DIR` env override still wins — GitHub Actions sets it to a throwaway tmp path.
  * `DEALFLOW_NO_DESKTOP=1` still skips the write entirely; that check lives in each caller.
  * Nothing about docs/index.html, which is the encrypted published board and unaffected by any
    of this.

MIGRATION NOTE FOR THE OTHER MACHINE
The laptop still has the old `OneDrive\\Desktop\\DEALFLOW` full of PII. Pulling this commit makes
the next refresh write to the NEW location; it does not delete the old folder. Delete it there by
hand, then empty the OneDrive recycle bin — otherwise the exposure this commit closes is still
sitting in the cloud.

USAGE
    import paths as P
    P.out('BSG_Call_List_2026-08-22.pdf')   # full path, parent folder created
    P.DEALFLOW_DIR                          # the folder itself
    P.TWIN                                  # the plaintext board twin
    P.DESKTOP                               # the real Desktop — PII must NOT go here
"""
import os

_HOME = os.path.expanduser('~')

# The DealFlow output folder. Deliberately outside OneDrive — see the module docstring before
# changing this to anything under a sync root.
DEALFLOW_DIR = os.environ.get('DEALFLOW_DIR') or os.path.join(_HOME, 'DEALFLOW')

# The plaintext, ungated board twin. Carries phone numbers; it is the single most sensitive file
# this project writes to disk.
TWIN = os.path.join(DEALFLOW_DIR, 'Foreclosure Lead Tracker.html')

# Where a machine-transfer bundle is built. prep_desktop.py used to drop the zip — 8 live API keys,
# the ledgers, 174 data files — straight onto the synced Desktop, which is exactly the folder that
# had to be evacuated by hand on 2026-08-22. Building it here means re-running that script cannot
# recreate the problem.
TRANSFER_DIR = os.environ.get('DEALFLOW_TRANSFER_DIR') or os.path.join(_HOME, 'secure')

# The user's real Desktop. Known Folder Move puts it under OneDrive on this account, so treat it as
# SYNCED: shortcuts and status text are fine, homeowner data is not.
_OD_DESKTOP = os.path.join(_HOME, 'OneDrive', 'Desktop')
DESKTOP = _OD_DESKTOP if os.path.isdir(_OD_DESKTOP) else os.path.join(_HOME, 'Desktop')

# True when DESKTOP is inside a sync root — callers that want to write something there can check
# first instead of assuming.
DESKTOP_IS_SYNCED = os.path.isdir(_OD_DESKTOP)


def out(*parts):
    """Absolute path inside DEALFLOW_DIR, with the folder created on first use.

    Every caller used to run its own os.makedirs; doing it here means a new output file cannot
    fail on a fresh machine just because nobody remembered the mkdir.
    """
    os.makedirs(DEALFLOW_DIR, exist_ok=True)
    return os.path.join(DEALFLOW_DIR, *parts)


def ensure():
    """Create DEALFLOW_DIR and return it."""
    os.makedirs(DEALFLOW_DIR, exist_ok=True)
    return DEALFLOW_DIR


if __name__ == '__main__':
    print('DEALFLOW_DIR      :', DEALFLOW_DIR)
    print('  exists          :', os.path.isdir(DEALFLOW_DIR))
    print('  inside OneDrive :', 'onedrive' in DEALFLOW_DIR.lower(), '  <-- must be False')
    print('TWIN              :', TWIN)
    print('DESKTOP           :', DESKTOP)
    print('  synced          :', DESKTOP_IS_SYNCED)


# ---------------------------------------------------------------------------------------------
# THE ACCESS CODE IS NOT A CONSTANT — it is a secret, and it belongs with the other things git
# does not carry.
#
# Eighteen test suites each hardcoded `CODE = 'DEALFLOW-xxxxxxxx'`. All eighteen were gitignored,
# so nothing was published by them — but two OTHER suites carried the same literal and were TRACKED
# via ! negations, which put a live board-decrypting code into this PUBLIC repo (scrubbed 69884b5).
# The literal is the hazard; whether a given file happens to be ignored today is not a property you
# can rely on, because adding a negation is a one-line change nobody thinks of as publishing a key.
#
# It also made rotation an 18-file edit, which is its own reason the code never got rotated.
# ---------------------------------------------------------------------------------------------
def live_code(name=''):
    """An access code from the gitignored site.codes. `name` picks a specific operator's line
    (substring, case-insensitive); default is the first code in the file.

    Returns None when site.codes is absent — a caller on a machine without it should say so and
    skip, not fall back to a literal.
    """
    import re as _re
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'site.codes')
    try:
        for line in open(p, encoding='utf-8'):
            if name and name.lower() not in line.lower():
                continue
            m = _re.search(r'(DEALFLOW-[A-Z0-9]{6,})', line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None
