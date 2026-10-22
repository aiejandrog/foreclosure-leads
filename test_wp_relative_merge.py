"""Synthetic cache fixtures: retain numbers across repeated relative records."""
import unittest
import wp_contacts as W


def extract(relatives, owner_numbers=()):
    records = [{'response': [{'name': 'Jose Garcia', 'relatives': rs} for rs in relatives]}]
    return W.extract_relatives('GARCIA, JOSE', '123 Example St', records, owner_numbers)


class RelativeMergeTests(unittest.TestCase):
    def test_name_only_record_does_not_hide_later_phone(self):
        got = extract([[{'name': 'Maria Garcia'}],
                       [{'name': 'MARIA GARCIA', 'relation': 'spouse',
                         'phones': [{'number': '3055550102'}]}]])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['phones'][0]['n'], '3055550102')
        self.assertEqual(got[0]['rel'], 'spouse')

    def test_union_dedup_cap_and_owner_number_exclusion(self):
        got = extract([[{'name': 'Maria Garcia', 'phones': [{'number': '3055550101'}]}],
                       [{'name': 'Maria Garcia', 'phones': [{'number': f'305555010{i}'}
                                                          for i in range(1, 7)]}]], ['3055550102'])
        self.assertEqual([p['n'] for p in got[0]['phones']],
                         ['3055550101', '3055550103', '3055550104'])

    def test_number_beyond_cap_can_belong_to_another_relative(self):
        got = extract([[{'name': 'Maria Garcia', 'phones': [{'number': f'305555010{i}'}
                                                          for i in range(1, 5)]},
                        {'name': 'Pedro Garcia', 'phones': [{'number': '3055550104'}]}]])
        self.assertEqual(got[1]['phones'][0]['n'], '3055550104')


if __name__ == '__main__':
    unittest.main()
