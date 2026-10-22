"""All nine bilingual genEmail pairs: meaning, callback ask, and immutable closing."""
import copy
import re
from pathlib import Path
import outreach_copy as OC
import unittest
import disclaimer
from email_variant_audit import variants

CLOSING = {
    'en': "If now's not a good time, just tell me and I won't reach out again.",
    'es': 'Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.',
}
# Observable message concepts, rather than a duplicate of every sentence.
CONCEPTS = {
    'property_note': ("I'm writing about", 'Le escribo sobre'),
    'no_date': ('does not list an auction date', 'no indica una fecha de subasta'),
    'record_date': ('Our record lists', 'Nuestro registro indica'),
    'tax_sale': ('tax-deed', 'tax deed'),
    'stale': ('may be out of date', 'puede estar desactualizado'),
    'county_check': ('check the current status with the county', 'confirme el estado actual con el condado'),
    'advisor': ('with our senior advisor', 'con nuestro asesor principal'),
    'handled': ('already handled', 'ya está resuelto'),
    'follow': ('following up on my earlier note', 'dar seguimiento a mi mensaje'),
    'final': ('my last note', 'mi última nota'),
    'no_more': ("won't send another follow-up", 'No enviaré otro mensaje de seguimiento'),
    'call': ('Call or text me at', 'Llámeme o mándeme un texto al'),
    'reply': ('reply with a time that works for you', 'responda con un horario que le convenga'),
}


def approved_english():
    return OC.fill(OC.email_body_template(), first='Synthetic', date='12/31/2099',
                   signer='Synthetic Advisor', phone='305-555-0100', company='Example Company',
                   sig='Synthetic Advisor\nExample Company\nPhone: 305-555-0100\n1 Example Street',
                   ident=disclaimer.identity('en', as_html=False))


def paragraphs(body):
    return [p.strip() for p in re.split(r'\n\s*\n', body.strip()) if p.strip()]


def check_dated(rows):
    by = {r['lang']: r for r in rows if (r['kind'], r['stage']) == ('dated', 'cold')}
    assert set(by) == {'en', 'es'}, 'missing dated EN/ES counterpart'
    for lang in ('en', 'es'):
        assert by[lang]['body'].rstrip().endswith(CLOSING[lang]), 'altered opt-out closing'
        assert by[lang]['body'].count(CLOSING[lang]) == 1, 'duplicate closing'
        assert disclaimer.identity(lang, as_html=False) in by[lang]['body'], 'missing sender disclosure'
    assert by['en']['body'] == approved_english(), 'dated English differs from rendered _ALEXMAIL_EN'
    assert len(paragraphs(by['es']['body'])) == len(paragraphs(by['en']['body'])), 'dated paragraph count differs'
    es = by['es']['body']
    for text in ('12/31/2099', 'Synthetic Advisor', 'Example Company', '305-555-0100',
                 OC.BOOKING_URL, 'cinco minutos', 'quince', 'más de 30 años',
                 'mejor postor', 'capital en la propiedad intacto',
                 'mejor número y horario', 'llamarme directamente'):
        assert text in es, 'dated Spanish omitted a fact or ask: '+text


def check_matrix(rows):
    expected = {(kind, stage, lang) for kind in ('early', 'tax', 'dated')
                for stage in ('cold', 'follow', 'final') for lang in ('en', 'es')}
    by = {(r['kind'], r['stage'], r['lang']): r for r in rows}
    assert len(rows) == len(by) == 18 and set(by) == expected, 'missing/duplicate EN or ES variant'
    for kind in ('early', 'tax', 'dated'):
        for stage in ('cold', 'follow', 'final'):
            if (kind, stage) == ('dated', 'cold'):
                check_dated(rows)
                continue
            need = {'call', 'reply'}
            if stage == 'cold':
                need |= {'property_note', 'stale', 'advisor'}
                need |= {'no_date', 'handled'} if kind == 'early' else {'record_date', 'county_check'}
                if kind == 'tax': need.add('tax_sale')
                if kind == 'dated': need.add('handled')
            elif stage == 'follow': need |= {'follow', 'handled'}
            else: need |= {'final', 'no_more'}
            seen = []
            for idx, lang in enumerate(('en', 'es')):
                r = by[kind, stage, lang]
                body = r['body']
                assert body.rstrip().endswith(CLOSING[lang]), f'{kind}/{stage}/{lang}: altered opt-out closing'
                assert body.count(CLOSING[lang]) == 1, 'duplicate closing'
                assert disclaimer.identity(lang, as_html=False) in body, 'missing sender disclosure'
                concepts = {name for name, pair in CONCEPTS.items() if pair[idx].lower() in body.lower()}
                assert concepts == need, f'{kind}/{stage}/{lang}: concepts {concepts} != {need}'
                assert '305-555-0100' in body, 'missing synthetic callback number'
                assert '1 Example Street' in body, 'missing synthetic property reference'
                assert ('12/31/2099' in body) == (stage == 'cold' and kind != 'early'), 'date mismatch'
                seen.append(concepts)
            assert seen[0] == seen[1], f'{kind}/{stage}: EN/ES meaning differs'


class EmailVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = variants()

    def test_every_rendered_pair_has_matching_meaning_ask_and_exact_closing(self):
        check_matrix(self.rows)

    def test_missing_spanish_variant_is_rejected(self):
        for kind in ('early', 'tax', 'dated'):
            for stage in ('cold', 'follow', 'final'):
                with self.subTest(kind=kind, stage=stage):
                    with self.assertRaisesRegex(AssertionError, 'missing/duplicate'):
                        check_matrix([r for r in self.rows
                                      if (r['kind'], r['stage'], r['lang']) != (kind, stage, 'es')])

    def test_missing_or_altered_closing_is_rejected_in_every_variant(self):
        for i, row in enumerate(self.rows):
            for replacement in ('', 'altered closing'):
                with self.subTest(variant=i, replacement=replacement):
                    rows = copy.deepcopy(self.rows)
                    rows[i]['body'] = rows[i]['body'].replace(CLOSING[row['lang']], replacement)
                    with self.assertRaisesRegex(AssertionError, 'altered opt-out closing'):
                        check_matrix(rows)

    def test_dated_english_is_the_exact_approved_rendering(self):
        dated = next(r for r in self.rows if (r['kind'], r['stage'], r['lang']) == ('dated', 'cold', 'en'))
        self.assertEqual(dated['body'], approved_english())

    def test_dated_spanish_paragraphs_and_signature(self):
        check_dated(self.rows)
        # encoding='utf-8': cp1252 is the default on Windows and cannot decode byte 0x8d in the
        # template, so this check -- the one guarding the Spanish dated email's ${sigES} ending --
        # errored out on the laptop and only ever ran in CI.
        source = Path(__file__).with_name('tracker_template.html').read_text(encoding='utf-8')
        block = source.split('  const bodyES = td', 1)[1].split(';\n', 1)[0]
        self.assertRegex(block, r'\$\{sigES\}\\n\\n`$')

    def test_spanish_meaning_drift_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            if (row['kind'], row['stage'], row['lang']) == ('early', 'cold', 'es'):
                row['body'] = row['body'].replace('puede estar desactualizado', 'dato confirmado')
        with self.assertRaisesRegex(AssertionError, 'concepts'):
            check_matrix(rows)


if __name__ == '__main__':
    unittest.main()
