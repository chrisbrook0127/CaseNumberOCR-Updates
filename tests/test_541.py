import tempfile,unittest,threading
from pathlib import Path
from unittest.mock import Mock,patch
import test_rules
m=test_rules.m
class ScanStart(unittest.TestCase):
    def app(self,folder):
        a=test_rules.Rules().app();a.folder=folder;a.root=Mock();a.status=Mock()
        a._startup_ready=True;a.learning_dir=folder;a.reader=object();a.trocr_model=object()
        a.tree=Mock();a.tree.selection.return_value=('x',);a.tree.exists.return_value=True
        p=folder/'Image1.pdf';p.write_bytes(b'unchanged PDF')
        a.records={'x':{'path':p,'status':'Not scanned','confirmed':'','exported':False}}
        a.scan_checked_iids=set();a.stop_event=threading.Event();a.pause_event=threading.Event()
        for n in ['scan_btn','pause_btn','restart_btn','_refresh_scan_button','_refresh_rescan_button','_refresh_review_controls','_update_completed_count','_scan_progress_update','_diag_start_run','_worker']:
            setattr(a,n,Mock())
        a._year_prefix=lambda:'08'
        return a
    def test_visible_selection_starts_worker_without_waiting_for_selection_event(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app(Path(d))
            with patch.object(m,'easyocr',object()),patch.object(m.threading,'Thread') as t:
                a.scan()
            t.return_value.start.assert_called_once()
            self.assertEqual([i for i,r in a.scan_items],['x']);self.assertTrue(a.busy)
            self.assertEqual(a.records['x']['path'].read_bytes(),b'unchanged PDF')
    def test_thread_start_failure_recovers_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app(Path(d));a._scan_blocked=Mock()
            with patch.object(m,'easyocr',object()),patch.object(m.threading,'Thread') as t,patch.object(m.logging,'exception'):
                t.return_value.start.side_effect=RuntimeError('thread failed');a.scan()
            self.assertFalse(a.busy);a._scan_blocked.assert_called_once()
            self.assertIn('thread failed',a._scan_blocked.call_args.args[0])
    def test_busy_or_missing_models_has_visible_reason(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app(Path(d));a._scan_blocked=Mock();a.busy=True;a.scan()
            self.assertTrue(a.busy);a._scan_blocked.assert_called_once()
            a.busy=False;a.reader=None;a._scan_blocked.reset_mock();a.scan()
            self.assertIn('models',a._scan_blocked.call_args.args[0])
    def test_review_rows_route_to_explicit_rescan(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app(Path(d));a.records['x']['status']='Needs review';a.rescan_selected=Mock()
            with patch.object(m,'easyocr',object()):a.scan()
            a.rescan_selected.assert_called_once();self.assertFalse(a.busy)
    def test_busy_refresh_disables_scan(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app(Path(d));a.busy=True;m.App._refresh_scan_button(a)
            a.scan_btn.config.assert_called_once_with(state='disabled')
