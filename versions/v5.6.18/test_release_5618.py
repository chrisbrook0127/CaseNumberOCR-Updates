"""v5.6.18: safe handwritten data, model-only crops, uncertain OCR policy."""
import json,os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
from PIL import Image
import cv2
from case_number_ocr import hw5618 as safety
import case_number_ocr as m

class HandwritingSafety5618(unittest.TestCase):
    def glyph(self,s='08-570'):
        im=np.full((65,220,3),255,np.uint8)
        cv2.putText(im,s,(7,48),cv2.FONT_HERSHEY_SIMPLEX,1.1,(0,0,0),2)
        return im
    def test_number_only_crop_for_model(self):
        im=self.glyph();crop,box,reason=safety.number_only(im)
        self.assertIsNotNone(crop);self.assertGreater(crop.shape[1],55)
        self.assertIn(reason,['number_only','isolated','edge_clipped'])
    def test_caption_not_sent_to_model(self):
        im=np.full((65,500,3),255,np.uint8)
        cv2.putText(im,'INCIDENT NO.',(10,47),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,0,0),2)
        im[:,250:470]=self.glyph('08-570')[:,0:220]
        crop,bounds,reason=safety.number_only(im,label_rects=[(10,10,236,56)],require_separation=True)
        self.assertIsNotNone(crop);self.assertGreaterEqual(bounds[0],236)
        self.assertLess(crop.shape[1],260)
    def test_unseparable_caption_fails_closed(self):
        im=self.glyph()
        crop,box,reason=safety.number_only(im,require_separation=True)
        self.assertIsNone(crop)
        self.assertEqual(reason,'field_not_separable')
    def test_blank_cannot_be_training_crop(self):
        blank=np.full((50,250,3),255,np.uint8)
        self.assertIsNone(safety.number_only(blank)[0])
    def test_trailing_prefix_conflict(self):
        self.assertTrue(safety.missing_end_conflict(['08-57','08-570']))
        self.assertFalse(safety.missing_end_conflict(['08-570','08-570']))
        self.assertFalse(safety.missing_end_conflict(['08-570','08-379']))
    def test_adjacent_digit_detected(self):
        im=self.glyph('08-570')
        self.assertTrue(safety.following_ink(im,(6,13,111,54)) or
                        safety.following_ink(im,(6,13,130,54)))
    def test_case_year_exact(self):
        self.assertIsNone(safety.identity('09-570'))
        self.assertIsNone(safety.identity('08-57x'))
        self.assertEqual(safety.identity('08-00570'),'08-570')
    def test_conflicting_training_label_quarantined(self):
        ex={'image':'samples/one.png','ocr_candidate':'08-570','ocr_score':96,
            'confirmed_case_number':'08-422','example_id':'x'}
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'samples').mkdir()
            Image.fromarray(self.glyph()).save(folder/'samples/one.png')
            safe,bad=safety.audit_examples([ex],folder)
            self.assertEqual(safe,[]);self.assertEqual(bad[0]['reason'],'ocr_label_conflict_needs_visual_verification')
            self.assertTrue((folder/'samples/one.png').is_file())
    def test_weak_training_label_quarantined(self):
        self.assertEqual(safety.suspect_training_label({'ocr_candidate':'08-422',
              'confirmed_case_number':'08-422','ocr_score':63.7}),
              'no_independent_high_confidence_label_evidence')
    def test_manual_filename_can_still_differ(self):
        self.assertEqual(safety.identity('08-422'),'08-422')
    def test_quarantine_report_keeps_originals(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'learning_examples_v3.json').write_text('source')
            safety.write_quarantine_report(folder,[{'image':'old.png','reason':'suspect'}])
            self.assertEqual((folder/'learning_examples_v3.json').read_text(),'source')
            self.assertEqual(json.loads((folder/'handwriting_quarantine_v5618.json').read_text())['excluded_from_training'][0]['image'],'old.png')
    def test_backup_creates_complete_marker_only_after_copy(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'learning_examples_v3.json').write_text('original')
            model=folder/'trocr_v2_finetuned';model.mkdir();(model/'config.json').write_text('model')
            b=safety.backup_learning_once(folder)
            self.assertEqual((b/'learning_examples_v3.json').read_text(),'original')
            self.assertEqual((b/'trocr_v2_finetuned/config.json').read_text(),'model')
            self.assertTrue((b/'COMPLETE.json').is_file())
    def test_learning_never_starts_automatically(self):
        a=m.App.__new__(m.App);a._stop_all_requested=False;a._startup_ready=True
        a.busy=False;a.export_busy=False;a.trocr_training=False
        a._pending_learning_examples=Mock(return_value=[{'id':1}]);a._maybe_train_trocr=Mock()
        self.assertFalse(a._start_automatic_learning_if_ready());a._maybe_train_trocr.assert_not_called()
        self.assertFalse(a._maybe_train_trocr.call_count)
    def test_schedule_never_installs_90_second_training_timer(self):
        a=m.App.__new__(m.App);a.root=Mock();a._post_scan_learning_after=None
        a._schedule_post_scan_learning();a.root.after.assert_not_called()
    def test_budget_retains_fast_path(self):
        a=m.App.__new__(m.App);a._page_ocr_budget={'thread':threading.get_ident(),'printed':0,'handwriting':0,'cache':{}}
        for _ in range(3):
            val=a._budgeted_ocr('handwriting',np.ones((20,20,3),np.uint8)*(_+1),lambda:('08-570',99),('',0))
            self.assertEqual(val[0],'08-570')
        self.assertEqual(a._page_ocr_budget['handwriting'],3)
        blocked=a._budgeted_ocr('handwriting',np.ones((20,20,3),np.uint8)*9,lambda:('08-570',99),('',0))
        self.assertEqual(blocked,('',0))
    def test_budget_allows_two_more_when_uncertain(self):
        a=m.App.__new__(m.App);a._page_ocr_budget={'thread':threading.get_ident(),'printed':0,'handwriting':0,'cache':{}}
        for i in range(5):
            a._budgeted_ocr('handwriting',np.ones((20,20,3),np.uint8)*(i+1),lambda:('08-57',70),('',0))
        self.assertEqual(a._page_ocr_budget['handwriting'],5)
    def test_regression_location_code_untouched(self):
        self.assertTrue(callable(m._learned_crop_rectangles))
        self.assertTrue(callable(m.App._ranked_learned_scan_zones))
    def test_blank_learning_label_never_trains(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d);(f/'blank.png').write_bytes(b'not an image')
            safe,bad=safety.audit_examples([{'image':'blank.png','ocr_candidate':'08-1',
                'confirmed_case_number':'08-1','ocr_score':99}],f)
            self.assertEqual(len(safe),0);self.assertEqual(len(bad),1)

if __name__=='__main__':unittest.main()
