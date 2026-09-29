"""wp_contacts -- WHO a Whitepages Pro record belongs to, and where relatives go.

Pure functions, no network, no key, no board. whitepages_lookup.py (the paid caller) and
foreclosure_leads.make_tracker (the bake) both import from here so the match rules exist once.
Guarded by _wpcontactstest.py.

THE OWNER BAR. A Whitepages number is tagged as the OWNER's ('wp') only when:
  * the name passes ownership_gate.owner_relation() as 'same' -- the check that decides whether
    the live appraiser owner is still our defendant -- AND
  * the two names share at least two significant tokens, one of them a surname. owner_relation
    alone returns 'same' for a one-token subset ("GARCIA" vs "JOSE GARCIA") because a false
    'different' there kills a live lead. Here the failure runs the other way: a false 'same' dials
    a stranger with the homeowner script. So the thin cases are refused.
  * For the Person layer (a name search, fuzzy on), the record must ALSO place that person at the
    property address (house number + street name). Name alone is 'nm', as before.
A record that fails the name bar entirely is dropped, not baked. Before this, every fuzzy Person
candidate's phones were baked as 'nm', namesakes included.

RELATIVES. Numbers Whitepages lists for the owner's relatives go in r.wpRelatives and NOWHERE
else. Never r.phones (which feeds textablePhones, the worker, the send bridge and Call Mode),
never r.emails. They are call-only, and the board hides them unless the owner has no reachable
number. Calling a relative about someone's foreclosure is a privacy and TCPA question; the
script is the attorney's call, not this module's.
"""
import re

try:
    from ownership_gate import owner_relation, _tokens, _surnames, _is_placeholder, _is_entity
except Exception:                                    # pragma: no cover - ownership_gate is in-repo
    raise

PHSRC_RELATIVE = 'rl'           # used only inside r.wpRelatives; never appears in r.phsrc
MAX_RELATIVES = 6
MAX_PHONES_PER_RELATIVE = 3

# Whitepages has used more than one key for related people across API versions. Read all of them;
# which one v2 Person actually returns is confirmed by `whitepages_lookup.py --schema` on the
# laptop (it prints key names only, never values).
_RELATIVE_KEYS = ('relatives', 'associated_people', 'related_people', 'relations')
_ADDR_LIST_KEYS = ('current_addresses', 'historical_addresses', 'historic_addresses',
                   'addresses', 'locations', 'address_history', 'found_at_address')


def _last10(v):
    d = ''.join(c for c in str(v or '') if c.isdigit())
    if len(d) == 11 and d.startswith('1'):
        d = d[1:]
    return d if len(d) == 10 else ''


def _owner_chunks(lead_owners):
    return [c.strip() for c in re.split(r';', str(lead_owners or '')) if c.strip()]


def name_grade(lead_owners, wp_name):
    """'owner' | 'kin' | 'no' -- is wp_name one of the lead's owners?

    'owner': passes the owner bar above against at least one owner on the lead.
    'kin'  : shares only a surname (spouse, child, or a stranger with a common surname).
    'no'   : nothing usable, or either side is empty / a placeholder / an entity."""
    wp_name = str(wp_name or '').strip()
    if _is_placeholder(wp_name) or _is_entity(wp_name):
        return 'no'
    best = 'no'
    for own in _owner_chunks(lead_owners):
        if _is_placeholder(own) or _is_entity(own):
            continue
        rel = owner_relation(own, wp_name)
        if rel == 'same':
            ca, aa = _surnames(own)
            cb, ab = _surnames(wp_name)
            surn = ca | cb | aa | ab
            for glue in (True, False):
                shared = _tokens(own, glue) & _tokens(wp_name, glue)
                if len(shared) >= 2 and (shared & surn):
                    return 'owner'
            best = 'kin'                              # 'same' only by a thin subset
        elif rel == 'unsure':
            best = 'kin'
    return best


def _street_key(addr):
    """(house number, first street-name word) or None. 'SW 8TH ST' and '8TH ST' compare equal on
    the number + first non-directional word, which is what both sources agree on."""
    s = re.sub(r'[^A-Z0-9 ]', ' ', str(addr or '').upper())
    words = s.split()
    if not words or not words[0].isdigit():
        return None
    rest = [w for w in words[1:] if w not in ('N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW')]
    if not rest:
        return None
    return words[0], rest[0]


def _addr_strings(rec):
    out = []
    for k in _ADDR_LIST_KEYS:
        v = rec.get(k)
        if isinstance(v, dict):
            v = [v]
        for a in (v or []):
            if isinstance(a, str):
                out.append(a)
            elif isinstance(a, dict):
                out.append(a.get('street_line_1') or a.get('street') or a.get('address')
                           or a.get('full_address') or a.get('line1') or '')
    return [a for a in out if a]


def at_property(rec, lead_addr):
    """True when a Person record places this person at the lead's street address."""
    want = _street_key(str(lead_addr or '').split(',')[0])
    if not want:
        return False
    return any(_street_key(a.split(',')[0]) == want for a in _addr_strings(rec))


def grade_person_record(lead_owners, lead_addr, rec):
    """-> 'wp' (owner, placed at the property) | 'nm' (owner by name only) | None (drop)."""
    if name_grade(lead_owners, (rec or {}).get('name')) != 'owner':
        return None
    return 'wp' if at_property(rec, lead_addr) else 'nm'


def grade_property_owner(lead_owners, wp_name):
    """A person Whitepages lists as an owner of the parcel. 'wp' when the name clears the owner
    bar, 'hh' otherwise: on the parcel but not the person our records say owns it (a spouse the
    clerk did not name, or a newer owner -- ownership_gate decides which)."""
    return 'wp' if name_grade(lead_owners, wp_name) == 'owner' else 'hh'


def _phones_of(obj):
    out = []
    for p in (obj or {}).get('phones') or []:
        raw = p.get('number') if isinstance(p, dict) else p
        n = _last10(raw)
        if n:
            out.append({'n': n, 'type': str((p.get('type') if isinstance(p, dict) else '') or '').lower()})
    return out


def extract_relatives(lead_owners, lead_addr, person_records, owner_numbers=()):
    """Relatives listed on Person records that cleared the owner bar. -> [{name, rel, of, phones}]

    A namesake's relatives are a stranger's family, so records graded None contribute nothing.
    A number already on the lead in any role is left where it is (it is not re-labelled as a
    relative's), and a relative who is also an owner on the lead is skipped."""
    have = {_last10(n) for n in owner_numbers if _last10(n)}
    out, seen_names = [], set()
    for pr in person_records or []:
        for rec in (pr.get('response') or []):
            if grade_person_record(lead_owners, lead_addr, rec) is None:
                continue
            of = str(rec.get('name') or pr.get('name') or '')
            for k in _RELATIVE_KEYS:
                for rel in (rec.get(k) or []):
                    if not isinstance(rel, dict):
                        continue
                    nm = str(rel.get('name') or '').strip()
                    if not nm or nm.upper() in seen_names:
                        continue
                    if name_grade(lead_owners, nm) == 'owner':
                        continue
                    phs = []
                    for p in _phones_of(rel):
                        if p['n'] in have:
                            continue
                        have.add(p['n'])
                        phs.append(p)
                    seen_names.add(nm.upper())
                    out.append({'name': nm,
                                'rel': str(rel.get('relation') or rel.get('type') or '').lower()[:30],
                                'of': of,
                                'phones': phs[:MAX_PHONES_PER_RELATIVE]})
    # relatives with a number first; name-only entries still help a human search by hand
    out.sort(key=lambda r: 0 if r['phones'] else 1)
    return out[:MAX_RELATIVES]


def relatives_allowed(lead, never_contact=None):
    """Python-side gate for baking r.wpRelatives. Any stay, never-contact, dead/claimed or
    dismissed state strips them. The board re-checks at render time; this is the second lock."""
    if lead.get('saleBkAct') or lead.get('sale_bk_active') or lead.get('bkWhy'):
        return False
    if lead.get('sibclaimed') or lead.get('lpDismissed') or lead.get('ddhold'):
        return False
    if never_contact is not None:
        try:
            if never_contact(lead.get('case')):
                return False
        except Exception:
            return False                              # unreadable never-contact list: fail closed
    return True
