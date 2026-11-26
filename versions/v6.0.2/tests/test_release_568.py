"""Regression tests: nearest independently completed anchors regardless of approval type."""
import unittest
from pathlib import Path
from unittest.mock import Mock
import case_number_ocr as m

def row(case='',mode='review',name='',**kwargs):
    record=dict(
      original_name=name,path=Path('/tmp')/name,confirmed=case,candidate=case,
      manual_approved=(mode=='manual'),auto_labeled=(mode=='auto'),
      sequence_inferred=(mode=='inferred'),sequence_conflict=False,
      scan_ambiguous=False,sequence_excluded=False,case_draft='',exported=False,
      status=('Manual verified' if mode=='manual' else
              'Auto verified' if mode=='auto' else 'Needs review'))
    record.update(kwargs)
    return record

def ordered(entries):
    return [(f'i{i}',row(*entry,name=f'Image{i}.pdf')) for i,entry in enumerate(entries)]

class AbsoluteNearestAnchors(unittest.TestCase):
    def setUp(self):
        self.app=m.App.__new__(m.App)
    def plan(self,entries):
        return self.app._sequence_consensus_plan(ordered(entries))
    def test_nearer_auto_conflict_blocks_matching_farther_human_pair(self):
        plan,conflicts=self.plan([('08-50','manual'),('','review'),
               ('08-60','auto'),('','review'),('08-50','manual')])
        self.assertEqual(plan[1],('08-50',['i0','i4']))
        self.assertEqual(plan[2],('08-50',['i0','i4']))
        self.assertEqual(plan[3],('08-50',['i0','i4']))
        self.assertFalse(conflicts)
    def test_nearest_auto_pair_can_infer_even_with_distant_humans(self):
        plan,conflicts=self.plan([('08-25','manual'),('08-57','auto'),
                ('','review'),('08-57','auto'),('08-25','manual')])
        self.assertEqual(plan[2],('08-25',['i0','i4']))
        self.assertFalse(conflicts)
    def test_automatic_independent_anchor_never_overridden(self):
        plan,conflicts=self.plan([('08-57','manual'),('','review'),
                 ('08-58','auto'),('','review'),('08-57','manual')])
        self.assertNotIn(2,plan)  # Valid 08-58 is only one case above matching anchors
        self.assertNotIn(2,conflicts)
    def test_manual_and_auto_have_equal_priority(self):
        plan,conflicts=self.plan([('08-1','manual'),('','review'),
                ('08-2','auto'),('','review'),('08-2','manual'),
                ('','review'),('08-3','auto')])
        self.assertEqual(plan[3],('08-2',['i2','i4']))
        self.assertNotIn(1,plan)
        self.assertNotIn(5,plan)
    def test_inferred_never_anchors_another_inference(self):
        plan,_=self.plan([('08-73','auto'),('','review'),
              ('08-73','inferred'),('','review'),('08-73','manual')])
        self.assertEqual(plan[1][1],['i0','i4'])
        self.assertEqual(plan[3][1],['i0','i4'])
    def test_one_hundred_file_gap(self):
        plan,_=self.plan([('08-84','auto')]+[('','review')]*100+[('08-84','manual')])
        self.assertEqual(len(plan),100)
        self.assertEqual(plan[50],('08-84',['i0','i101']))
    def test_only_one_boundary_cannot_infer(self):
        plan,_=self.plan([('','review'),('08-84','auto'),('','review')])
        self.assertFalse(plan)
    def test_incremental_uses_closest_automatic_before_human(self):
        a=self.app
        order=ordered([('08-50','manual'),('','review'),('08-60','auto'),
                 ('','review'),('08-60','auto'),('','review'),('08-50','manual')])
        a.folder=Path('/tmp');a.records=dict(order)
        a._sequence_order_with_sent_history=lambda:order
        a._apply_sequence_consensus=Mock(return_value=2)
        a._last_sequence_signature=None
        self.assertEqual(a._incremental_sequence_check('i4'),2)
        a._apply_sequence_consensus.assert_called_once_with(affected_range=(0,7))
    def test_sent_history_as_nearest_automatic_anchor(self):
        order=ordered([('08-61','auto'),('','review'),('','review'),('08-61','manual')])
        order[0][1].update(exported=True,status='Sent to COMPLETED (history)')
        plan,_=self.app._sequence_consensus_plan(order)
        self.assertEqual(plan[1],('08-61',['i0','i3']))
        self.assertEqual(plan[2],('08-61',['i0','i3']))
    def test_zero_padding_does_not_break_case_identity(self):
        plan,_=self.plan([('08-0084','auto'),('','review'),('08-84','manual')])
        self.assertEqual(m._case_identity(plan[1][0]),'08-84')

if __name__=='__main__':
    unittest.main(verbosity=2)
