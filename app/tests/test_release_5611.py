"""MFCase v5.6.11: labeled case-field location priority only.

No actual PDFs, external OCR models, or private data required.
"""
import unittest, sys
from pathlib import Path
from unittest.mock import Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import case_number_ocr as m


def bbox(x0,y0,x1,y1):
    return [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]


def hit(number,score,x0,y0,x1,y1,kind=None):
    rect=(x0,y0,x1,y1)
    return {'number':number,'score':score,'rect':rect,'box_rect':rect,
            'box_verified':True,'label_type':kind,'engines':['EasyOCR'],
            'engine_scores':{'EasyOCR':score}}

class FieldPriorityTests(unittest.TestCase):
    def make_app(self, detections):
        a=m.App.__new__(m.App)
        a.reader=object()
        a._timed_easyocr_readtext=Mock(return_value=detections)
        a._diag_event=Mock()
        a._automatic_case_crop_rects_4x4=Mock(return_value=[])
        a._plausible_case_ink_crop=Mock(return_value=(True,'number'))
        a._probe_trocr_default_bias=Mock()
        a._trocr_bias_values=set()
        a._log=Mock()
        a.trocr_model=None
        a.trocr_error='unavailable'
        a.stop_event=None
        return a

    def test_incident_label_outweighs_unrelated_higher_scored_number(self):
        items=[hit('08-391',99.9,175,145,260,170,None),
               hit('08-398',67,275,25,360,45,'incident')]
        selected=m._select_primary_case_hit(items,'incident',(400,600,3))
        self.assertEqual(selected['number'],'08-398')
        self.assertEqual(selected['label_type'],'incident')

    def test_labeled_number_does_not_require_two_ocr_engines(self):
        candidate=hit('08-398',98.5,275,25,360,45,'incident')
        selected=m._select_primary_case_hit([candidate],'incident',(400,600,3))
        self.assertEqual(selected['number'],'08-398')
        self.assertEqual(selected['engines'],['EasyOCR'])

    def test_unlabeled_high_confidence_number_still_selected(self):
        a=hit('08-403',97.6,275,30,350,57,None)
        other=hit('08-390',72,200,175,280,195,None)
        result=m._select_primary_case_hit([other,a],'fallback',(400,600,3))
        self.assertEqual(result['number'],'08-403')

    def test_case_field_is_right_of_label_and_not_below(self):
        detections=[(bbox(160,15,260,37),'INCIDENT NO.',.98),
                    (bbox(275,17,350,39),'08-398',.79),
                    (bbox(160,125,270,147),'08-391',.99)]
        a=self.make_app(detections)
        image=np.full((330,500,3),255,np.uint8)
        hits=a._automatic_handwritten_4x4(image,0,0)
        labeled=[h for h in hits if h['number']=='08-398']
        self.assertTrue(labeled,hits)
        self.assertEqual(labeled[0]['label_type'],'incident')
        unrelated=[h for h in hits if h['number']=='08-391']
        self.assertTrue(unrelated,hits)
        self.assertIsNone(unrelated[0]['label_type'])
        selected=m._select_primary_case_hit(hits,'incident',image.shape)
        self.assertEqual(selected['number'],'08-398')

    def test_missing_printed_number_proposes_right_of_label_crop_first(self):
        detections=[(bbox(160,15,260,37),'INCIDENT NO.',.98),
                    (bbox(160,125,270,147),'08-391',.99)]
        a=self.make_app(detections)
        a.trocr_model=object()
        a.trocr_error=''
        a._automatic_case_crop_rects_4x4=Mock(return_value=[(160,125,270,147)])
        calls=[]
        def hand_read(crop):
            calls.append(crop.shape[:2])
            return ('08-398',97.2) if len(calls)==1 else ('',0.0)
        a._trocr_read_scored=hand_read
        image=np.full((330,500,3),255,np.uint8)
        # Small visible ink in right-of-label crop to make the pixel pipeline live.
        image[23:36,295:345]=25
        image[130:143,175:245]=30
        hits=a._automatic_handwritten_4x4(image,0,0)
        self.assertTrue(calls,calls)
        self.assertTrue(any(h['number']=='08-398' and h['label_type']=='incident'
                            for h in hits),hits)
        self.assertEqual(m._select_primary_case_hit(hits,'incident',image.shape)['number'],'08-398')

    def test_selection_never_invents_prefix(self):
        a=hit('391',100,275,25,360,45,'incident')
        valid=hit('08-398',70,275,25,360,45,None)
        self.assertEqual(m._select_primary_case_hit([a,valid],'incident',(400,600,3))['number'],'08-398')

if __name__=='__main__':unittest.main()
