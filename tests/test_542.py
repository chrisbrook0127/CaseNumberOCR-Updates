import unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
import test_rules,test_export_completed
m=test_rules.m

class Fixes542(unittest.TestCase):
    app=test_rules.Rules.app
    sequence=test_rules.Rules.sequence
    def test_hallucinated_prefix_on_bare_digits_rejected(self):
        a=self.app();a.reader=Mock();a.reader.readtext.return_value=[(None,'211',.99)]
        hit={'number':'08-111','rect':(0,0,40,30),'score':100,'engines':['TrOCR']}
        self.assertEqual(a._prefix_verified_hits(np.zeros((40,50,3),np.uint8),[hit]),[])
        self.assertNotIn('allowlist',a.reader.readtext.call_args.kwargs)
    def test_real_prefix_in_candidate_crop_required(self):
        a=self.app();a.reader=Mock()
        for text,valid in [('08-136',True),('INCIDENT NO. 08-136',True),('136',False),('208-136',False),('date 08-136',False)]:
            a.reader.readtext.return_value=[(None,text,.80)]
            hits=a._prefix_verified_hits(np.zeros((40,80,3),np.uint8),[{'number':'08-136','rect':(0,0,80,40)}])
            self.assertEqual(bool(hits),valid,text)
            if hits:self.assertTrue(hits[0]['prefix_verified'])
    def test_bare_digits_never_trigger_trocr_box_guess(self):
        a=self.app();a.reader=Mock();a.reader.readtext.return_value=[([[0,0],[40,0],[40,30],[0,30]],'211',.9)]
        a._trocr_read_scored=Mock(return_value=('08-111',100));a._recognize_case_strip=Mock(return_value=[])
        self.assertEqual(a._find_case_candidates(np.zeros((40,80,3),np.uint8)),[])
        a._trocr_read_scored.assert_not_called()
    def test_sent_manual_anchor_still_fills_two_middle_files(self):
        a=self.sequence(['08-0084',None,None,'08-85'])
        a.records['0'].update(path=a.folder/'COMPLETED'/'000_08-84.pdf',exported=True,auto_labeled=False,original_name='000.pdf')
        a.records['3'].update(confirmed='08-84',manual_approved=True,auto_labeled=False)
        a._apply_sequence_consensus()
        self.assertEqual([a.records[k]['confirmed'] for k in ['1','2']],['08-84','08-84'])
        self.assertFalse(a._sequence_case_number(a.records['1']))
    def test_green_check_sends_only_approved_row(self):
        with tempfile.TemporaryDirectory() as d:
            a=test_export_completed.CompletedExport().export_app(Path(d))
            a._export_in_flight_iids=set();a.completed_edit_drafts={}
            a._completed_thumbnail_path=Mock(return_value=None);a._save_learning_example=Mock();a._ensure_preview=Mock()
            a._close_completed_edit=Mock();a._insert_completed_row=Mock()
            a.records['review']['candidate']='08-00136'
            class Inline:
                def __init__(self,target,args,**kwargs):self.target=target;self.args=args
                def start(self):self.target(*self.args)
            with patch.object(m.threading,'Thread',Inline):a._quick_approve('review')
            self.assertEqual((Path(d)/'COMPLETED'/'review_08-136.pdf').read_bytes(),b'review')
            self.assertTrue(a.records['approved1']['path'].is_file())
            self.assertFalse((Path(d)/'review.pdf').exists())
    def test_sort_is_natural_and_does_not_change_anchor_order(self):
        a=self.app();a._schedule_scan_preview_refresh=Mock()
        class Tree:
            rows=['ten','two','one']
            values={'ten':'Image10.pdf','two':'Image2.pdf','one':'Image1.pdf'}
            def get_children(self,*args):return tuple(self.rows)
            def set(self,iid,col):return self.values[iid]
            def move(self,iid,parent,index):self.rows.remove(iid);self.rows.insert(index,iid)
        a.tree=Tree();a.scan_items=['original order']
        a._sort_file_column(a.tree,'file');self.assertEqual(a.tree.rows,['one','two','ten'])
        a._sort_file_column(a.tree,'file');self.assertEqual(a.tree.rows,['ten','two','one'])
        self.assertEqual(a.scan_items,['original order'])
