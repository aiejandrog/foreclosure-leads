"""sunbiz_entities: what Florida's corporate registry says about an entity on title. $0.

Priority 6 of the Miami automation goal. When the current deed candidate's grantee is an LLC or a
corporation, the question "who do we talk to" has an answer on Sunbiz: the entity's status, its
registered agent and its listed officers. That answer is useful and it is also narrow, and this
module keeps it narrow:

  - ACTIVE is a filing status. It does not mean the entity holds title (only the deed chain can
    say that) and it does not mean any officer may sign for it or speak for it.
  - An officer or a registered agent is a person to research, not a decision-maker we have
    verified. `title_authority` and `contact_authority` are always 'not_established'.
  - An entity-only owner is never call-ready on this evidence. `call_ready` is always False here;
    a later step may qualify a named person on other evidence, never on this record alone.
  - "Sunbiz could not be reached" is never "not found". The lookup either returns a match, a
    reasoned not_found (with the near names the registry offered), or an error.
  - Trusts, estates and heirs are not in Sunbiz; they come back 'not_applicable' with the reason,
    so nobody reads a missing lookup as a failed one.

Matching is llc_officers._lookup's: exact, identical once the suffix is dropped, or a typo-tight
(>=0.92) match with the sibling-series guard. A typo match is reported as 'typo_match_unverified'.

Results are cached in DEALFLOW_DIR/title_discovery/sunbiz-cache.json for CACHE_DAYS so a rerun
makes no requests. The network step is a public, free registry page; no key, no spend.
"""
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

CACHE_DAYS = 30
_SUNBIZ_RE = re.compile(r'\b(L\.?L\.?C\.?|L\.?L\.?L\.?P|L\.?L\.?P|L\.?P|INC(?:ORPORATED)?|CORP(?:ORATION)?|'
                        r'COMPANY|CO|LTD|P\.?A|PLLC|HOLDINGS)\b\.?', re.I)
_NOT_SUNBIZ_RE = re.compile(r'\b(TRUST|TRUSTEES?|ESTATE OF|ESTATE|HEIRS|REVOCABLE|LIVING TRUST|'
                            r'DECEASED|UNKNOWN SPOUSE)\b', re.I)
_PUBLIC_RE = re.compile(r'\b(BANK|MORTGAGE|FEDERAL NATIONAL|FANNIE|FREDDIE|SECRETARY OF|HUD|'
                        r'UNITED STATES|STATE OF|COUNTY|CITY OF|ASSOCIATION)\b', re.I)


class SunbizUnreachable(RuntimeError):
    pass


def kind_of(name):
    """-> 'sunbiz_entity' | 'trust_or_estate' | 'institution' | 'person'. A trust holding title
    through its trustee is still a trust: the trustee is the person to research, via the deed."""
    text = str(name or '').strip()
    if _PUBLIC_RE.search(text):
        return 'institution'
    # 'TRUST HOLDINGS LLC' is an LLC; 'SMITH FAMILY TRUST' is a trust. The suffix decides first.
    if re.search(_SUNBIZ_RE.pattern + r'\W*$', text, re.I):
        return 'sunbiz_entity'
    if _NOT_SUNBIZ_RE.search(text):
        return 'trust_or_estate'
    if _SUNBIZ_RE.search(text):
        return 'sunbiz_entity'
    return 'person'


def strict_fetch(url):
    """curl that raises instead of returning '' when Sunbiz does not answer."""
    import llc_officers as LO
    try:
        result = subprocess.run([LO.CURL, '-s', '-S', '-L', '--fail', '--max-time', '25', '-A', LO.UA,
                                 '-H', 'Accept: text/html,application/xhtml+xml', url],
                                capture_output=True, text=True, encoding='utf-8', errors='replace')
    except OSError as exc:
        raise SunbizUnreachable('curl not runnable: %s' % exc)
    if result.returncode != 0 or not result.stdout.strip():
        raise SunbizUnreachable('curl exit %s: %s' % (result.returncode, (result.stderr or '').strip()[:160]))
    return result.stdout


def _key(name):
    return re.sub(r'[^A-Z0-9]+', ' ', str(name or '').upper()).strip()


def _cache_path():
    import case_review
    return Path(case_review.output_path('title_discovery/sunbiz-cache.json'))


def _now():
    return datetime.now(timezone.utc)


def resolve(name, lookup=None, cache=None, now=None):
    """-> one entity record. `lookup(name)` returns llc_officers._lookup's dict or raises;
    `cache` is a dict of previous records by key (mutated with the new one)."""
    now = now or _now()
    record = {'name': name, 'kind': kind_of(name), 'title_authority': 'not_established',
              'contact_authority': 'not_established', 'call_ready': False}
    if record['kind'] != 'sunbiz_entity':
        reason = {'trust_or_estate': 'trusts, estates and heirs are not registered on Sunbiz; the deed '
                                     'and probate records name who acts for them',
                  'institution': 'a lender, agency or association; not an owner contact',
                  'person': 'a natural person; Sunbiz is not a source for people'}[record['kind']]
        return dict(record, lookup='not_applicable', reason=reason)
    key = _key(name)
    cached = (cache or {}).get(key)
    if cached and cached.get('lookup') in ('found', 'not_found'):
        try:
            fresh = now - datetime.fromisoformat(cached['checked_at']) <= timedelta(days=CACHE_DAYS)
        except (KeyError, TypeError, ValueError):
            fresh = False
        if fresh:
            return dict(cached, cached=True)
    if lookup is None:
        import llc_officers as LO
        lookup = lambda n: LO._lookup(n, fetch=strict_fetch)
    try:
        raw = lookup(name)
    except Exception as exc:   # a failed lookup is a gap, never a not_found
        return dict(record, lookup='error', reason='%s: %s' % (type(exc).__name__, str(exc)[:160]),
                    checked_at=now.isoformat())
    if raw.get('not_found'):
        record.update(lookup='not_found', near=raw.get('near') or [],
                      reason='no exact, suffix or typo-tight Sunbiz match; the registry offered the near names')
    else:
        record.update(
            lookup='found', matched_name=raw.get('matched'),
            match='typo_match_unverified' if raw.get('typo') else 'exact_or_suffix',
            status=raw.get('status') or 'unknown', document_number=raw.get('doc') or None,
            filed=raw.get('filed') or None,
            registered_agent={'name': raw.get('ra') or None, 'address': raw.get('ra_addr') or None,
                              'authority': 'registered_agent_accepts_service_only'},
            officers=[{'title': o.get('t'), 'name': o.get('n'), 'address': o.get('a'),
                       'authority': 'listed_on_filing_not_verified_signatory'}
                      for o in raw.get('officers') or []],
            reason=('Sunbiz status is a filing status. It does not establish that this entity holds '
                    'title, or that any listed person may sign or speak for it.'))
    record['checked_at'] = now.isoformat()
    if cache is not None:
        cache[key] = record
    return record


def resolve_owners(names, lookup=None, cache_file=None, now=None):
    """Resolve each distinct name once, reusing and updating the on-disk cache."""
    path = Path(cache_file) if cache_file else None
    cache = {}
    if path is None and lookup is None:
        path = _cache_path()
    if path is not None and path.is_file():
        try:
            cache = json.loads(path.read_text(encoding='utf-8'))
        except ValueError:
            cache = {}
    seen, out = set(), []
    for name in names or []:
        if _key(name) in seen or not _key(name):
            continue
        seen.add(_key(name))
        out.append(resolve(name, lookup=lookup, cache=cache, now=now))
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(cache, indent=2) + '\n', encoding='utf-8')
        tmp.replace(path)
    return out


def people_to_research(record):
    """Named people behind an entity, with the limit of what each name means. Never a contact
    list: an officer who is itself another company is dropped, not greeted."""
    if record.get('lookup') != 'found':
        return []
    people = []
    for officer in record.get('officers') or []:
        if kind_of(officer.get('name')) != 'person':
            continue
        people.append({'name': officer['name'], 'why': 'Sunbiz %s of %s' % (officer.get('title') or 'officer',
                                                                          record.get('matched_name')),
                       'authority': 'listed_on_filing_not_verified_signatory'})
    agent = (record.get('registered_agent') or {}).get('name')
    if agent and kind_of(agent) == 'person':
        people.append({'name': agent, 'why': 'Sunbiz registered agent of %s' % record.get('matched_name'),
                       'authority': 'registered_agent_accepts_service_only'})
    return people
