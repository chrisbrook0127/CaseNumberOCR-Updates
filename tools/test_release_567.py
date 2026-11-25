"""Regression for TrOCR's repeated spurious 08-357 output on non-case crops."""
import unittest
from pathlib import Path
from unittest.mock import Mock
import sys
import numpy as np
import cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import case_number_ocr as m

class OCRDefaultBiasTests(unittest.TestCase):
    def make_app(self):
        a=m.App.__new__(m.App)
        a._diag_event=Mock();a._log=Mock()
        a.trocr_model=object();a.trocr_error="";a.reader=None
        a.stop_event=None
        a._trocr_bias_checked=True;a._trocr_bias_values=set()
        a._automatic_case_crop_rects_4x4=lambda _image,max_regions=12: [(382,50,590,112)]
        return a
    @staticmethod
    def digit_image(number='08-386'):
        region=np.full((350,650,3),255,np.uint8)
        cv2.putText(region,number,(395,97),cv2.FONT_HERSHEY_SIMPLEX,
                    1.45,(0,0,0),3,cv2.LINE_AA)
        return region
    def test_known_false_crop_dimensions_rejected(self):
        img=np.full((1400,900,3),255,np.uint8)
        for rect in [(0,441,821,510),(0,496,72,538),(516,1249,771,1270),
                     (612,268,640,292)]:
            ok,reason=m.App._plausible_case_ink_crop(img,rect)
            self.assertFalse(ok,(rect,reason))
    def test_blank_and_ruled_regions_do_not_get_a_case_number(self):
        blank=np.full((250,650,3),255,np.uint8)
        self.assertFalse(m.App._plausible_case_ink_crop(blank,(50,30,260,85))[0])
        cv2.line(blank,(50,57),(620,57),(0,0,0),2)
        self.assertFalse(m.App._plausible_case_ink_crop(blank,(50,30,620,85))[0])
    def test_detected_non_case_word_cannot_become_model_only_case(self):
        img=np.full((250,650,3),255,np.uint8)
        cv2.putText(img,'DEPARTMENT',(130,96),cv2.FONT_HERSHEY_SIMPLEX,
                    1,(0,0,0),2,cv2.LINE_AA)
        good,reason=m.App._plausible_case_ink_crop(img,(120,52,405,113),
            [((135,66,385,104),'DEPARTMENT',99)])
        self.assertFalse(good,reason)
        self.assertEqual(reason,'non_case_word_detected')
    def test_true_handwriting_area_independent_of_easyocr_text(self):
        a=self.make_app()
        a._trocr_read_scored=Mock(return_value=('08-386',99.1))
        result=a._automatic_handwritten_4x4(self.digit_image(),x_offset=700)
        self.assertEqual([x['number'] for x in result],['08-386'])
        self.assertEqual(result[0]['rect'][0],1082)
        self.assertEqual(a._trocr_read_scored.call_count,1)
    def test_negative_controls_quarantine_repeated_model_default(self):
        a=self.make_app();a._trocr_bias_checked=False
        a._trocr_read_scored=Mock(side_effect=[('08-357',98),('08-357',97),('08-357',99)])
        hits=a._automatic_handwritten_4x4(self.digit_image('08-357'))
        self.assertEqual(hits,[])
        self.assertIn('08-357',a._trocr_bias_values)
        self.assertEqual(a._trocr_read_scored.call_count,3)
    def test_legitimate_357_not_hardcoded_blocked(self):
        a=self.make_app();a._trocr_bias_checked=False
        a._trocr_read_scored=Mock(side_effect=[('report',40),('other',55),('08-357',99)])
        hits=a._automatic_handwritten_4x4(self.digit_image('08-357'))
        self.assertEqual([x['number'] for x in hits],['08-357'])
    def test_literal_easyocr_357_still_approved_even_if_trocr_biased(self):
        a=self.make_app();a.reader=object();a._trocr_bias_values={'08-357'}
        a._timed_easyocr_readtext=Mock(return_value=[(
           [[395,62],[571,62],[571,105],[395,105]],'08-357',.989)])
        a._trocr_read_scored=Mock()
        hits=a._automatic_handwritten_4x4(self.digit_image('08-357'))
        self.assertEqual(hits[0]['number'],'08-357')
        self.assertAlmostEqual(hits[0]['score'],98.9,places=1)
        a._trocr_read_scored.assert_not_called()
    def test_different_negative_controls_do_not_disable_other_numbers(self):
        a=self.make_app();a._trocr_bias_checked=False
        a._trocr_read_scored=Mock(side_effect=[('08-357',98),('08-382',98),('08-386',99)])
        hits=a._automatic_handwritten_4x4(self.digit_image())
        self.assertEqual([x['number'] for x in hits],['08-386'])
        self.assertEqual(a._trocr_bias_values,set())

if __name__=='__main__':unittest.main(verbosity=2)