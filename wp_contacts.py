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


_DIRS = {'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW', 'NORTH', 'SOUTH', 'EAST', 'WEST',
         'NORTHEAST', 'NORTHWEST', 'SOUTHEAST', 'SOUTHWEST'}
_DIR_ABBR = {'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W', 'NORTHEAST': 'NE',
             'NORTHWEST': 'NW', 'SOUTHEAST': 'SE', 'SOUTHWEST': 'SW'}
_SUFFIX = {'ST': 'ST', 'STREET': 'ST', 'AVE': 'AVE', 'AV': 'AVE', 'AVENUE': 'AVE', 'CT': 'CT',
           'COURT': 'CT', 'RD': 'RD', 'ROAD': 'RD', 'DR': 'DR', 'DRIVE': 'DR', 'TER': 'TER',
           'TERR': 'TER', 'TERRACE': 'TER', 'PL': 'PL', 'PLACE': 'PL', 'LN': 'LN', 'LANE': 'LN',
           'BLVD': 'BLVD', 'BOULEVARD': 'BLVD', 'WAY': 'WAY', 'CIR': 'CIR', 'CIRCLE': 'CIR',
           'HWY': 'HWY', 'HIGHWAY': 'HWY', 'PKWY': 'PKWY', 'PARKWAY': 'PKWY', 'TRL': 'TRL',
           'TRAIL': 'TRL', 'CV': 'CV', 'COVE': 'CV', 'PATH': 'PATH', 'LOOP': 'LOOP', 'ROW': 'ROW'}


def _street_key(addr):
    """(house number, directionals, street name, suffix) or None.

    Every part counts. Miami-Dade numbers its streets, so '123 SW 8 ST', '123 NW 8th Ave' and
    '123 NE 8th Ct' share a house number and a street number and are three different houses; a
    key of house number + first word called them one, which let a same-name owner on the wrong
    street grade as the owner at our address. Ordinals are normalised ('8 ST' == '8th St'), and
    long forms are abbreviated ('Street' == 'ST', 'Southwest' == 'SW'). Unit numbers are ignored."""
    s = re.sub(r'[^A-Z0-9 ]', ' ', str(addr or '').upper())
    words = s.split()
    if not words or not words[0].isdigit():
        return None
    house, rest = words[0], words[1:]
    dirs, name, suffix = [], [], ''
    for w in rest:
        if w in ('APT', 'UNIT', 'STE', 'SUITE', '#'):
            break
        if suffix:
            if w in _DIRS:                                  # post-directional: '123 8 ST SW'
                dirs.append(_DIR_ABBR.get(w, w))
            break
        if w in _DIRS and not name:
            dirs.append(_DIR_ABBR.get(w, w))
        elif w in _SUFFIX and name:
            suffix = _SUFFIX[w]
        else:
            name.append(re.sub(r'^(\d+)(ST|ND|RD|TH)$', r'\1', w))
    if not name:
        return None
    return house, ' '.join(sorted(dirs)), ' '.join(name), suffix


def same_street(a, b):
    """Two addresses name the same house: same house number and street name, and the same
    directionals and suffix wherever BOTH carry them. One source dropping 'SW' or 'ST' is common;
    two different ones ('NW 8 AVE' vs 'NE 8 CT') are different houses."""
    ka, kb = _street_key(a), _street_key(b)
    if not ka or not kb:
        return False
    if ka[0] != kb[0] or ka[2] != kb[2]:
        return False
    for i in (1, 3):                                       # directionals, suffix
        if ka[i] and kb[i] and ka[i] != kb[i]:
            return False
    return True


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
                # Whitepages' own address dicts carry `street` as the street NAME only (no house
                # number) next to `line1` / `full_address` (seen on every cached /property record,
                # 2026-10-22). Reading `street` first made _street_key fail on every such address,
                # so no Person record could ever be placed at the property. Full lines first, and
                # house + street rebuilt when only the parts are there.
                for kk in ('street_line_1', 'line1', 'full_address', 'address'):
                    if a.get(kk):
                        out.append(str(a.get(kk)))
                if a.get('house') and a.get('street'):
                    out.append('%s %s' % (a.get('house'), a.get('street')))
    return [a for a in out if a]


def at_property(rec, lead_addr):
    """True when a Person record places this person at the lead's street address."""
    want = str(lead_addr or '').split(',')[0]
    if not _street_key(want):
        return False
    return any(same_street(a.split(',')[0], want) for a in _addr_strings(rec))


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
    out, by_name = [], {}
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
                    if not nm:
                        continue
                    if name_grade(lead_owners, nm) == 'owner':
                        continue
                    entry = by_name.get(nm.upper())
                    if entry is None:
                        entry = {'name': nm,
                                 'rel': str(rel.get('relation') or rel.get('type') or '').lower()[:30],
                                 'of': of, 'phones': []}
                        by_name[nm.upper()] = entry
                        out.append(entry)
                    elif not entry['rel']:
                        entry['rel'] = str(rel.get('relation') or rel.get('type') or '').lower()[:30]
                    for p in _phones_of(rel):
                        if p['n'] in have or len(entry['phones']) >= MAX_PHONES_PER_RELATIVE:
                            continue
                        have.add(p['n'])
                        entry['phones'].append(p)
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


def _prop_street(result):
    pa = (result or {}).get('property_address')
    if isinstance(pa, dict):
        pa = (pa.get('line1') or pa.get('street_line_1') or pa.get('full_address')
              or ('%s %s' % (pa.get('house') or '', pa.get('street') or '')).strip())
    return str(pa or '').split(',')[0]


def parcel_owners(verdict, lead_owners, lead_addr, result):
    """Which person_owners of a Whitepages /property result may be baked, given the APN verdict
    (foreclosure_leads.wp_parcel_verdict).

      'ok'         -> all of them; grade_property_owner then splits owner ('wp') from household.
      'mismatch'   -> none. The record is a different parcel; everyone on it is a stranger.
      'unverified' -> the lead has no folio to check against. Only a person whose name clears the
                      owner bar, on a record whose property street (house number + street name)
                      is our lead's street. That is the evidence a Person record needs for 'wp'.
                      Non-owner names are not baked, because 'household' only means something
                      when we know it is the right house."""
    owners = ((result or {}).get('ownership_info') or {}).get('person_owners') or []
    if verdict == 'ok':
        return list(owners)
    if verdict != 'unverified':
        return []
    if not same_street(_prop_street(result), str(lead_addr or '').split(',')[0]):
        return []
    return [o for o in owners if grade_property_owner(lead_owners, o.get('name')) == 'wp']


def wp_parcel_verdict(apn, folio):
    """Does a cached Whitepages property record belong to THIS lead's parcel?

    The WP cache is keyed by case number, which is correct, but a 2026-10-21 cross-check found
    half the joined records resolved to a DIFFERENT parcel (same house number, wrong street) because
    the address fed to Whitepages at lookup time was garbled. Verify the record's assessor parcel
    (apn) against the lead's folio before trusting its owners/phones for dialing.

      'ok'         -> apn matches folio (or there is no property parcel to check, e.g. a person-only
                      record) -> safe to bake; name-grading (wp_contacts) still applies downstream.
      'mismatch'   -> apn present and differs from folio -> record is for a different property; its
                      phones are strangers (wrong-party contact / TCPA-FTSA). Do not bake.
      'unverified' -> apn present but the lead has no usable folio (missing or the 'Property Appraiser'
                      scrape artifact) -> cannot tie the record to this lead by parcel. Only
                      parcel_owners()' owner-bar-plus-street rescue bakes from it.
    Either way the Person half of the record (searched by name, graded per record) still bakes.
    """
    a = re.sub(r'\D', '', str(apn or ''))
    f = re.sub(r'\D', '', str(folio or ''))
    if not a:
        return 'ok'            # no parcel claim on the record (person-only) — nothing to contradict
    if not f:
        return 'unverified'    # record claims a parcel; lead has none to check it against
    return 'ok' if a == f else 'mismatch'
