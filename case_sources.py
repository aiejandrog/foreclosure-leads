"""Priority public-record portals from the user's 2026-09-22 county bookmarks.

Discovery URLs only: reaching a landing page never counts as reviewing a case.
The Miami bookmark's example folio is deliberately not a default search target.
"""
from copy import deepcopy

SOURCES = {
    'MIAMI-DADE': {
        'auction': 'https://miamidade.realforeclose.com/index.cfm?zaction=USER&zmethod=CALENDAR',
        'court': 'https://www2.miamidadeclerk.gov/ocs/',
        'official_records': 'https://onlineservices.miamidadeclerk.gov/officialrecords',
        'appraiser': 'https://apps.miamidadepa.gov/PropertySearch/',
    },
    'BROWARD': {
        'auction': 'https://www.broward.realforeclose.com/index.cfm?ZACTION=USER&ZMETHOD=CALENDAR',
        'court': 'https://www.browardclerk.org/web2',
        'official_records': 'https://officialrecords.broward.org/AcclaimWeb/search/SearchTypeName',
        'appraiser': 'https://web.bcpa.net/BcpaClient/#/Record-Search',
    },
    'PALM BEACH': {
        'auction': 'https://palmbeach.realforeclose.com/index.cfm?ZACTION=USER&ZMETHOD=CALENDAR',
        'court': 'https://appsgp.mypalmbeachclerk.com/eCaseView/Search',
        'official_records': 'https://erec.mypalmbeachclerk.com/',
        'appraiser': 'https://pbcpao.gov/index.htm',
    },
}

REQUIRED = {
    'auction': ['case identifier', 'sale date and current status', 'auction item identifier'],
    'court': ['all docket pages', 'all accessible documents and exhibits', 'parties and counsel',
              'judgment and later orders', 'death/probate references and related cases'],
    'official_records': ['subject legal description', 'deed chain', 'foreclosed instrument',
                         'assignments and modifications', 'releases and satisfactions'],
    'appraiser': ['parcel identifier', 'situs and mailing addresses', 'record owner',
                  'legal description', 'assessment date and value'],
}


def source_tasks(county):
    """Return independent pending tasks; no network request or verification is implied."""
    if county not in SOURCES:
        raise ValueError('County must be MIAMI-DADE, BROWARD or PALM BEACH')
    return [{'source_id': county + ':' + kind, 'county': county, 'kind': kind,
             'url': url, 'priority': 1, 'status': 'pending', 'owner_type': 'agent',
             'human_verification_required': False, 'required_evidence': deepcopy(REQUIRED[kind]),
             'findings': [], 'blocker': None}
            for kind, url in SOURCES[county].items()]
