"""MFCase v5.6.10: v5.6.5 manual-boundary strategy with v5.6.9 OCR unchanged."""
import unittest,tempfile
from pathlib import Path
from unittest.mock import Mock
import case_number_ocr as m

def record(i,number='',mode='review'):
    return dict(path=Path('/tmp')/f'Image{i:04d}.pdf',
      original_name=f'Image{i:04d}.pdf',confirmed=number,candidate=number,
      manual_approved=mode in ('manual','auto'),auto_labeled=mode=='auto',
      sequence_inferred=mode=='inferred',scan_ambiguous=False,
      sequence_conflict=False,sequence_excluded=False,case_draft='',
      exported=False,status={'manual':'Manual verified','auto':'99.9% OCR approved',
             'review':'Needs review','inferred':'Before/after inferred'}[mode])
class FakeTree:
    def exists(self,iid):return False
    def delete(self,iid):pass
class AnchorRollback(unittest.TestCase):
    def setUp(self):
        a=self.a=m.App.__new__(m.App)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        a.folder=Path(self.tmp.name);a.records={};a.sent_history={}
        a.busy=True;a.export_busy=False;a._perf_stages={};a._diag_event=Mock()
        a.completed_tree=FakeTree();a._move_to_completed=Mock()
        a._insert_scan_row=Mock();a._save_state=Mock(return_value=True)
        a._update_completed_count=Mock();a._refresh_review_controls=Mock()
        a._rename_completed_in_place=Mock();a._last_sequence_signature=None
    def setup(self,items):
        a=self.a
        a.records={f'i{i}':record(i,*item) for i,item in enumerate(items)}
        for rec in a.records.values():rec['path']=a.folder/rec['original_name']
        return a.records
    def test_matching_manual_pair_overrides_wrong_auto_and_review(self):
        r=self.setup([('08-395','manual'),('08-704','auto'),('','review'),('08-395','manual')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),2)
        for i in ('i1','i2'):
            self.assertEqual(r[i]['confirmed'],'08-395')
            self.assertTrue(r[i]['sequence_inferred'])
            self.assertEqual(r[i]['sequence_anchors'],['i0','i3'])
    def test_matching_manual_pair_overrides_multiple_auto(self):
        r=self.setup([('08-395','manual'),('08-704','auto'),('08-477','auto'),('08-395','manual')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),2)
        self.assertTrue(all(r[i]['confirmed']=='08-395' for i in ('i1','i2')))
    def test_manual_before_auto_after(self):
        r=self.setup([('08-395','manual'),('','review'),('08-395','auto')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),1)
        self.assertEqual(r['i1']['confirmed'],'08-395')
    def test_auto_before_manual_after(self):
        r=self.setup([('08-395','auto'),('','review'),('08-395','manual')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),1)
        self.assertEqual(r['i1']['confirmed'],'08-395')
    def test_auto_pair_without_manual(self):
        r=self.setup([('08-395','auto'),('','review'),('08-395','auto')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),1)
        self.assertEqual(r['i1']['confirmed'],'08-395')
    def test_one_hundred_page_mixed_gap(self):
        r=self.setup([('08-395','manual')]+[('','review')]*100+[('08-395','auto')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),100)
        self.assertEqual(r['i100']['confirmed'],'08-395')
    def test_conflicting_manual_auto_pair_does_not_infer(self):
        r=self.setup([('08-395','manual'),('','review'),('08-396','auto')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),0)
        self.assertFalse(r['i1']['confirmed'])
    def test_manual_correction_is_untouchable(self):
        r=self.setup([('08-395','manual'),('08-396','manual'),('08-395','manual')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),0)
        self.assertEqual(r['i1']['confirmed'],'08-396')
    def test_inferred_case_cannot_anchor(self):
        r=self.setup([('08-395','manual'),('','review'),('08-395','inferred'),('','review'),('08-395','manual')])
        self.assertEqual(self.a._apply_sequence_consensus_impl(),2)
        self.assertIsNone(self.a._sequence_case_number(r['i2']))
    def test_immediate_sequence_rechecks_full_manual_gap(self):
        r=self.setup([('08-395','manual'),('08-704','auto'),('','review'),('08-395','manual')])
        self.assertEqual(self.a._incremental_sequence_check('i3'),2)
        self.assertEqual(r['i1']['confirmed'],'08-395')
        self.assertEqual(r['i2']['confirmed'],'08-395')
    def test_sent_manual_and_unsent_manual_bound_auto(self):
        r=self.setup([('08-704','auto'),('','review'),('08-395','manual')])
        for i in range(3):
            rec=r[f'i{i}'];rec['original_name']=f'Image{i+1:04d}.pdf'
            rec['path']=self.a.folder/rec['original_name']
        self.a.sent_history={'Image0000.pdf':dict(destination_name='Image0000_08-395.pdf',
               confirmed='08-395',manual_approved=True,auto_labeled=False,sequence_inferred=False)}
        self.assertEqual(self.a._apply_sequence_consensus_impl(),2)
        self.assertEqual(r['i0']['confirmed'],'08-395')
    def test_history_change_invalidates_incremental_signature(self):
        self.setup([('08-395','auto'),('','review'),('08-395','manual')])
        self.a._apply_sequence_consensus=Mock(return_value=3)
        self.assertEqual(self.a._incremental_sequence_check('i2'),3)
        self.assertEqual(self.a._incremental_sequence_check('i2'),0)
        self.a.sent_history['Image9999.pdf']=dict(destination_name='Image9999_08-395.pdf',
            confirmed='08-395',manual_approved=True,auto_labeled=False)
        self.assertEqual(self.a._incremental_sequence_check('i2'),3)
if __name__=='__main__':unittest.main()
