"""v5.6.19: UI allocation, adjustable OCR confidence and guarded idle learning."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import case_number_ocr as mf


class MFCase5619Regressions(unittest.TestCase):
    def app_with_threshold(self, threshold="95"):
        a=mf.App.__new__(mf.App)
        a.auto_threshold_var=Mock()
        a.auto_threshold_var.get.return_value=threshold
        return a

    def test_approval_range_is_90_to_99(self):
        a=self.app_with_threshold("89")
        self.assertEqual(a._get_auto_threshold(),90)
        a.auto_threshold_var.get.return_value="98"
        self.assertEqual(a._get_auto_threshold(),98)
        a.auto_threshold_var.get.return_value="101"
        self.assertEqual(a._get_auto_threshold(),99)
        a.auto_threshold_var.get.return_value="invalid"
        self.assertEqual(a._get_auto_threshold(),95)

    def test_high_confidence_handwriting_eligible_above_94(self):
        a=self.app_with_threshold("97")
        self.assertIsNone(a._auto_label_block_reason(
            98.6,False,"08-570",{"TrOCR":98.6}))
        self.assertIsNotNone(a._auto_label_block_reason(
            96.0,False,"08-570",{"TrOCR":96.0}))
        a.auto_threshold_var.get.return_value="90"
        self.assertIsNone(a._auto_label_block_reason(
            91.2,False,"08-570",{"TrOCR":91.2}))

    def test_handwriting_not_downgraded_due_to_engine(self):
        source=Path(mf.__file__).read_text(encoding="utf-8")
        self.assertNotIn("score=min(score,94.0)",source)
        self.assertNotIn("min(c,94.0)",source)
        self.assertIn("trailing_digit_uncertain",source)

    def test_missing_final_digit_still_blocks_fast_approval(self):
        valid=dict(number="08-570",score=98.5,box_verified=True,
                   box_rect=(12,14,140,48))
        self.assertEqual(mf.App._clear_localized_case([valid],97),valid)
        uncertain=dict(valid,trailing_digit_uncertain=True)
        self.assertIsNone(mf.App._clear_localized_case([uncertain],90))

    def test_threshold_saved_without_touching_auto_send(self):
        a=self.app_with_threshold("98")
        a.auto_send_var=Mock()
        a.auto_send_var.get.return_value=False
        a.folder=None
        a._last_folder=r"C:\Users\Public\NewFolder"
        a._audit_event=Mock()
        with tempfile.TemporaryDirectory() as temporary:
            a._user_settings_file=Path(temporary)/"user_settings.json"
            a._save_user_settings()
            cfg=json.loads(a._user_settings_file.read_text())
            self.assertEqual(cfg["ocr_approval_threshold"],98)
            self.assertFalse(cfg["auto_send_completed"])
            self.assertEqual(cfg["last_folder"],a._last_folder)

    def test_automatic_learning_runs_when_safe(self):
        a=mf.App.__new__(mf.App)
        a._stop_all_requested=False
        a._startup_ready=True
        a.busy=False
        a.export_busy=False
        a.trocr_training=False
        a._startup_learning_pending=False
        a._auto_learning_started=False
        a._pending_learning_examples=Mock(return_value=[dict(image="one.png")])
        a._maybe_train_trocr=Mock(return_value=True)
        self.assertTrue(a._start_automatic_learning_if_ready())
        a._maybe_train_trocr.assert_called_once_with(automatic=True)
        self.assertTrue(a._auto_learning_started)

    def test_stop_prevents_automatic_learning(self):
        a=mf.App.__new__(mf.App)
        a._stop_all_requested=True
        a._maybe_train_trocr=Mock()
        self.assertIsNone(a._start_automatic_learning_if_ready())
        a._maybe_train_trocr.assert_not_called()

    def test_idle_and_send_both_schedule_learning(self):
        a=mf.App.__new__(mf.App)
        a.root=Mock()
        a._post_scan_learning_after=None
        a._stop_all_requested=False
        a._last_activity_time=0
        a._schedule_post_scan_learning()
        self.assertEqual(a.root.after.call_args.args[0],90000)
        a._schedule_post_scan_learning(after_send=True)
        self.assertEqual(a.root.after.call_args.args[0],3500)

    def test_full_folder_path_preserved(self):
        a=mf.App.__new__(mf.App)
        path=r"C:\Users\Public\NewFolder"
        self.assertEqual(a._short_folder_name(path),path)

    def test_transport_rectangular_four_controls_preserved(self):
        self.assertEqual(mf.MFCasePieTransport.ORDER,
                         ("folder","scan","pause","stop"))
        self.assertTrue(callable(mf.MFCasePieTransport.invoke_action))
        self.assertTrue(callable(mf.MFCasePieTransport.register))

    def test_location_learning_not_rewritten(self):
        self.assertTrue(callable(mf._learned_crop_rectangles))
        self.assertTrue(callable(mf.App._ranked_learned_scan_zones))
        self.assertEqual(mf.REQUIRED_CASE_YEAR,"08")


if __name__=="__main__":
    unittest.main()
