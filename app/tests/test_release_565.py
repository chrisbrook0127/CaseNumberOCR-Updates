from pathlib import Path
import unittest,tempfile
from unittest.mock import Mock
import pymupdf
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import case_number_ocr as m

def hit(n,score,rect,label=None,engine='TrOCR'):
    return dict(number=n,score=score,rect=rect,box_rect=rect,box_verified=True,
        label_type=label,engines=[engine],engine_scores={engine:score})
class MFCase565Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=self.path=Path(self.tmp.name)/'Image9999.pdf'
        doc=pymupdf.open();doc.new_page(width=612,height=792);doc.save(p);doc.close()
        self.app=m.App.__new__(m.App)
        self.app._diag_event=Mock();self.app._log=Mock()
        self.app.stop_event=Mock();self.app.stop_event.is_set.return_value=False
        self.app.pause_event=Mock();self.app.pause_event.wait.return_value=True
    def read(self,hits):
        self.app._automatic_handwritten_4x4=Mock(return_value=hits)
        return self.app._ocr_pdf(self.path)
    def test_100pct_correct_header_over_unrelated_100pct_lower(self):
        good=hit('08-375',100,(1090,55,1215,105),'incident','EasyOCR')
        bad=hit('08-266',100,(1025,145,1155,189),'incident','TrOCR')
        result=self.read([bad,good]);self.assertEqual(result[0],'08-375');self.assertEqual(result[4],100)
        self.assertNotIn('conflicting OCR reads',result[2]);self.assertEqual(len(result[1][4]),1)
        self.assertIsNone(self.app._auto_label_block_reason(result[4],False,result[0]))
    def test_true_printed_98_over_other_99_on_lower_line(self):
        strong=hit('08-0367',98,(1070,53,1207,106),'incident','EasyOCR')
        false=hit('08-2009',99.2,(1074,139,1203,173),'incident','TrOCR')
        extra=hit('08-357',66,(1080,60,1200,104),'incident','TrOCR')
        result=self.read([false,strong,extra]);self.assertEqual(result[0],'08-0367');self.assertNotIn('conflicting',result[2])
    def test_genuine_two_95pct_models_on_same_box_remain_review(self):
        first=hit('08-287',97,(1090,55,1220,105),'incident','TrOCR')
        second=hit('08-297',97.5,(1092,56,1218,106),'incident','EasyOCR')
        result=self.read([first,second]);self.assertIn('conflicting OCR reads',result[2]);self.assertIn('08-287',result[2]);self.assertIn('08-297',result[2])
    def test_95_literal_printed_quashes_model_only_alternative(self):
        real=hit('08-0367',98.6,(1085,55,1215,108),'incident','EasyOCR')
        bad=hit('08-357',97.9,(1085,55,1215,108),'incident','TrOCR')
        result=self.read([real,bad]);self.assertEqual(result[0],'08-0367');self.assertNotIn('conflicting OCR reads',result[2])
    def test_4x4_rejects_off_quarter(self):
        real=hit('08-375',99,(1090,55,1215,105),'incident','TrOCR')
        fake=hit('08-266',100,(300,990,480,1050),'incident','EasyOCR')
        result=self.read([real,fake]);self.assertEqual(result[0],'08-375')
    def test_auto_prefix_hard_gate(self):
        invalid=hit('266',100,(1090,55,1215,105),'incident','TrOCR')
        result=self.read([invalid]);self.assertEqual(result[0],'')
    def test_many_low_confidence_08_guesses_do_not_block_real_98(self):
        real=hit('08-0367',98,(1070,53,1210,103),'incident','EasyOCR')
        lower=[hit(f'08-{2000+i}',90+i/20,(1085,180+i*25,1210,195+i*25),None,'TrOCR') for i in range(6)]
        result=self.read(lower+[real])
        self.assertEqual(result[0],'08-0367')
        self.assertNotIn('conflicting OCR reads',result[2])
    def test_actual_4x4_only_manual_outside(self):
        outside=hit('08-266',100,(400,900,480,970),None,'EasyOCR')
        result=self.read([outside]);self.assertEqual(result[0],'')
        self.assertIn('MANUAL REVIEW',result[2])
    def test_99_handwritten_single_always_qualifies(self):
        result=self.read([hit('08-0368',99.9,(1080,55,1215,111),None,'TrOCR')])
        self.assertEqual(result[0],'08-0368')
        self.assertEqual(result[4],99.9)
        self.assertIsNone(self.app._auto_label_block_reason(result[4],False,result[0]))
    def test_either_engine_independently_can_approve(self):
        for engine in ('EasyOCR','TrOCR'):
            with self.subTest(engine=engine):
                res=self.read([hit('08-375',99,(1090,55,1215,105),None,engine)])
                self.assertEqual(res[0],'08-375');self.assertIsNone(self.app._auto_label_block_reason(res[4],False,res[0]))
if __name__=='__main__':unittest.main(verbosity=2)
