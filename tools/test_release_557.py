"""MFCase v5.5.7 release smoke tests for complete case fields and 95% approval."""
import unittest,sys,tempfile,threading
from pathlib import Path
from unittest.mock import Mock
import numpy as np,cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import case_number_ocr as m
class Tests(unittest.TestCase):
    def app(self):
        a=m.App.__new__(m.App);a._diag_event=Mock();a._log=Mock()
        a.stop_event=threading.Event();a.pause_event=threading.Event();a.pause_event.set()
        a.learning_data={'field_regions_full_page':[]};a._perf_stages={}
        a._find_case_field_label_regions=Mock(return_value=[])
        a._find_case_candidates=Mock(return_value=[])
        a._diag_field_filter=lambda hits,kind:hits
        a._prefer_case_margin=lambda hits,kind,image:hits
        a._resolve_ocr_alternates=lambda hits,kind,best:hits
        return a
    def test_95_always_enabled(self):
        a=self.app();a.auto_label_var=Mock();a.auto_label_var.get.return_value=False
        self.assertIsNone(a._auto_label_block_reason(98.6,False,'08-287'))
        self.assertIsNotNone(a._auto_label_block_reason(94.9,False,'08-287'))
        self.assertIsNotNone(a._auto_label_block_reason(100,True,'77-287'))
    def test_crop_includes_whole_number(self):
        img=np.full((634,634,3),255,np.uint8)
        cv2.putText(img,'08-',(400,108),cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,1.3,(0,0,0),3,cv2.LINE_AA)
        cv2.putText(img,'287',(490,108),cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,1.3,(0,0,0),3,cv2.LINE_AA)
        boxes=m.App._automatic_case_crop_rects_4x4(img)
        self.assertTrue(any(x0<420 and x1>=570 and y0<98<y1 for x0,y0,x1,y1 in boxes),boxes)
    def test_split_easyocr_box_join(self):
        a=self.app()
        def box(x0,y0,x1,y1):return [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
        reads=[(box(400,50,476,82),'08-',.99),(box(484,50,575,82),'287',.98)]
        calls=[]
        def read(*args,**kwargs):
            calls.append(1);return reads if len(calls)==1 else []
        a._timed_easyocr_readtext=read
        a._timed_easyocr_recognize=lambda *args,**kwargs:[]
        a._recognize_case_strip=lambda *args,**kwargs:[]
        a._trocr_read_scored=lambda *args,**kwargs:('',0)
        hits=m.App._find_case_candidates(a,np.full((160,634,3),255,np.uint8),x_offset=100,run_trocr=False)
        found=[h for h in hits if h['number']=='08-287' and h['box_verified']]
        self.assertTrue(found,hits)
        self.assertGreaterEqual(found[0]['box_rect'][2],675)
    def test_98_beats_76_same_box(self):
        hi={'number':'08-287','score':98.6,'rect':(745,50,925,105),'box_rect':(745,50,925,105),
            'box_verified':True,'engines':['TrOCR-auto-crop'],'engine_scores':{'TrOCR':98.6}}
        low=dict(hi,number='08-77',score=76.1,engines=['EasyOCR'],engine_scores={'EasyOCR':76.1})
        selected=m._select_primary_case_hit([low,hi],'header',(1743,1347,3))
        self.assertEqual(selected['number'],'08-287')
    def test_two_real_95_conflicts_review(self):
        a=self.app()
        a._automatic_handwritten_4x4=Mock(return_value=[
          {'number':n,'score':score,'rect':(745,50,925,105),'box_rect':(745,50,925,105),
           'box_verified':True,'engines':['EasyOCR'],'engine_scores':{'EasyOCR':score}}
          for n,score in [('08-287',99),('08-297',99.8)]])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.pdf'
            with m.pymupdf.open() as doc:doc.new_page(width=612,height=792);doc.save(p)
            result=a._ocr_pdf(p)
        self.assertIn('conflicting OCR reads',result[2])
if __name__=='__main__':unittest.main()
