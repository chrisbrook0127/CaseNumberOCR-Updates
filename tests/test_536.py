import tempfile, threading, unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
from PIL import Image
import test_rules

class SpanAndLearning(unittest.TestCase):
    app=test_rules.Rules.app
    sequence=test_rules.Rules.sequence

    def test_two_ambiguous_middle_files_corrected(self):
        a=self.sequence(['08-84','08-44','08-44','08-84'], **{
            '1':{'scan_ambiguous':True,'confirmed':'','auto_labeled':False,'manual_approved':False},
            '2':{'scan_ambiguous':True,'confirmed':'','auto_labeled':False,'manual_approved':False}})
        for i in ('1','2'):
            self.assertEqual(a.records[i]['confirmed'],'08-84')
            self.assertTrue(a.records[i]['sequence_inferred'])
            self.assertFalse(a.records[i]['scan_ambiguous'])
            self.assertEqual(a.records[i]['sequence_anchors'],['0','3'])

    def test_long_span_and_filename_gaps(self):
        a=self.sequence(['08-1011','08-11',None,'08-101','08-1','08-1011'])
        self.assertTrue(all(a.records[str(i)]['confirmed']=='08-1011' for i in range(6)))
        for i,r in a.records.items():r['path']=a.folder/f'Image{int(i)*7+1000}.pdf'
        a._apply_sequence_consensus()
        self.assertTrue(all(r['confirmed']=='08-1011' for r in a.records.values()))

    def test_single_confirmed_end_does_not_fill_span(self):
        a=self.sequence(['08-84','08-44','08-44','08-84'], **{
            '0':{'scan_ambiguous':True,'confirmed':'','manual_approved':False,'auto_labeled':False}})
        self.assertFalse(a.records['1'].get('sequence_inferred',False))

    def learning_app(self,folder):
        a=self.app();a.folder=Path(folder);a.learning_dir=a.folder/'learning'
        a.data_lock=threading.RLock();a.reader=None;a.learning_data={'examples':[]}
        a._persist_learning_data=Mock();a._refresh_learning_stats=Mock();a._refresh_learning_button_state=Mock();a._diag_sync_shared=Mock()
        return a

    def test_corrected_digit_retains_original_number_box(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.learning_app(d)
            page=np.full((300,600,3),255,dtype=np.uint8)
            page[40:70,300:400]=0
            r={'path':a.folder/'test.pdf','candidate':'08-44','scan_source':'incident',
               'snippet':(0,page,'',(100,20,500,100),[{'number':'08-44','rect':(300,40,400,70)}])}
            a._save_learning_example(r,'08-84')
            ex=a.learning_data['examples'][0]
            self.assertEqual(ex['confirmed_case_number'],'08-84')
            self.assertEqual(ex['ocr_candidate'],'08-44')
            self.assertEqual(ex['crop_origin'],'candidate-number-box')
            with Image.open(a.learning_dir/ex['image']) as im:self.assertEqual(im.size,(116,40))

    def test_drawn_field_saved_without_rerunning_ocr(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.learning_app(d);a.reader=Mock()
            a._find_case_field_label_regions=Mock(return_value=[(100,40,250,70,'incident')])
            r={'path':a.folder/'test.pdf','candidate':'08-44','scan_source':'incident',
               'manual_crop_origin':'drawn','manual_crop_rect':(90,20,450,100),
               'snippet':(0,np.zeros((300,600,3),dtype=np.uint8),'',(90,20,450,100),
                          [{'number':'08-84','rect':(500,200,580,230)}])}
            a._save_learning_example(r,'08-84')
            ex=a.learning_data['examples'][0]
            self.assertEqual(ex['crop_origin'],'manual-field')
            with Image.open(a.learning_dir/ex['image']) as im:self.assertEqual(im.size,(388,98))
            a._find_case_field_label_regions.assert_not_called()

    def test_missing_crop_reports_failure_without_example(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.learning_app(d)
            with self.assertRaisesRegex(ValueError,'No usable learning crop'):
                a._save_learning_example({'path':a.folder/'test.pdf','candidate':'08-44'},'08-84')
            self.assertEqual(a.learning_data['examples'],[])
            a._persist_learning_data.assert_not_called()
