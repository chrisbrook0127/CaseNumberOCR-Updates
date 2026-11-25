"""Focused integration checks for approved learning and OCR-budget changes."""
import copy, threading, unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
import case_number_ocr as m

class Release5613(unittest.TestCase):
    def app(self):
        a=m.App.__new__(m.App)
        a.data_lock=threading.RLock();a.learning_data={'examples':[], 'field_regions_full_page':[]}
        a._persist_learning_data=Mock();a._log=Mock();a._perf_stages={}
        return a
    def record(self):
        return dict(path=Path('/roll1/Image1.pdf'),confirmed='08-0084',candidate='08-0084',score=95.,auto_labeled=True,
            snippet=(0,np.zeros((1000,800,3),dtype=np.uint8),'case',(500,20,700,70),[
              dict(number='08-0084',score=95.,box_verified=True,rect=(500,20,700,70))]))
    def test_auto_location_waits_for_send_and_is_not_training_data(self):
        a=self.app();r=self.record();a._stage_auto_location(r)
        a._commit_auto_location(r)
        self.assertNotIn('auto_field_regions_full_page',a.learning_data)
        r['exported']=True;a._commit_auto_location(r)
        self.assertEqual(len(a.learning_data['auto_field_regions_full_page']),1)
        self.assertEqual(a.learning_data['examples'],[])
        self.assertEqual(a.learning_data['field_regions_full_page'],[])
        a._persist_learning_data.assert_called_once()
    def test_reject_changed_inferred_conflicting_and_manual(self):
        for change in ({'confirmed':'08-85'},{'sequence_inferred':True},{'scan_ambiguous':True},
                       {'sequence_conflict':True},{'auto_labeled':False}):
            with self.subTest(change=change):
                a=self.app();r=self.record();a._stage_auto_location(r)
                r.update(exported=True,**change);a._commit_auto_location(r)
                a._persist_learning_data.assert_not_called()
    def test_low_invalid_unverified_or_nonfinite_cannot_stage(self):
        for key,value in [('score',94.99),('score',float('nan')),('confirmed','84'),('confirmed','08-12345678')]:
            a=self.app();r=self.record();r[key]=value;a._stage_auto_location(r)
            self.assertNotIn('pending_auto_location',r)
        a=self.app();r=self.record();r['snippet'][4][0]['box_verified']=False
        a._stage_auto_location(r);self.assertNotIn('pending_auto_location',r)
    def test_normalized_identity_and_folder_deduplication(self):
        a=self.app()
        for folder in ('roll1','roll1','roll2'):
            r=self.record();r['path']=Path('/'+folder+'/Image1.pdf')
            a._stage_auto_location(r);r.update(exported=True,confirmed='08-84')
            a._commit_auto_location(r)
        self.assertEqual(len(a.learning_data['auto_field_regions_full_page']),2)
    def test_manual_geometry_wins_over_many_automatic_samples(self):
        manual={'rect_norm':[.6,.02,.85,.09],'source_pdf':'manual.pdf'}
        data={'field_regions_full_page':[manual],'auto_field_regions_full_page':[
            dict(rect_norm=[.63,.025,.89,.11],source_pdf=str(i)) for i in range(100)]}
        original=copy.deepcopy(data);clusters=m._learned_crop_clusters(data)
        self.assertEqual(clusters[0]['count'],1)
        np.testing.assert_allclose(clusters[0]['rect_norm'],manual['rect_norm'])
        self.assertEqual(data,original)
    def test_auto_only_location_is_available_and_invalidates_cache(self):
        a=self.app();self.assertEqual(a._ranked_learned_scan_zones(800,1000),[])
        r=self.record();a._stage_auto_location(r);r['exported']=True;a._commit_auto_location(r)
        self.assertEqual(len(a._ranked_learned_scan_zones(800,1000)),1)
    def budget(self,a):
        a._page_ocr_budget={'thread':threading.get_ident(),'printed':0,'handwriting':0,'cache':{}}
        a.reader=Mock();a.reader.readtext.return_value=[]
        a._trocr_read_scored_impl=Mock(return_value=('08-84',98.))
    def test_shared_limits_and_no_repeat_model_read(self):
        a=self.app();self.budget(a)
        for i in range(8):
            image=np.full((15,30,3),i,dtype=np.uint8)
            a._trocr_read_scored(image);a._trocr_read_scored(image)
            a._timed_easyocr_readtext(image,detail=1,paragraph=False)
        self.assertEqual(a._trocr_read_scored_impl.call_count,3)
        self.assertEqual(a.reader.readtext.call_count,1)
    def test_manual_thread_is_not_limited_by_scan_budget(self):
        a=self.app();self.budget(a);a._page_ocr_budget['thread']=-1
        for i in range(4):a._trocr_read_scored(np.zeros((10,10),dtype=np.uint8))
        self.assertEqual(a._trocr_read_scored_impl.call_count,4)
    def test_detector_reused_between_learned_zone_and_quarter(self):
        a=self.app();self.budget(a)
        image=np.zeros((500,400,3),dtype=np.uint8)
        a._page_ocr_budget['quarter']=(image,400,0)
        a.reader.readtext.return_value=[([[110,20],[190,20],[190,60],[110,60]],'08-84',.99)]
        hits=a._automatic_detections(image[10:80,100:200],500,10)
        self.assertEqual(hits[0][0][0],[10.,10.])
        self.assertEqual(len(a._automatic_detections(image,400,0)),1)
        a.reader.readtext.assert_called_once()
    def test_spinner_runs_stopping_then_cancels(self):
        a=self.app();a.root=Mock();a.root.after.return_value='timer'
        a.learning_activity=Mock();a.learning_activity.winfo_manager.return_value=''
        a.learning_spinner=Mock();a.learning_activity_label=Mock()
        a.learning_stop_event=threading.Event()
        a._set_background_learning_indicator(True)
        a.learning_spinner.create_arc.assert_called_once()
        a.learning_stop_event.set();a._spin_learning_indicator()
        a.learning_activity_label.config.assert_called_with(text='LEARNING STOPPING')
        a._set_background_learning_indicator(False)
        a.root.after_cancel.assert_called_with('timer')
        a.learning_activity.pack_forget.assert_called_once()

if __name__=='__main__':unittest.main()
