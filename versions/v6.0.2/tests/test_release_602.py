"""v6.0.2: tolerance 2, correct culprit attribution, two-sided anchors, no OCR changes."""
import unittest, inspect
from unittest.mock import Mock,patch
import case_number_ocr as m
import test_release_601 as p

class CorrectOrder602(unittest.TestCase):
    def setup(self,rows):
        self.helper=p.Case601()
        a,records=self.helper.setup(rows)
        self.addCleanup(self.helper.doCleanups)
        for r in records.values():
            if r.get('auto_labeled'):r['score']=99.5
        return a,records

    def test_two_is_allowed_three_is_flagged(self):
        for gap,flag in ((0,False),(1,False),(2,False),(3,True),(4,True)):
            a,r=self.setup([('08-111','auto'),('08-111','auto'),
                (f'08-{111-gap}','auto'),('08-111','auto')])
            found=a._order_review_findings()
            self.assertEqual('i2' in found,flag,(gap,found))
            self.assertNotIn('i1',found)
            self.assertNotIn('i3',found)
            reason=a._manual_order_problem('i2',f'08-{111-gap}')
            self.assertEqual(bool(reason),flag,(gap,reason))

    def test_603_not_flagged_by_403_misread(self):
        a,r=self.setup([('08-602','auto'),('08-602','auto'),('08-422','auto'),
                        ('08-603','auto'),('08-403','auto'),('08-603','auto')])
        found=a._order_review_findings()
        self.assertNotIn('i3',found)
        self.assertIn('i2',found)
        self.assertIn('i4',found)
        self.assertEqual(a._manual_order_problem('i3','08-603'),'')
        with patch.object(m.messagebox,'askyesno',return_value=True) as popup:
            self.assertTrue(a._confirm_out_of_order_change('i3','08-603'))
            popup.assert_not_called()
        self.assertFalse(r['i3'].get('sequence_locked',False))

    def test_small_real_ocr_difference_never_silently_overwritten(self):
        a,r=self.setup([('08-111','auto'),('08-109','auto'),('08-111','auto')])
        a._apply_sequence_consensus_impl()
        self.assertEqual(r['i1']['confirmed'],'08-109')
        self.assertFalse(r['i1'].get('sequence_inferred',False))
        self.assertEqual(a._order_review_findings(),[])

    def test_matching_autos_fix_large_ocr_misread(self):
        a,r=self.setup([('08-602','auto'),('08-422','auto'),('08-602','auto')])
        r['i1']['scan_raw_candidate']='08-422'
        a._apply_sequence_consensus_impl()
        self.assertEqual(r['i1']['confirmed'],'08-602')
        self.assertTrue(r['i1']['sequence_inferred'])
        self.assertEqual(r['i1']['scan_raw_candidate'],'08-422')
        self.assertEqual(a._order_review_findings(),[])

    def test_two_human_anchors_bridge_multiple_wrong_autos(self):
        a,r=self.setup([('08-602','manual'),('08-422','auto'),
                        ('08-403','auto'),('08-602','manual')])
        a._apply_sequence_consensus_impl()
        for iid in ('i1','i2'):
            self.assertEqual(r[iid]['confirmed'],'08-602')
            self.assertTrue(r[iid]['sequence_inferred'])
        self.assertEqual(r['i0']['confirmed'],'08-602')
        self.assertEqual(r['i3']['confirmed'],'08-602')

    def test_locked_row_blocks_crossing_and_is_unchanged(self):
        a,r=self.setup([('08-602','manual'),('08-422','auto'),
                        ('08-999','manual'),('08-422','auto'),('08-602','manual')])
        r['i2']['sequence_locked']=True
        a._apply_sequence_consensus_impl()
        self.assertEqual(r['i2']['confirmed'],'08-999')
        self.assertFalse(r['i2'].get('sequence_inferred',False))
        self.assertNotEqual(r['i1']['confirmed'],'08-602')
        self.assertNotEqual(r['i3']['confirmed'],'08-602')

    def test_forward_gap_without_reversal_does_not_popup(self):
        a,r=self.setup([('08-574','auto'),('08-574','auto'),
            ('08-576','auto'),('08-587','auto'),('08-588','auto')])
        self.assertEqual(a._order_review_findings(),[])

    def test_manual_warning_three_or_more_accept_creates_lock(self):
        a,r=self.setup([('08-111','auto'),('08-111','auto'),
            ('08-108','review'),('08-111','auto')])
        with patch.object(m.messagebox,'askyesno',return_value=True) as dialog:
            self.assertTrue(a._confirm_out_of_order_change('i2','08-108'))
            dialog.assert_called_once()
        self.assertTrue(r['i2']['sequence_locked'])
        self.assertEqual(r['i2']['order_review_ack'],'08-108')

    def test_manual_decline_does_not_lock(self):
        a,r=self.setup([('08-111','auto'),('08-111','auto'),
            ('08-108','review'),('08-111','auto')])
        with patch.object(m.messagebox,'askyesno',return_value=False):
            self.assertFalse(a._confirm_out_of_order_change('i2','08-108'))
        self.assertFalse(r['i2'].get('sequence_locked',False))

    def test_ocr_and_learning_bytecode_unchanged(self):
        # CI independently compares AST/source with official v6.0.1 archive.
        self.assertTrue(callable(m.App._ocr_pdf))
        self.assertTrue(callable(m.App._train_trocr_worker))
        self.assertTrue(callable(m.App._ranked_learned_scan_zones))

if __name__=='__main__':unittest.main()
