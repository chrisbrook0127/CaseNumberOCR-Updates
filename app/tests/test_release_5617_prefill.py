"""Previous 95%+ case is a suggestion, NEVER a completion (v5.6.17)."""
import unittest
from unittest.mock import Mock
import test_release_570 as anchors

class PreviousCasePrefill(unittest.TestCase):
    def setUp(self):
        t=anchors.AnchorRollback()
        t.setUp()
        self.addCleanup(t.tmp.cleanup)
        self.app=t.a
        self.helper=t
        self.app.busy=True
        self.app.scan_checked_iids=set()

    def records(self,values):
        r=self.helper.setup(values)
        for row in r.values():
            row['score']=99 if row['auto_labeled'] else 40
        return r

    def test_previous_case_prefills_in_review_but_never_completes(self):
        r=self.records([('08-15','auto'),('08-900','review'),('','review')])
        changed=self.app._apply_sequence_consensus_impl()
        self.assertEqual(changed,2)
        for key in ('i1','i2'):
            self.assertEqual(r[key]['candidate'],'08-15')
            self.assertEqual(r[key]['confirmed'],'')
            self.assertFalse(r[key]['manual_approved'])
            self.assertFalse(r[key]['auto_labeled'])
            self.assertFalse(r[key]['sequence_inferred'])
            self.assertTrue(r[key]['sequence_previous_prefill'])
            self.assertIsNone(self.app._sequence_case_number(r[key]))
        self.app._move_to_completed.assert_not_called()
        self.app._rename_completed_in_place.assert_not_called()
        self.assertEqual(self.app._apply_sequence_consensus_impl(),0)
        self.assertEqual(r['i1']['sequence_original_candidate'],'08-900')

    def test_exactly_75_percent_prefills_but_above_75_does_not(self):
        r=self.records([('08-20','auto'),('08-111','review'),('08-222','review')])
        r['i1']['score']=75
        r['i2']['score']=75.01
        self.assertEqual(self.app._apply_sequence_consensus_impl(),1)
        self.assertEqual(r['i1']['candidate'],'08-20')
        self.assertEqual(r['i2']['candidate'],'08-222')
        self.assertFalse(r['i1']['confirmed'])

    def test_matching_before_and_after_still_completes(self):
        r=self.records([('08-20','auto'),('','review'),('08-20','manual')])
        self.assertEqual(self.app._apply_sequence_consensus_impl(),1)
        self.assertEqual(r['i1']['confirmed'],'08-20')
        self.assertTrue(r['i1']['sequence_inferred'])
        self.app._move_to_completed.assert_called_once()

    def test_promote_prefill_only_when_matching_right_anchor_arrives(self):
        r=self.records([('08-20','manual'),('08-750','review')])
        r['i0']['score']=99
        self.app._apply_sequence_consensus_impl()
        self.assertFalse(r['i1']['confirmed'])
        r['i2']=dict(r['i0'],path=self.app.folder/'Image0002.pdf',original_name='Image0002.pdf')
        self.app._apply_sequence_consensus_impl()
        self.assertEqual(r['i1']['confirmed'],'08-20')
        self.assertTrue(r['i1']['sequence_inferred'])
        self.assertFalse(r['i1'].get('sequence_previous_prefill'))
        self.assertEqual(r['i1']['sequence_original_candidate'],'08-750')

    def test_new_conflicting_anchor_restores_original_suggestion(self):
        r=self.records([('08-20','auto'),('08-750','review')])
        self.app._apply_sequence_consensus_impl()
        r['i2']=dict(r['i0'],path=self.app.folder/'Image0002.pdf',original_name='Image0002.pdf',confirmed='08-21')
        self.app._apply_sequence_consensus_impl()
        self.assertEqual(r['i1']['candidate'],'08-750')
        self.assertFalse(r['i1']['confirmed'])
        self.assertFalse(r['i1']['sequence_previous_prefill'])

    def test_legacy_previous_case_approval_is_demoted_to_review(self):
        r=self.records([('08-20','auto'),('08-20','review')])
        r['i1'].update(confirmed='08-20',candidate='08-20',manual_approved=True,
                       auto_labeled=True,sequence_inferred=True,sequence_anchors=['i0'],
                       sequence_original_candidate='08-750',sequence_original_score=40)
        self.assertEqual(self.app._apply_sequence_consensus_impl(),1)
        self.assertFalse(r['i1']['confirmed'])
        self.assertFalse(r['i1']['manual_approved'])
        self.assertFalse(r['i1']['auto_labeled'])
        self.assertTrue(r['i1']['sequence_previous_prefill'])
        self.app._move_to_completed.assert_not_called()

if __name__=='__main__':unittest.main()
