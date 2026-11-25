"""v5.6.12 multi-crop location learning: form types, scan order, safeguards."""
import tempfile, threading, unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
import case_number_ocr as m


def saved_crop(i, x=.67, y=.07, w=.23, h=.07, full=True):
    rect=[x-w/2,y-h/2,x+w/2,y+h/2]
    return {'rect_norm':rect,'source_pdf':f'Image{i}.pdf','confirmed_case_number':'08-398',
            'saved_at':'2026-11-25T12:00:00'}

class CropLearning(unittest.TestCase):
    def test_100_crops_group_to_one_average_standard_location(self):
        crops=[saved_crop(i,x=.68+((i%9)-4)*.002,y=.08+((i%7)-3)*.002,
                          w=.20+(i%5)*.003,h=.06+(i%3)*.003)
               for i in range(100)]
        clusters=m._learned_crop_clusters({'field_regions_full_page':crops})
        self.assertEqual(len(clusters),1)
        self.assertEqual(clusters[0]['count'],100)
        x0,y0,x1,y1=clusters[0]['rect_norm']
        self.assertAlmostEqual((x0+x1)/2,.68,delta=.012)
        self.assertAlmostEqual((y0+y1)/2,.08,delta=.012)
        self.assertAlmostEqual(x1-x0,.206,delta=.013)
        box=m._learned_crop_rectangles({'field_regions_full_page':crops},1360,1760)[0]
        self.assertGreater(box['rect'][0],650)
        self.assertEqual(box['count'],100)

    def test_multiple_distinct_form_templates_both_ranked(self):
        crops=[saved_crop(i,x=.74,y=.08) for i in range(75)] + [
            saved_crop(100+i,x=.19,y=.43,w=.27,h=.08) for i in range(25)]
        clusters=m._learned_crop_clusters({'field_regions_full_page':crops})
        self.assertEqual([x['count'] for x in clusters],[75,25])
        boxes=m._learned_crop_rectangles({'field_regions_full_page':crops},1300,1700)
        self.assertEqual(len(boxes),2)
        self.assertLess(boxes[1]['rect'][0],1300//2)  # An approved outside-quarter crop.

    def test_all_300_crops_retained_not_limited_to_200(self):
        crops=[saved_crop(i) for i in range(300)]
        clusters=m._learned_crop_clusters({'field_regions_full_page':crops})
        self.assertEqual(sum(a['count'] for a in clusters),300)

    def test_outliers_do_not_widen_the_typical_crop(self):
        crops=[saved_crop(i,w=.22,h=.06) for i in range(96)] + [
            saved_crop(200+i,w=.38,h=.12) for i in range(4)]
        cluster=m._learned_crop_clusters({'field_regions_full_page':crops})[0]
        self.assertEqual(cluster['count'],100)
        rect=cluster['rect_norm']
        self.assertLess(rect[2]-rect[0],.26)

    def test_legacy_crop_locations_and_new_fullpage_both_count(self):
        data={'field_regions_full_page':[saved_crop(1,x=.2,y=.42)],
              'field_regions':[{'rect_norm':[.5,.1,.9,.3],
                               'source_pdf':'Image2.pdf','confirmed_case_number':'08-22'}]}
        self.assertEqual(sum(x['count'] for x in m._learned_crop_clusters(data)),2)

    def test_same_file_old_and_new_storage_not_double_counted(self):
        data={'field_regions_full_page':[saved_crop(1)],
              'field_regions':[{'rect_norm':[.5,.1,.9,.3],
                               'source_pdf':'Image1.pdf','confirmed_case_number':'08-22'}]}
        self.assertEqual(sum(x['count'] for x in m._learned_crop_clusters(data)),1)

    def test_never_learn_invalid_or_empty_boxes(self):
        full=[saved_crop(1),{'rect_norm':[0,0,1,1]},
            {'rect_norm':['nan',.1,.7,.2]},
            {'rect_norm':[.5,.2,.3,.1]},
            {'rect_norm':[.1,.1,.1,.2]}]
        self.assertEqual(sum(x['count'] for x in m._learned_crop_clusters(
            {'field_regions_full_page':full})),1)

    def test_cache_invalidates_as_new_crops_arrive(self):
        a=m.App.__new__(m.App)
        a.learning_data={'field_regions_full_page':[saved_crop(1)]}
        a.data_lock=threading.RLock()
        one=a._ranked_learned_scan_zones(1300,1700)
        self.assertEqual(one[0]['count'],1)
        a.learning_data['field_regions_full_page'].append(saved_crop(2))
        two=a._ranked_learned_scan_zones(1300,1700)
        self.assertEqual(two[0]['count'],2)
        self.assertEqual(two[0]['rect'],one[0]['rect'])

    def make_app(self,crops):
        a=m.App.__new__(m.App)
        a.learning_data={'field_regions_full_page':crops, 'field_regions':[]}
        a.data_lock=threading.RLock()
        a.stop_event=threading.Event();a.pause_event=threading.Event();a.pause_event.set()
        a._perf_stages={};a._diag_event=Mock();a._log=Mock()
        return a

    def test_handwriting_read_runs_before_printed_and_either_can_succeed(self):
        a=self.make_app([saved_crop(1)])
        a.trocr_model=object();a._probe_trocr_default_bias=Mock()
        a._trocr_bias_values=set()
        order=[]
        def handwritten(img):order.append('handwriting');return ('08-398',99.)
        def printed(*args,**kwargs):order.append('printed');return []
        a._trocr_read_scored=handwritten;a._automatic_handwritten_4x4=printed
        zone=a._ranked_learned_scan_zones(1300,1700)[0]
        hits=a._scan_one_learned_zone(np.random.default_rng(6).integers(30,220,(1700,1300,3),dtype=np.uint8),zone)
        self.assertEqual(order,['handwriting','printed'])
        self.assertEqual(hits[0]['number'],'08-398')
        self.assertTrue(hits[0]['learned_location'])
        self.assertEqual(hits[0]['score'],99.)

    def test_below_threshold_handwritten_then_printed_second(self):
        a=self.make_app([saved_crop(1)])
        a.trocr_model=object();a._probe_trocr_default_bias=Mock()
        a._trocr_bias_values=set();order=[]
        def handwritten(img):order.append('handwriting');return ('08-398',73.)
        def printed(crop,x0,y0,run_trocr=True):
            order.append('printed');self.assertFalse(run_trocr)
            rect=(x0,y0,x0+90,y0+30)
            return [dict(number='08-398',score=98.,box_rect=rect,
                rect=rect,box_verified=True,engines=['EasyOCR'],engine_scores={'EasyOCR':98.})]
        a._trocr_read_scored=handwritten;a._automatic_handwritten_4x4=printed
        zone=a._ranked_learned_scan_zones(1300,1700)[0]
        hits=a._scan_one_learned_zone(np.random.default_rng(6).integers(30,220,(1700,1300,3),dtype=np.uint8),zone)
        self.assertEqual(order,['handwriting','printed'])
        self.assertIsNotNone(a._clear_localized_case(hits,95.))

    def test_model_default_value_is_not_accepted_even_at_100(self):
        a=self.make_app([saved_crop(1)])
        a.trocr_model=object();a._probe_trocr_default_bias=Mock()
        a._trocr_bias_values={'08-477'}
        a._trocr_read_scored=Mock(return_value=('08-477',100.))
        a._automatic_handwritten_4x4=Mock(return_value=[])
        zone=a._ranked_learned_scan_zones(1300,1700)[0]
        self.assertFalse(a._scan_one_learned_zone(np.random.default_rng(6).integers(30,220,(1700,1300,3),dtype=np.uint8),zone))

    def test_quarter_fallback_when_learned_regions_unreadable(self):
        a=self.make_app([saved_crop(1,x=.2,y=.4)])
        a._scan_one_learned_zone=Mock(return_value=[])
        a._automatic_handwritten_4x4=Mock(return_value=[dict(number='08-398',score=99.,
          rect=(800,100,930,150),box_rect=(800,100,930,150),box_verified=True,
          engines=['EasyOCR'],engine_scores={'EasyOCR':99.},label_type=None)])
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'example.pdf'
            with m.pymupdf.open() as doc:
                doc.new_page(width=612,height=792);doc.save(path)
            result=a._ocr_pdf(path)
        self.assertEqual(result[0],'08-398')
        self.assertEqual(a._scan_one_learned_zone.call_count,0)
        self.assertEqual(a._automatic_handwritten_4x4.call_count,1)

    def test_valid_outside_quarter_learned_zone_can_complete(self):
        a=self.make_app([saved_crop(1,x=.2,y=.4)])
        def located(img,zone):
            x0,y0,x1,y1=zone['rect']
            return [dict(number='08-398',score=99.,
                rect=(x0,y0,x1,y1),box_rect=(x0,y0,x1,y1),box_verified=True,
                engines=['TrOCR-learned'],engine_scores={'TrOCR':99.},learned_location=True)]
        a._scan_one_learned_zone=Mock(side_effect=located)
        a._automatic_handwritten_4x4=Mock(return_value=[])
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'example.pdf'
            with m.pymupdf.open() as doc:
                doc.new_page(width=612,height=792);doc.save(path)
            result=a._ocr_pdf(path)
        self.assertEqual(result[0],'08-398')
        self.assertEqual(a._automatic_handwritten_4x4.call_count,1)

if __name__=='__main__':unittest.main()
