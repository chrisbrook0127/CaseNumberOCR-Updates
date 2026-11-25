"""MFCase 5.6.6: minimal OCR decision logic, physical bounds and storage isolation."""
from pathlib import Path
import unittest, tempfile
from unittest.mock import Mock
import numpy as np
import pymupdf
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import case_number_ocr as m


def hit(case, score, rect, engine='TrOCR', label=None):
    return dict(number=case,score=score,rect=rect,box_rect=rect,
                box_verified=True,engines=[engine],
                engine_scores={engine:score},label_type=label)


class SimplifiedOCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'Image0001.pdf'
        doc=pymupdf.open();doc.new_page(width=612,height=792);doc.save(self.path);doc.close()
        self.app=m.App.__new__(m.App)
        self.app._diag_event=Mock()
        self.app._log=Mock()
        self.app.stop_event=Mock();self.app.stop_event.is_set.return_value=False
        self.app.pause_event=Mock();self.app.pause_event.wait.return_value=True

    def scan(self, hits):
        self.app._automatic_handwritten_4x4=Mock(return_value=hits)
        return self.app._ocr_pdf(self.path)

    def test_full_upper_right_quarter_not_tiny_corner(self):
        # This field is past the previous artificial x82% / top24% cutoffs.
        image=np.zeros((1743,1346,3),dtype=np.uint8)
        rect=(758,641,918,698)  # right half and top half, but no old sub-zone
        self.assertTrue(m.App._is_in_4x4_rect(rect,image))
        self.assertTrue(m.App._is_corner_case_rect(rect,image))
        out=self.scan([hit('08-42',96.1,rect,'TrOCR')])
        self.assertEqual(out[0],'08-42')
        self.assertGreaterEqual(out[4],95.0)

    def test_95_0_threshold_either_engine_no_incident_label(self):
        for engine in ('EasyOCR','TrOCR'):
            with self.subTest(engine=engine):
                out=self.scan([hit('08-375',95.0,(1045,58,1190,109),engine)])
                self.assertEqual(out[0],'08-375')
                self.assertIsNone(self.app._auto_label_block_reason(out[4],False,out[0]))

    def test_incident_label_optional_and_secondary(self):
        for label in (None,'incident','case'):
            with self.subTest(label=label):
                out=self.scan([hit('08-888',98,(1075,150,1210,200),'TrOCR',label)])
                self.assertEqual(out[0],'08-888')

    def test_low_confidence_alternatives_do_not_veto_clear_read(self):
        good=hit('08-87',99,(1090,60,1201,100),'TrOCR')
        bad=hit('08-78',55,(1090,60,1201,100),'EasyOCR')
        self.assertEqual(m.App._clear_localized_case([bad,good])['number'],'08-87')

    def test_two_95_percent_same_field_higher_score_wins(self):
        a=hit('08-375',96.1,(1090,65,1215,111),'EasyOCR')
        b=hit('08-376',98.2,(1091,65,1215,111),'EasyOCR-joined')
        self.assertIsNone(m.App._clear_localized_case([a,b]))
        # Actual _ocr_pdf conflict depends on same-field alternate policy.
        result=self.scan([a,b]);self.assertEqual(result[0],'08-376');self.assertNotIn('conflicting OCR reads',result[2])

    def test_invalid_or_outside_quarter_goes_review(self):
        for candidate,rect in [('08-32',(200,80,300,105)),
                               ('09-32',(1100,80,1200,105)),
                               ('32',(1100,80,1200,105))]:
            with self.subTest(candidate=candidate):
                out=self.scan([hit(candidate,100,rect)])
                self.assertEqual(out[0],'')
                self.assertIn('MANUAL REVIEW',out[2])

    def test_scan_not_blocked_by_missing_learning_dir(self):
        a=self.app
        a._scan_case_editor=None
        a._startup_ready=True
        a.reader=object();a.trocr_model=object();a.learning_dir=None
        a.busy=False;a.export_busy=False;a.trocr_training=False
        a._training_blocks_work=lambda:False
        a._background_learning_active=lambda:False
        a.folder=None;a.records={}
        a._scan_blocked=Mock()
        a._start_scan()
        self.assertTrue(a._scan_blocked.called)
        msg=a._scan_blocked.call_args.args[0]
        self.assertIn('SELECT FILES',msg)
        self.assertNotIn('Learning storage',msg)

    def test_95_strict_but_no_engine_agreement_requirement(self):
        self.assertIsNone(self.app._auto_label_block_reason(95,False,'08-123'))
        self.assertIsNotNone(self.app._auto_label_block_reason(94.9,True,'08-123'))
        self.assertIsNotNone(self.app._auto_label_block_reason(100,True,'08-'))

    def test_review_crop_full_page_untouched(self):
        src=Path(m.__file__).read_text(encoding='utf-8')
        # Full-page manual crop path is separate from _ocr_pdf's quarter image.
        self.assertIn('def _ocr_review_crop(',src)
        self.assertIn('def _show_review_item(',src)
        self.assertIn('region=image[:side,offset_x:].copy()',src)
        self.assertNotIn('Learning storage is unavailable. Restore access before scanning.',src)


if __name__=='__main__':unittest.main(verbosity=2)
