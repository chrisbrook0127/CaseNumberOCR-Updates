import unittest,tempfile,queue
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
import test_rules
m=test_rules.m

class CompletedExport(unittest.TestCase):
    app=test_rules.Rules.app
    def export_app(self,folder):
        a=self.app();a.folder=folder;a.queue=queue.Queue()
        a.status=Mock();a.completed_send_status=Mock()
        a._unsaved_completed_changes=Mock(return_value=[])
        a.tree=Mock();a.tree.selection.return_value=['review']
        a.completed_tree.get_children.return_value=['approved1','approved2']
        a.completed_checked_iids={'approved1'}
        a.records={}
        for name,case,approved in [('approved1','08-01114',True),('approved2','08-00101',True),('review','08-19',False)]:
            path=folder/(name+'.pdf');path.write_bytes(name.encode())
            a.records[name]={'path':path,'candidate':case,'confirmed':case if approved else '',
                 'manual_approved':approved,'auto_labeled':False,'exported':False}
        return a
    def test_send_all_approved_to_completed_and_leave_review(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.export_app(Path(d))
            class ImmediateThread:
                def __init__(self,target,args,**kwargs):self.target=target;self.args=args
                def start(self):self.target(*self.args)
            with patch.object(m.threading,'Thread',ImmediateThread):
                a.export_completed()
            self.assertTrue((a.folder/'COMPLETED'/'approved1_08-1114.pdf').exists())
            self.assertTrue((a.folder/'COMPLETED'/'approved2_08-101.pdf').exists())
            self.assertEqual((a.folder/'review.pdf').read_bytes(),b'review')
            self.assertFalse((a.folder/'08-19.pdf').exists())
            self.assertFalse((a.folder/'approved1.pdf').exists())
            self.assertFalse((a.folder/'approved2.pdf').exists())
    def test_export_waits_for_final_sequence_check(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.export_app(Path(d));a._sequence_review_pending=True
            a.export_completed()
            self.assertFalse((a.folder/'COMPLETED').exists())
            self.assertTrue((a.folder/'approved1.pdf').exists())
            self.assertFalse(a._eligible_for_kurt('approved1',a.records['approved1']))
    def test_completion_barrier_released_after_check(self):
        a=self.app();a._sequence_review_pending=True;a.scan_items=[]
        a._scan_progress_update=Mock()
        def check():self.assertTrue(a._sequence_review_pending)
        a._apply_sequence_consensus=Mock(side_effect=check)
        a._finish_scan_sequence_review()
        self.assertFalse(a._sequence_review_pending)
        a._apply_sequence_consensus.assert_called_once()
    def test_failed_sequence_check_keeps_export_blocked(self):
        a=self.app();a.scan_items=[];a._scan_progress_update=Mock()
        a._apply_sequence_consensus=Mock(side_effect=RuntimeError('failed check'))
        with self.assertRaises(RuntimeError):a._finish_scan_sequence_review()
        self.assertTrue(a._sequence_review_pending)
    def test_zeroes_and_collision_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.export_app(Path(d));dest=a.folder/"COMPLETED";dest.mkdir()
            (dest/'approved1_08-1114.pdf').write_bytes(b'prior')
            a._export_worker(dest,[('approved1',a.records['approved1']['path'],'08-01114')])
            self.assertEqual((dest/'approved1_08-1114.pdf').read_bytes(),b'prior')
            self.assertEqual((dest/'approved1_08-1114 (1).pdf').read_bytes(),b'approved1')
    def test_full_strip_keeps_digit_one(self):
        import numpy as np
        a=self.app();a.reader=Mock()
        a.reader.recognize.return_value=[(None,'08-0119',.99)]
        hits=a._recognize_case_strip(np.ones((25,100),dtype=np.uint8)*255)
        self.assertEqual(hits[0]['number'],'08-0119')
        self.assertEqual(hits[0]['engine_scores']['EasyOCR'],99)
        a.reader.readtext.assert_not_called()
    def test_full_strip_rejects_missing_prefix(self):
        import numpy as np
        a=self.app();a.reader=Mock();a.reader.recognize.return_value=[(None,'8-120',1)]
        self.assertEqual(a._recognize_case_strip(np.zeros((25,100),dtype=np.uint8)),[])

if __name__=='__main__':unittest.main()
