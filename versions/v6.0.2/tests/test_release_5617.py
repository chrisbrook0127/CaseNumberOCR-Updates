"""v5.6.17 UI-only change checks; no OCR engine/model/network required."""
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from case_number_ocr import App, MFCasePieTransport


class UIOnlyRegressions(unittest.TestCase):
    def test_pie_has_all_original_actions_with_specified_colors(self):
        self.assertEqual(set(MFCasePieTransport.COLORS),
                         {'folder','scan','pause','stop'})
        for color in MFCasePieTransport.COLORS.values():
            self.assertRegex(color,r'^#[0-9a-fA-F]{6}$')

    def test_display_caps_preserves_original_pdf_identifiers_and_paths(self):
        self.assertEqual(App._ui_message_caps('Scan finished. Ready to send.'),
                         'SCAN FINISHED. READY TO SEND.')
        for value in ('Image6095.pdf',r'C:\CASE\File1.pdf',
                      'https://github.com/example/CaseNumberOCR-Updates',
                      'COPY Image6095.pdf TO COMPLETED'):
            self.assertEqual(App._ui_message_caps(value),value)

    def test_folder_name_shortened_without_changing_path(self):
        a=App.__new__(App)
        self.assertEqual(a._short_folder_name(Path('/root/rolls/April 2008')),
                         str(Path('/root/rolls/April 2008')))
        self.assertEqual(a._short_folder_name(None),'NO FOLDER SELECTED')

    def test_clocks_are_non_negative_and_correct_after_stop(self):
        a=App.__new__(App)
        a._scan_clock_start=None
        a._scan_clock_final_elapsed=62.8
        self.assertEqual(a._format_run_clock(a._scan_clock_actual()),'00:01:02')
        self.assertEqual(a._format_run_clock(-15),'00:00:00')
        self.assertEqual(a._format_run_clock(3661),'01:01:01')

    def test_timers_show_unknown_before_first_file(self):
        a=App.__new__(App)
        a.scan_run_time_label=Mock()
        a._scan_clock_start=None
        a._scan_clock_final_elapsed=0
        a._scan_clock_done=0
        a.scan_items=[1,2,3]
        a._update_run_timers()
        displayed=a.scan_run_time_label.config.call_args.kwargs['text']
        self.assertIn('ESTIMATED --:--:--',displayed)
        self.assertIn('ACTUAL 00:00:00',displayed)
        self.assertIn('REMAINING --:--:--',displayed)

    def test_timers_estimate_scales_with_completed_files(self):
        a=App.__new__(App)
        a.scan_run_time_label=Mock()
        a._scan_clock_start=None
        a._scan_clock_final_elapsed=30
        a._scan_clock_done=3
        a.scan_items=[None]*9
        a._update_run_timers()
        shown=a.scan_run_time_label.config.call_args.kwargs['text']
        self.assertIn('ESTIMATED 00:01:30',shown)
        self.assertIn('ACTUAL 00:00:30',shown)
        self.assertIn('REMAINING 00:01:00',shown)

    def test_export_creates_completed_folder_and_uniquely_named_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            local=root/'local';directory=local/'MFCase'/'diagnostics'
            directory.mkdir(parents=True)
            (directory/'mfcase-demo.jsonl').write_text('{"event":"test"}\n')
            a=App.__new__(App)
            a.folder=root/'source';a.folder.mkdir()
            a.app_dir=root;a._diag_file=directory/'mfcase-demo.jsonl'
            a.log_path=None
            a._audit_root=root/'audit';a._audit_root.mkdir()
            a._audit_file=a._audit_root/'MFCase_Work_Log.txt'
            a._audit_file.write_text('LOCAL AUDIT DATA')
            with patch.dict(os.environ,{'LOCALAPPDATA':str(local)}),\
                 patch('case_number_ocr.messagebox.showinfo') as success,\
                 patch('case_number_ocr.messagebox.showerror') as error:
                a._diag_export()
                a._diag_export()
            paths=list((a.folder/'COMPLETED').glob('MFCase_Work_Log_*.zip'))
            self.assertEqual(len(paths),2)
            self.assertEqual(len({p.name for p in paths}),2)
            self.assertEqual(success.call_count,2)
            error.assert_not_called()
            for path in paths:
                with zipfile.ZipFile(path) as z:
                    self.assertIsNone(z.testzip())
                    self.assertIn('mfcase-demo.jsonl',z.namelist())
                    self.assertIn('MFCase_Work_Log.txt',z.namelist())
            self.assertEqual(a._audit_file.read_text(),'LOCAL AUDIT DATA')

    def test_export_without_folder_does_not_create_anything(self):
        a=App.__new__(App);a.folder=None
        with patch('case_number_ocr.messagebox.showwarning') as warning:
            a._diag_export()
        warning.assert_called_once()

    def test_ui_source_does_not_modify_ocr_or_learning_engine_references(self):
        source=Path(__file__).resolve().parents[1]/'case_number_ocr.py'
        code=source.read_text(encoding="utf-8")
        self.assertIn('def _sequence_consensus_plan(',code)
        self.assertIn('def _scan_one_learned_zone(',code)
        self.assertIn('def _run_post_scan_learning_if_idle(',code)
        self.assertIn('self.app_version = "6.0.2"',code)
        self.assertNotIn('"GITHUB LOG UPLOAD",self._configure_github_logs',code)
        self.assertIn('GITHUB SETTINGS',code)
        self.assertIn("target=output_dir/suggested",code)


if __name__=='__main__':unittest.main()
