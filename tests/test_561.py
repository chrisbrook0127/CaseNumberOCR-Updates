import sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import case_number_ocr as m

class OCR561(unittest.TestCase):
 def app(self,printed='',hand='08-336',ps=.80,hs=99):
  a=m.App.__new__(m.App);a.reader=object();a.trocr_model=object();a.trocr_error=''
  a.stop_event=threading.Event();a.pause_event=threading.Event();a.pause_event.set()
  a._diag_event=Mock();a._log=Mock();a._perf_stages={}
  a._get_auto_threshold=lambda:95
  a._timed_easyocr_readtext=Mock(return_value=[([[10,10],[150,10],[150,45],[10,45]],printed,ps)] if printed else [])
  a._trocr_read_scored=Mock(return_value=(hand,hs))
  a._automatic_case_crop_rects_4x4=Mock(return_value=[(10,10,150,45)])
  return a
 def read(self,a):return a._automatic_handwritten_4x4(np.full((634,634,3),255,dtype=np.uint8))
 def test_handwriting_alone_establishes_prefix(self):
  h=self.read(self.app())[0];self.assertEqual(h['number'],'08-336');self.assertEqual(h['prefix_source'],'TrOCR')
 def test_prefixless_wrong_year_and_truncated_strings_rejected(self):
  for bad in ['336','09-336','08336','08 336','08-12345678','text 08-336','08-336 extra']:
   self.assertEqual(self.read(self.app(bad,bad)),[],bad)
 def test_higher_handwriting_score_wins(self):
  a=self.app('08-136','08-336',.96,99);h=self.read(a)[0]
  self.assertEqual(h['number'],'08-336');self.assertIsNone(a._auto_label_block_reason(h['score'],False,h['number']))
 def test_higher_printed_score_wins(self):
  self.assertEqual(self.read(self.app('08-336','08-136',.99,96))[0]['number'],'08-336')
 def test_below_threshold_remains_review(self):
  a=self.app(hs=94.9);h=self.read(a)[0];self.assertIsNotNone(a._auto_label_block_reason(h['score'],False,h['number']))
 def test_no_digit_rewriting(self):
  for n in ['08-01114','08-00101','08-1000','08-1234567']:
   self.assertEqual(self.read(self.app(hand=n))[0]['number'],n)
 def test_one_printed_three_handwriting_max(self):
  a=self.app();a._automatic_case_crop_rects_4x4.return_value=[(i*100,10,i*100+60,45) for i in range(5)]
  self.read(a);self.assertEqual(a._timed_easyocr_readtext.call_count,1);self.assertEqual(a._trocr_read_scored.call_count,3)
 def test_duplicate_crop_not_read_twice(self):
  a=self.app('08-336');a._automatic_case_crop_rects_4x4.return_value=[(8,8,152,47),(10,10,150,45)]
  self.read(a);self.assertEqual(a._trocr_read_scored.call_count,1)
 def test_split_prefix_and_digits_same_line(self):
  a=self.app();a._timed_easyocr_readtext.return_value=[([[10,10],[50,10],[50,45],[10,45]],'08-',.99),([[55,10],[120,10],[120,45],[55,45]],'336',.99)]
  self.assertEqual(self.read(a)[0]['number'],'08-336')
 def test_manual_crop_prefixless_is_blank(self):
  from PIL import Image
  a=self.app('336','336');self.assertEqual(a._ocr_review_crop(Image.new('RGB',(200,100)))[0],'')
 def test_actual_pdf_region_calls_and_original_preserved(self):
  a=self.app();regions=[]
  def read(image,**kw):regions.append(image.shape[:2]);return []
  a._timed_easyocr_readtext.side_effect=read
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'page.pdf'
   with m.pymupdf.open() as doc:doc.new_page(width=612,height=792);doc.save(p)
   before=p.read_bytes();out=a._ocr_pdf(p);self.assertEqual(p.read_bytes(),before)
  self.assertEqual(out[0],'08-336');self.assertEqual(regions,[(871,674)])
  self.assertGreaterEqual(out[1][3][0],673);self.assertEqual(a._trocr_read_scored.call_count,1)

 def test_combined_incident_field_beats_other_checkbox(self):
  a=self.app('INCIDENT NO.08-341','08-341',.99,98)
  a._timed_easyocr_readtext.return_value.append(([[200,200],[300,200],[300,230],[200,230]],'OTHER',.99))
  a._automatic_case_crop_rects_4x4.return_value=[(200,200,300,230)]
  h=self.read(a);self.assertEqual([x['number'] for x in h],['08-341'])
  self.assertEqual(h[0]['label_type'],'incident');self.assertEqual(a._trocr_read_scored.call_count,1)
 def test_separate_incident_label_and_handwritten_number(self):
  a=self.app('INCIDENT NO.','08-341')
  h=self.read(a);self.assertEqual(h[0]['number'],'08-341');self.assertEqual(h[0]['label_type'],'incident')
 def test_detected_other_word_not_reinterpreted(self):
  a=self.app('OTHER','08-314');self.assertEqual(self.read(a),[]);a._trocr_read_scored.assert_not_called()
 def test_label_without_valid_prefix_does_not_fall_back_to_other(self):
  a=self.app('INCIDENT NO.','341');self.assertEqual(self.read(a),[])
 def test_quarter_bounds_and_approval_gate(self):
  image=np.zeros((1743,1347,3),dtype=np.uint8)
  self.assertTrue(m.App._is_in_4x4_rect((680,700,900,850),image))
  self.assertFalse(m.App._is_in_4x4_rect((600,700,900,850),image))
  self.assertFalse(m.App._is_in_4x4_rect((680,700,900,900),image))

if __name__=='__main__':unittest.main()
