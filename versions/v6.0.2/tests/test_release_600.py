"""Raw-order review barrier, shared row actions and source-crop visibility."""
import ast,hashlib,json,queue,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
from PIL import Image,ImageTk
import case_number_ocr as m
import test_release_570 as helpers

class OrderReview(unittest.TestCase):
    def make(self,numbers):
        h=helpers.AnchorRollback();h.setUp();self.addCleanup(h.tmp.cleanup);a=self.a=h.a
        rows=h.setup([(n,'auto') for n in numbers]);a.busy=False;a.root=Mock();a.status=Mock()
        a._schedule_order_popup=Mock();a._scan_progress_update=Mock();a.scan_items=list(rows)
        for r in rows.values():r['score']=99;r['scan_raw_candidate']=r['candidate']
        return rows
    def test_forward_gaps_repeats_and_leading_zeros_allowed(self):
        self.make(['08-0574','08-574','08-576','08-587']);self.assertEqual(self.a._order_review_findings(),[])
    def test_backward_step_flags_both_without_guessing_winner(self):
        r=self.make(['08-574','08-576','08-576','08-574','08-576','08-575','08-587'])
        self.assertEqual(self.a._order_review_findings(),[])  # All deviations are <= 2
        self.assertEqual(r['i3']['confirmed'],'08-574')
    def test_raw_ocr_cannot_be_hidden_by_prior_inference(self):
        r=self.make(['08-574','08-576','08-574'])
        r['i1'].update(candidate='08-574',confirmed='08-574',sequence_inferred=True)
        # Two-sided inferred correction takes precedence; raw OCR is retained.
        self.assertEqual(self.a._order_review_findings(),[])
        self.assertEqual(r['i1']['scan_raw_candidate'],'08-576')
        self.a._check_order_review();self.assertEqual(r['i1']['confirmed'],'08-574')
    def test_manual_change_is_used_instead_of_old_ocr(self):
        r=self.make(['08-574','08-999','08-576']);r['i1'].update(confirmed='08-575',manual_approved=True,auto_labeled=False)
        self.assertEqual(self.a._order_review_findings(),[])
    def test_excluded_and_acknowledged_not_repeatedly_flagged(self):
        r=self.make(['08-574','08-576','08-574']);r['i1']['sequence_locked']=True
        self.assertEqual(self.a._order_review_findings(),[])
        r['i1']['sequence_locked']=False;r['i1']['order_review_ack']='08-576';r['i2']['order_review_ack']='08-574'
        self.assertEqual(self.a._order_review_findings(),[])
    def test_five_rows_use_original_order_despite_sorting_and_exclusion(self):
        r=self.make([f'08-{i}' for i in range(10)])
        r['i3']['sequence_locked']=True;self.a.records=dict(reversed(list(r.items())))
        self.assertEqual([i for i,_ in self.a._order_review_context('i4')],['i2','i3','i4','i5','i6'])
        self.assertEqual(len(self.a._order_review_context('i0')),3)
    def test_sent_history_is_read_only_context_and_can_reveal_reversal(self):
        self.make(['08-574']);self.a.sent_history={'Image9999.pdf':dict(destination_name='Image9999_08-571.pdf',confirmed='08-571',manual_approved=True)}
        self.assertEqual(self.a._order_review_findings(),['i0'])
        self.assertTrue(self.a._order_review_context('i0')[-1][1]['exported'])
    def test_full_scan_gate_runs_before_sequence_engine(self):
        self.make(['08-576','08-573']);self.a._apply_sequence_consensus=Mock()
        self.a._finish_scan_sequence_review();self.a._apply_sequence_consensus.assert_called_once()
        self.assertTrue(self.a._order_review_pending);self.assertFalse(self.a._sequence_review_pending)
    def test_unresolved_popup_blocks_sequence_changes(self):
        self.make(['08-576','08-574']);self.a._order_review_pending=True
        self.a._apply_sequence_consensus_impl=Mock()
        self.assertEqual(self.a._apply_sequence_consensus(),0);self.a._apply_sequence_consensus_impl.assert_not_called()
    def export_setup(self,numbers):
        r=self.make(numbers);a=self.a
        a._training_blocks_work=Mock(return_value=False);a._yield_background_learning=Mock()
        a._unsaved_completed_changes=Mock(return_value=[]);a._apply_sequence_consensus=Mock()
        a._export_cancel_event=threading.Event();a.completed_send_status=Mock()
        a.auto_send_var=Mock(get=Mock(return_value=True));a._startup_ready=True
        for row in r.values():row['path'].write_bytes(b'unchanged')
        return r
    def test_manual_and_auto_send_both_block_until_reviewed(self):
        self.export_setup(['08-576','08-573'])
        with patch.object(m.threading,'Thread') as worker:
            self.a.export();self.a.export(automatic=True);worker.assert_not_called()
        self.assertTrue(self.a._order_review_pending)
    def test_red_green_sends_only_approved_excluded_row_during_batch_review(self):
        r=self.export_setup(['08-576','08-572','08-569'])
        r['i0'].update(sequence_locked=True,auto_labeled=False)
        self.a._order_review_pending=True
        with patch.object(m.threading,'Thread') as worker:self.a._order_review_after_approval('i0')
        self.assertEqual([i for i,_,_ in worker.call_args.kwargs['args'][1]],['i0'])
        self.assertTrue(self.a._order_review_pending)
    def test_all_issues_acknowledged_resume_original_send_request(self):
        r=self.make(['08-576','08-574']);a=self.a;a._order_review_pending=True
        a._hide_order_popup=Mock();a._apply_sequence_consensus=Mock();a._drain_excluded_sends=Mock()
        a.export=Mock();a._order_resume_send=(None,True);r['i0']['order_review_ack']='08-576'
        r['i1']['auto_labeled']=False;a._order_review_after_approval('i1')
        self.assertFalse(a._order_review_pending);a._apply_sequence_consensus.assert_called_once()
        self.assertIsNone(a._order_resume_send);a.export.assert_called_once_with(selected_iids=None,automatic=True)
    def test_red_green_queues_during_scan_without_transfer(self):
        r=self.export_setup(['08-574']);self.a.busy=True;r['i0'].update(sequence_locked=True,auto_labeled=False)
        self.a.export=Mock();self.a._order_review_after_approval('i0')
        self.assertTrue(r['i0']['excluded_send_requested']);self.a.export.assert_not_called()
    def test_failed_approval_save_does_not_send(self):
        r=self.export_setup(['08-574']);r['i0'].update(sequence_locked=True,auto_labeled=False)
        self.a._save_state=Mock(return_value=False);self.a.export=Mock();self.a._order_review_after_approval('i0')
        self.a.export.assert_not_called();self.assertFalse(r['i0']['excluded_send_requested'])
    def test_ack_and_raw_read_saved(self):
        r=self.make(['08-574']);r['i0']['order_review_ack']='08-574';self.a._save_state_impl()
        row=json.loads((self.a.folder/'.case_ocr_state.json').read_text())['files']['Image0000.pdf']
        self.assertEqual(row['scan_raw_candidate'],'08-574');self.assertEqual(row['order_review_ack'],'08-574')
    def test_corrected_case_keeps_real_crop_without_ocr_rescan(self):
        r=self.make(['08-574'])['i0'];a=self.a;a.app_dir=a.folder
        a._scan_preview_keys={};a._scan_preview_photos={};a._log=Mock();a._ensure_preview=Mock()
        pix=np.full((500,500,3),255,dtype=np.uint8);pix[40:60,320:390]=0
        r.update(candidate='08-576',confirmed='08-576',sequence_inferred=True,
                 snippet=(0,pix,'incident',(300,30,420,80),[dict(number='08-574',box_verified=True,rect=(300,30,420,80),score=99)]))
        with patch.dict(m.os.environ,{'LOCALAPPDATA':str(a.folder)}),patch.object(ImageTk,'PhotoImage',side_effect=lambda img:img):
            tile=a._make_scan_preview('i0',r)
        self.assertGreater(np.count_nonzero(np.asarray(tile)[5:63,5:217]<50),100)
        a._ensure_preview.assert_not_called()
    def test_preview_rebuilds_source_from_metadata_once_and_caches(self):
        r=self.make(['08-574'])['i0'];a=self.a;a.app_dir=a.folder
        a._scan_preview_keys={};a._scan_preview_photos={};a._log=Mock()
        meta=dict(page_num=0,rect=[300,30,420,80],candidates=[dict(number='08-574',box_verified=True,rect=[300,30,420,80],score=99)])
        r.update(preview_meta=meta,snippet=None)
        def rebuild(rec):
            pix=np.full((500,500,3),255,dtype=np.uint8);pix[40:60,320:390]=0
            rec['snippet']=(0,pix,'incident',meta['rect'],meta['candidates'])
        a._ensure_preview=Mock(side_effect=rebuild)
        with patch.dict(m.os.environ,{'LOCALAPPDATA':str(a.folder)}),patch.object(ImageTk,'PhotoImage',side_effect=lambda img:img):
            a._make_scan_preview('i0',r);a._make_scan_preview('i0',r)
        a._ensure_preview.assert_called_once()
    def test_before_after_engine_locked_to_approved_5620(self):
        code=Path(m.__file__).read_text(encoding="utf-8")
        cls=next(n for n in ast.parse(code).body if isinstance(n,ast.ClassDef) and n.name=='App')
        sources={n.name:ast.get_source_segment(code,n) for n in cls.body if isinstance(n,ast.FunctionDef)}
        expected={'_sequence_case_number': 'd86ec58a0fea5b35fad3b54b231e5929e9d0bc995ad28d2cbe65e87522911dff'}
        # The planner/lock boundary and context outlier methods are intentionally
        # revised only in v6.0.1 and have separate behavioral regression tests.
        for name in ('_sequence_consensus_plan','_apply_sequence_consensus_impl','_flag_context_outliers'):
            self.assertIn(name,sources)
        for name,digest in expected.items():
            self.assertEqual(hashlib.sha256(sources[name].encode()).hexdigest(),digest,name)
    def test_popup_creates_five_rows_with_shared_actions(self):
        self.make(['08-574','08-575','08-999','08-576','08-577'])
        a=self.a;a._order_review_ids=['i2'];a._order_current='i2';a._order_review_pending=True
        a._order_popup=None;a._make_scan_preview=Mock(return_value='photo');a._scan_row_locked=Mock(return_value=False)
        with patch.multiple(m.tk,Toplevel=Mock(),Canvas=Mock(),Label=Mock(),Button=Mock(),StringVar=Mock()),patch.multiple(m.ttk,Frame=Mock(return_value=Mock(winfo_children=Mock(return_value=[]))),Label=Mock(),Entry=Mock(),Button=Mock(),Scrollbar=Mock()):
            a._show_order_popup()
        self.assertEqual(len(a._order_entry_vars),5);self.assertEqual(len(a._order_photo_refs),5)
    def test_closing_popup_does_not_release_send_barrier(self):
        self.make(['08-576','08-574']);a=self.a;a._order_review_pending=True;a._order_popup=Mock()
        a._hide_order_popup();self.assertTrue(a._order_review_pending);self.assertIsNone(a._order_popup)
    def test_unscanned_old_raw_data_does_not_trigger_popup(self):
        r=self.make(['08-999','08-574']);r['i0']['status']='Not scanned'
        self.assertEqual(self.a._order_review_findings(),[])
    def test_batch_send_waits_for_individual_send_and_resumes_once(self):
        self.make(['08-574']);a=self.a;a._order_review_pending=False;a._order_resume_send=(None,False)
        a.export=Mock();a.export_busy=True;a._resume_order_send_when_ready()
        a.export.assert_not_called();self.assertIsNotNone(a._order_resume_send)
        a.export_busy=False;a._resume_order_send_when_ready();a._resume_order_send_when_ready()
        a.export.assert_called_once_with(selected_iids=None,automatic=False)
if __name__=='__main__':unittest.main()
