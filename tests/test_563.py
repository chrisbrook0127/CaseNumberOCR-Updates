import sys,unittest,threading,tempfile
from pathlib import Path
from unittest.mock import Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import case_number_ocr as m

def det(text,rect=(20,20,150,50),score=.98):
 x0,y0,x1,y1=rect
 return ([[x0,y0],[x1,y0],[x1,y1],[x0,y1]],text,score)
class ScanFixes(unittest.TestCase):
 def app(self,detections=(),hand='',score=99,proposals=()):
  a=m.App.__new__(m.App);a.reader=object();a.trocr_model=object();a.trocr_error=''
  a.stop_event=threading.Event();a.pause_event=threading.Event();a.pause_event.set();a._diag_event=Mock();a._log=Mock();a._perf_stages={}
  a._timed_easyocr_readtext=Mock(return_value=list(detections));a._trocr_read_scored=Mock(return_value=(hand,score))
  a._automatic_case_crop_rects_4x4=Mock(return_value=list(proposals));a._get_auto_threshold=lambda:95
  return a
 def image(self):return np.repeat(((np.indices((640,640)).sum(axis=0)%2)*255).astype('uint8')[:,:,None],3,axis=2)
 def read(self,a):return a._automatic_handwritten_4x4(self.image())
 def test_complete_number_never_appends_adjacent_digits(self):
  a=self.app([det('08-84',(20,20,100,50)),det('84',(105,20,150,50))])
  self.assertEqual([h['number'] for h in self.read(a)],['08-84'])
 def test_split_prefix_and_multiple_digit_fragments(self):
  a=self.app([det('08-',(10,20,50,50)),det('01',(55,20,75,50)),det('114',(80,20,120,50))])
  self.assertEqual(self.read(a)[0]['number'],'08-01114')
 def test_literal_dash_split_is_supported_not_invented(self):
  a=self.app([det('08',(10,20,35,50)),det('-',(40,20,45,50)),det('101',(50,20,90,50))])
  self.assertEqual(self.read(a)[0]['number'],'08-101')
  a=self.app([det('08',(10,20,35,50)),det('101',(40,20,90,50))]);self.assertEqual(self.read(a),[])
 def test_invalid_prefix_year_length_rejected(self):
  for text in ['336','09-336','08336','08 336','08-12345678','extra 08-336']:
   a=self.app([det(text)],text,proposals=[(20,20,150,50)]);self.assertEqual(self.read(a),[],text)
 def test_handwriting_independently_establishes_prefix(self):
  a=self.app([],hand='08-1001',proposals=[(20,20,150,50)])
  self.assertEqual(self.read(a)[0]['number'],'08-1001')
 def test_higher_same_crop_score_wins_both_directions(self):
  for printed,ps,hand,hs,want in [('08-136',.96,'08-336',99,'08-336'),('08-336',.99,'08-136',96,'08-336')]:
   a=self.app([det(printed,score=ps)],hand,hs);h=self.read(a)[0]
   self.assertEqual(h['number'],want);self.assertIsNone(a._auto_label_block_reason(h['score'],False,want))
   self.assertTrue(any(x.args[0]=='crop_read_selection' for x in a._diag_event.call_args_list))
 def test_above_below_label_field_association(self):
  for y in [0,65]:
   a=self.app([det('INCIDENT NO.',(20,35,150,55)),det('08-341',(155,y,260,y+20))])
   h=self.read(a)[0];self.assertEqual(h['label_type'],'incident')
 def test_combined_and_split_labels(self):
  a=self.app([det('INCIDENT NO.08-341')]);self.assertEqual(self.read(a)[0]['number'],'08-341')
  a=self.app([det('INCIDENT',(10,20,100,50)),det('NO.',(105,20,140,50)),det('08-341',(145,20,240,50))])
  self.assertEqual(self.read(a)[0]['label_type'],'incident')
 def test_large_overlap_does_not_merge_distinct_fields(self):
  self.assertFalse(m.App._same_number_box((0,0,500,100),(30,20,100,40)))
  self.assertFalse(m.App._same_number_box((10,10,150,35),(10,40,150,65)))
  self.assertTrue(m.App._same_number_box((10,10,150,35),(12,11,152,36)))
 def test_budget_and_duplicate_crops(self):
  a=self.app(hand='08-341',proposals=[(20,20,150,50)]*3+[(20,100+i*50,150,130+i*50) for i in range(5)])
  self.read(a);self.assertEqual(a._timed_easyocr_readtext.call_count,1);self.assertEqual(a._trocr_read_scored.call_count,3)
 def test_blank_crop_not_sent_to_handwriting(self):
  a=self.app(hand='08-341',proposals=[(20,20,150,50)])
  self.assertEqual(a._automatic_handwritten_4x4(np.full((640,640,3),255,dtype='uint8')),[]);a._trocr_read_scored.assert_not_called()
 def test_other_checkbox_word_excluded(self):
  a=self.app([det('OTHER')],hand='08-314',proposals=[(20,20,150,50)])
  self.assertEqual(self.read(a),[]);a._trocr_read_scored.assert_not_called()
 def test_label_does_not_exclude_other_handwritten_location(self):
  a=self.app([det('INCIDENT NO.')],proposals=[(350,200,500,240)])
  a._trocr_read_scored.side_effect=[('',0),('08-341',99)]
  self.assertEqual(self.read(a)[0]['number'],'08-341')
 def test_label_field_gets_budget_before_poor_proposals(self):
  a=self.app([det('INCIDENT NO.',(20,200,150,225)),det('08-341',(155,195,260,225))],proposals=[(300,i*40,500,i*40+30) for i in range(8)])
  self.read(a)
  choices=[x.kwargs for x in a._diag_event.call_args_list if x.args[0]=='crop_read_selection']
  self.assertEqual(choices[0]['selected'],'08-341');self.assertLessEqual(a._trocr_read_scored.call_count,3)
 def test_distinct_conflicting_fields_even_when_one_below_threshold(self):
  a=self.app([det('08-341',(20,20,150,50),.99),det('08-342',(20,100,150,130),.70)])
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'page.pdf'
   with m.pymupdf.open() as doc:doc.new_page(width=612,height=792);doc.save(p)
   before=p.read_bytes();out=a._ocr_pdf(p);self.assertEqual(before,p.read_bytes())
  self.assertIn('conflicting',out[2]);self.assertIn('MANUAL REVIEW',out[2])
 def test_quarter_bounds_unchanged(self):
  image=np.zeros((1743,1347,3),dtype='uint8')
  self.assertTrue(m.App._is_in_4x4_rect((680,700,900,850),image));self.assertFalse(m.App._is_in_4x4_rect((600,700,900,850),image))

class Anchors(unittest.TestCase):
 def record(self,case=None,human=False,inferred=False,**kw):
  return dict(status='REVIEW',confirmed=case or '',manual_approved=bool(case),auto_labeled=bool(case) and not human,sequence_inferred=inferred,**kw)
 def plan(self,records):
  a=m.App.__new__(m.App);return a._sequence_consensus_plan([(f'Image{i*7}',r) for i,r in enumerate(records)])
 def test_matching_zero_padded_anchors_span_multiple_files(self):
  r=self.record;p,c=self.plan([r('08-0084'),r(),r(),r(),r('08-84')]);self.assertEqual(set(p),{1,2,3});self.assertEqual(p[2][0],'08-84')
 def test_nearest_conflicting_anchor_not_skipped(self):
  r=self.record;p,c=self.plan([r('08-84'),r('08-85'),r(),r('08-84')]);self.assertNotIn(2,p);self.assertIn(2,c)
 def test_manual_matching_boundaries_correct_auto_middle(self):
  r=self.record;p,c=self.plan([r('08-84',True),r('08-85'),r(),r('08-84',True)]);self.assertEqual(set(p),{1,2})
 def test_different_manual_boundaries_no_winner(self):
  r=self.record;p,c=self.plan([r('08-84',True),r(),r('08-85',True)]);self.assertEqual(p,{})
 def test_inferred_anchor_cannot_chain(self):
  r=self.record;p,c=self.plan([r('08-84',inferred=True),r(),r('08-84')]);self.assertEqual(p,{})
 def test_sent_anchor_and_manual_middle_preserved(self):
  r=self.record;p,c=self.plan([r('08-84',True,exported=True),r(),r('08-85',True),r(),r('08-84',True)]);self.assertNotIn(2,p);self.assertNotIn(1,p);self.assertNotIn(3,p)
 def test_hidden_rows_sent_history_order_used(self):
  r=self.record;p,c=self.plan([r('08-84',True,exported=True),r(),r(),r('08-84',True)]);self.assertEqual(set(p),{1,2})
 def test_matching_local_auto_anchors_inside_different_outer_humans(self):
  r=self.record;p,c=self.plan([r('08-1',True),r('08-2'),r(),r('08-2'),r('08-3',True)])
  self.assertEqual(p[2][0],'08-2')
if __name__=='__main__':unittest.main()
