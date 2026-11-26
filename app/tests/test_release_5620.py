"""Per-file sequence exclusion and isolated transfer regressions."""
import json,queue,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import case_number_ocr as m
import test_release_570 as helpers

class ExclusionAndSend(unittest.TestCase):
    def setUp(self):
        h=helpers.AnchorRollback();h.setUp();self.addCleanup(h.tmp.cleanup);self.a=h.a
        self.r=h.setup([('08-574','manual'),('08-576','auto'),('','review'),('08-574','manual')])
        for r in self.r.values():r['score']=99
    def test_locked_auto_preserved_and_other_files_inferred(self):
        self.r['i1']['sequence_locked']=True
        self.a._apply_sequence_consensus_impl()
        self.assertEqual(self.r['i1']['confirmed'],'08-576')
        self.assertEqual(self.r['i2']['confirmed'],'08-574')
        self.assertEqual(self.r['i2']['sequence_anchors'],['i0','i3'])
        self.assertIsNone(self.a._sequence_case_number(self.r['i1']))
    def test_locked_manual_cannot_anchor(self):
        self.r['i0']['sequence_locked']=True
        self.assertIsNone(self.a._sequence_case_number(self.r['i0']))
        self.assertNotIn('i0',[i for i,_ in self.a._sequence_order_with_sent_history()])
    def test_locked_review_cannot_be_completed_by_inference(self):
        self.r['i2']['sequence_locked']=True
        self.a._apply_sequence_consensus_impl();self.assertFalse(self.r['i2']['manual_approved'])
        self.assertEqual(self.r['i2']['confirmed'],'')
    def test_direct_plan_and_context_cannot_change_locked_record(self):
        self.r['i1']['sequence_locked']=True
        plan,_=self.a._sequence_consensus_plan(list(self.r.items()))
        self.assertNotIn(1,plan)
        self.a._flag_context_outliers(list(self.r.items()))
        self.assertEqual(self.r['i1']['confirmed'],'08-576')
    def test_sent_exclusion_survives_real_history_reload(self):
        completed=self.a.folder/'COMPLETED';completed.mkdir();(completed/'Image0001_08-576.pdf').write_bytes(b'pdf-fixture')
        row=dict(destination_name='Image0001_08-576.pdf',confirmed='08-576',manual_approved=True,sequence_locked=True)
        self.a.sent_history={'Image0001.pdf':row};self.a._save_sent_history_impl()
        self.a.sent_history={};self.a._load_sent_history_impl();self.a.records={}
        self.assertTrue(self.a.sent_history['Image0001.pdf']['sequence_locked'])
        self.assertEqual(self.a._sequence_order_with_sent_history(),[])
    def test_state_preserves_exclusion_flag(self):
        self.r['i1']['sequence_locked']=True;self.a._save_state_impl()
        cfg=json.loads((self.a.folder/'.case_ocr_state.json').read_text())
        self.assertTrue(cfg['files']['Image0001.pdf']['sequence_locked'])
    def test_toggle_does_not_approve_and_unlock_restores_anchor(self):
        a=self.a;a._scan_row_locked=Mock(return_value=False);a.status=Mock()
        a._recheck_after_decision=Mock();a._schedule_scan_preview_refresh=Mock()
        a._toggle_sequence_lock('i1');self.assertIsNone(a._sequence_case_number(self.r['i1']))
        a._toggle_sequence_lock('i1');self.assertEqual(a._sequence_case_number(self.r['i1']),'08-576')
        a._toggle_sequence_lock('i2');self.assertFalse(self.r['i2']['manual_approved'])
    def test_failed_save_reverts_checkbox(self):
        a=self.a;a._scan_row_locked=Mock(return_value=False);a._save_state=Mock(return_value=False);a.status=Mock()
        a._toggle_sequence_lock('i1');self.assertFalse(self.r['i1']['sequence_locked'])
    def test_row_send_rejects_unapproved_and_invalid_prefix(self):
        a=self.a;a.busy=False;a.status=Mock();a.export=Mock()
        a._send_scan_row('i2');a.export.assert_not_called()
        self.r['i1']['confirmed']='576';a._send_scan_row('i1');a.export.assert_not_called()
    def test_selected_send_moves_only_requested_pdf_and_keeps_bytes(self):
        a=self.a;a.busy=False;a._training_blocks_work=Mock(return_value=False)
        a._yield_background_learning=Mock();a._unsaved_completed_changes=Mock(return_value=[])
        a._apply_sequence_consensus=Mock();a._export_cancel_event=threading.Event()
        a.queue=queue.Queue();a.status=Mock();a.completed_send_status=Mock()
        self.r['i1']['sequence_locked']=True
        for r in self.r.values():r['path'].write_bytes(b'original-pdf')
        with patch.object(m.threading,'Thread') as worker:
            a.export(selected_iids=['i1'])
        args=worker.call_args.kwargs['args'];self.assertEqual([x[0] for x in args[1]],['i1'])
        a._export_worker(*args)
        self.assertFalse(self.r['i1']['path'].exists());self.assertTrue(self.r['i0']['path'].exists())
        dest=a.folder/'COMPLETED'/'Image0001_08-576.pdf';self.assertEqual(dest.read_bytes(),b'original-pdf')
    def test_main_send_still_includes_all_approved(self):
        a=self.a;a.busy=False;a._training_blocks_work=Mock(return_value=False)
        a._yield_background_learning=Mock();a._unsaved_completed_changes=Mock(return_value=[])
        a._apply_sequence_consensus=Mock();a._export_cancel_event=threading.Event();a.status=Mock();a.completed_send_status=Mock()
        for r in self.r.values():r['path'].write_bytes(b'pdf')
        with patch.object(m.threading,'Thread') as worker:a.export()
        self.assertEqual([x[0] for x in worker.call_args.kwargs['args'][1]],['i0','i1','i3'])
if __name__=='__main__':unittest.main()
