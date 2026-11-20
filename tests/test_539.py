import unittest
from unittest.mock import Mock
import test_rules
m=test_rules.m

class PaddingIdentity(unittest.TestCase):
    app=test_rules.Rules.app
    sequence=test_rules.Rules.sequence

    def test_only_leading_padding_ignored(self):
        for raw,want in [('08-0084','08-84'),('08-01114','08-1114'),('08-0101','08-101'),('08-001000','08-1000'),('08-1010','08-1010'),('08-848484','08-848484'),('08-0000000','08-0')]:
            self.assertEqual(m._case_identity(raw),want)
            self.assertEqual(m.normalize_case_number(raw),raw)
        for bad in ['09-84','08-00000084','08-0I14','84','08-']:
            self.assertIsNone(m._case_identity(bad))

    def test_multiple_middle_files_and_both_anchor_directions(self):
        for left,right in [('08-0084','08-84'),('08-84','08-0084')]:
            a=self.sequence([left,'08-44',None,right])
            for i in ['1','2']:
                self.assertEqual(a.records[i]['confirmed'],'08-84')
                self.assertTrue(a.records[i]['sequence_inferred'])
            self.assertEqual(a.records['0']['confirmed'],left)
            self.assertEqual(a.records['3']['confirmed'],right)
            a._ocr_pdf=Mock(side_effect=AssertionError('Must not rescan'))
            self.assertEqual(a._apply_sequence_consensus(),0)
            a._ocr_pdf.assert_not_called()

    def test_padding_change_does_not_withdraw_but_real_change_does(self):
        a=self.sequence(['08-0084',None,'08-84'])
        a.records['0']['confirmed']='08-00084'
        self.assertEqual(a._apply_sequence_consensus(),0)
        a.records['0']['confirmed']='08-85'
        a._apply_sequence_consensus()
        self.assertFalse(a.records['1']['manual_approved'])

    def test_padding_does_not_make_unapproved_anchor(self):
        a=self.sequence(['08-0084',None,'08-84'],**{'0':{'manual_approved':False,'auto_labeled':False}})
        self.assertFalse(a.records['1'].get('manual_approved'))

    def test_manual_learning_keeps_exact_label(self):
        a=self.sequence(['08-0084',None,'08-84'])
        self.assertEqual(a.records['0']['candidate'],'08-0084')
        self.assertEqual(a._sequence_case_number(a.records['0']),'08-84')
        self.assertEqual(a.records['0']['confirmed'],'08-0084')
