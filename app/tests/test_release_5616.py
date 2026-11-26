"""Focused regression checks for context, approval, cancellation and resume."""
import queue,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import case_number_ocr as m
import test_release_570 as anchors
class Release5616(unittest.TestCase):
    def records(self,values):
        h=anchors.AnchorRollback();h.setUp();self.addCleanup(h.tmp.cleanup)
        self.a=h.a;r=h.setup(values)
        for row in r.values():row['score']=99
        return r
    def test_outlier_high_confidence_review_no_guess(self):
        r=self.records([('08-39','auto'),('08-111','auto'),('08-44','auto')]);self.a.busy=False
        self.a._apply_sequence_consensus_impl()
        self.assertEqual(r['i1']['confirmed'],'');self.assertEqual(r['i1']['candidate'],'08-111');self.assertEqual(r['i1']['score'],99)
        self.assertTrue(r['i1']['sequence_conflict']);self.assertEqual(r['i0']['confirmed'],'08-39');self.assertEqual(r['i2']['confirmed'],'08-44')
    def test_manual_and_matching_boundaries_preserved(self):
        r=self.records([('08-0039','auto'),('08-111','auto'),('08-39','auto')]);self.a.busy=False
        self.a._apply_sequence_consensus_impl();self.assertEqual(r['i1']['confirmed'],'08-39')
        r=self.records([('08-39','auto'),('08-111','manual'),('08-44','auto')]);self.a.busy=False
        self.a._apply_sequence_consensus_impl();self.assertEqual(r['i1']['confirmed'],'08-111')
    def test_context_waits_until_scan_end(self):
        r=self.records([('08-39','auto'),('08-111','auto'),('08-44','auto')])
        self.a._apply_sequence_consensus_impl();self.assertEqual(r['i1']['confirmed'],'08-111')
    def test_window_ten_positions_not_ten_anchors(self):
        self.records([('08-39','auto')]+[('','review')]*10+[('08-111','auto'),('08-44','auto')])
        self.assertEqual(self.a._flag_context_outliers(self.a._sequence_order_with_sent_history()),0)
    def test_medians_do_not_use_bad_nearest_as_boundary(self):
        r=self.records([(f'08-{n}','auto') for n in (37,38,39,111,44,45,46)])
        self.assertEqual(self.a._flag_context_outliers(self.a._sequence_order_with_sent_history()),1)
        self.assertTrue(r['i3']['sequence_conflict']);self.assertEqual(r['i4']['confirmed'],'08-44')
    def test_low_confidence_and_inferred_cannot_anchor(self):
        r=self.records([('08-39','auto'),('08-111','auto'),('08-44','auto')]);r['i0']['score']=94.9
        self.assertEqual(self.a._flag_context_outliers(list(r.items())),0)
        r['i0']['score']=99;r['i2']['sequence_inferred']=True
        self.assertEqual(self.a._flag_context_outliers(list(r.items())),0)
    def test_sent_hidden_and_reordered_anchors(self):
        r=self.records([('08-39','auto'),('08-111','auto'),('08-0044','auto')])
        self.a.sent_history={'Image0000.pdf':dict(destination_name='Image0000_08-39.pdf',confirmed='08-39',score=99,auto_labeled=True)}
        del r['i0'];self.a.records=dict(reversed(list(r.items())))
        self.assertEqual(self.a._flag_context_outliers(self.a._sequence_order_with_sent_history()),1)
    def quick_app(self):
        r=self.records([('08-111','review')]);r['i0'].update(confirmed='',score=60)
        a=self.a;a._scan_row_locked=Mock(return_value=False);a._yield_background_learning=Mock()
        a._save_completed_case_edit=Mock(return_value=True);a._audit_event=Mock();a.status=Mock();a._start_scan_case_edit=Mock()
        return a,r
    def test_green_restored_empty_draft_uses_candidate(self):
        a,r=self.quick_app();a._quick_approve('i0')
        self.assertEqual(a._save_completed_case_edit.call_args.args[1].get(),'08-111')
    def test_blank_user_edit_cannot_approve_old_candidate(self):
        a,r=self.quick_app();r['i0']['sequence_excluded']=True;a._quick_approve('i0');a._save_completed_case_edit.assert_not_called()
    def test_green_saves_crop_below_threshold(self):
        r=self.records([('08-111','review')]);r['i0'].update(confirmed='',score=60,snippet=None)
        a=self.a;a._export_in_flight_iids=set();a._completed_thumbnail_path=Mock(return_value=None)
        for n in ('_ensure_preview','_audit_event','_close_completed_edit','_insert_completed_row','_recheck_after_decision','_schedule_post_scan_learning'):setattr(a,n,Mock())
        a._save_learning_example=Mock(return_value=True);a.completed_edit_drafts={};a.status=Mock();a.completed_send_status=Mock()
        self.assertTrue(a._save_completed_case_edit('i0',Mock(get=Mock(return_value='08-111'))))
        a._save_learning_example.assert_called_once_with(r['i0'],'08-111');self.assertTrue(r['i0']['manual_approved']);self.assertFalse(r['i0']['learning_pending'])
    def test_stop_cancels_and_never_starts_scan(self):
        a=m.App.__new__(m.App);a.root=Mock();a.scan=Mock();a._post_scan_learning_after='timer'
        for n in ('stop_event','pause_event','learning_stop_event','learning_pause_event','_export_cancel_event'):setattr(a,n,threading.Event())
        for n in ('pause_btn','restart_btn','status','_save_state','_refresh_scan_button'):setattr(a,n,Mock())
        a.stop_or_restart_scan();a.scan.assert_not_called();self.assertTrue(a._stop_all_requested);self.assertTrue(a._export_cancel_event.is_set())
        a.root.after_cancel.assert_called_once_with('timer')
    def test_cancelled_transfer_preserves_source_and_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            a=m.App.__new__(m.App);a.folder=Path(d);p=a.folder/'Image1.pdf';p.write_bytes(b'unchanged')
            a._export_cancel_event=threading.Event();a._export_cancel_event.set();a.queue=queue.Queue()
            a._export_worker(a.folder/'COMPLETED',[('i1',p,'08-1')]);self.assertEqual(p.read_bytes(),b'unchanged');self.assertEqual(a.queue.get(),('export_done',))
    def test_idle_retry_and_stop(self):
        a=m.App.__new__(m.App);a.root=Mock();a.export_busy=False;a.busy=True
        a._run_post_scan_learning_if_idle();a.root.after.assert_called_once()
        a.busy=False;a.paused=False;a.trocr_training=False;a._loading_jobs={};a._startup_ready=True
        a._last_activity_time=time.monotonic()-100;a._audit_event=Mock();a._start_automatic_learning_if_ready=Mock()
        a._run_post_scan_learning_if_idle();a._start_automatic_learning_if_ready.assert_called_once()
        a._stop_all_requested=True;a._run_post_scan_learning_if_idle();a._start_automatic_learning_if_ready.assert_called_once()
    def test_last_folder_saved_and_restored(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(m.os.environ,{'LOCALAPPDATA':d}):
            a=m.App.__new__(m.App);a.auto_send_var=Mock(get=Mock(return_value=False));a.show_all_files_var=Mock(get=Mock(return_value=True))
            a._load_user_settings();a.folder=Path(d)/'PDFs';a._audit_event=Mock();a._save_user_settings()
            a.folder=None;a._load_user_settings();a.choose_folder=Mock();a._restore_last_folder();a.choose_folder.assert_called_once_with(str(Path(d)/'PDFs'))
    def test_poll_recovers_from_missing_widget(self):
        a=m.App.__new__(m.App);a.queue=queue.Queue();a.queue.put(('export_result','i','src','dst',''))
        a._apply_export_result=Mock(side_effect=m.tk.TclError('row disappeared'));a.root=Mock();a._update_scan_file_count=Mock()
        with patch.object(m.logging,'exception'):a._poll()
        a.root.after.assert_called_once_with(150,a._poll)
    def test_close_waits_for_write_then_saves(self):
        a=m.App.__new__(m.App);a.root=Mock();a.busy=False;a.export_busy=True;a.trocr_training=False
        a._close_when_workers_idle();a.root.destroy.assert_not_called()
        a.export_busy=False;a.folder=Path('/tmp');a._save_state=Mock(return_value=True);a._save_user_settings=Mock()
        a._close_when_workers_idle();a._save_state.assert_called_once();a.root.destroy.assert_called_once()
    @staticmethod
    def glyph_fixture():
        import cv2, numpy as np
        image=np.full((500,500,3),255,dtype=np.uint8)
        cv2.putText(image,'08-111',(308,58),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,0,0),2)
        return image

    def test_learning_saves_real_handwritten_crop_without_modifying_old_data(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as d:
            a=m.App.__new__(m.App);a.folder=Path(d);a.learning_dir=Path(d)/'learning';a.learning_data={};a.data_lock=threading.RLock()
            old=a.learning_dir/'examples_v3'/'printed'/'old.png';old.parent.mkdir(parents=True);old.write_bytes(b'old-example')
            for n in ('_persist_learning_data','_diag_event','_diag_sync_shared','_refresh_learning_stats','_refresh_learning_button_state','_save_state'):setattr(a,n,Mock())
            a._valid_ocr_box_candidates=Mock(return_value=[])
            r=dict(path=Path(d)/'Image1.pdf',candidate='08-111',score=60,training_type='printed',
                   snippet=(None,self.glyph_fixture(),None,(300,30,430,65)))
            self.assertTrue(a._save_learning_example(r,'08-111'))
            ex=a.learning_data['examples'][0];self.assertEqual(ex['training_type'],'handwritten')
            self.assertTrue((a.learning_dir/ex['image']).is_file());self.assertEqual(old.read_bytes(),b'old-example')
    def test_clear_removes_rows_and_refreshes_overlay(self):
        a=m.App.__new__(m.App);a.busy=False;a.export_busy=False;a._training_blocks_work=Mock(return_value=False)
        a.tree=Mock();a.tree.get_children.return_value=('i1','i2');a.status=Mock()
        for n in ('_refresh_scan_checkbox_heading','_update_scan_file_count','_refresh_scan_button','_schedule_scan_preview_refresh'):setattr(a,n,Mock())
        a.clear_scan_screen();self.assertEqual(a.tree.delete.call_count,2);a._schedule_scan_preview_refresh.assert_called_once()
    def test_export_done_unlocks_and_rebuilds_list(self):
        a=m.App.__new__(m.App);a.queue=queue.Queue();a.queue.put(('export_done',));a.root=Mock()
        a.busy=False;a.export_busy=True;a._export_in_flight_iids={'i1'};a.scan_items=[];a._stop_all_requested=True
        for n in ('_scan_progress_update','_rebuild_scan_list','_update_completed_count','_refresh_scan_button','_schedule_auto_export','_update_scan_file_count','_apply_sequence_consensus'):setattr(a,n,Mock())
        a.records={};a._poll();self.assertFalse(a.export_busy);self.assertEqual(a._export_in_flight_iids,set())
        a._rebuild_scan_list.assert_called_once();a._apply_sequence_consensus.assert_not_called()
if __name__=='__main__':unittest.main()
