"""MFCase v5.5.5: strict OCR, bounded fallback, timing and safe scan/learning handoff."""
import tempfile, unittest, threading, sys, time
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import case_number_ocr as m

class PerformanceSafety(unittest.TestCase):
    def app(self):
        a=m.App.__new__(m.App)
        a._diag_event=Mock()
        a._log=Mock()
        a.learning_data={"field_regions_full_page":[]}
        a.stop_event=threading.Event()
        a.pause_event=threading.Event(); a.pause_event.set()
        a.scan_year_prefix='08'
        a._perf_stages={}
        a._find_case_field_label_regions=Mock(return_value=[])
        a._find_case_candidates=Mock(return_value=[])
        a._diag_field_filter=lambda hits,kind:hits
        a._prefer_case_margin=lambda hits,kind,image:hits
        a._resolve_ocr_alternates=lambda hits,kind,best:hits
        return a

    def pdf(self, folder, n):
        path=Path(folder)/'test.pdf'
        with m.pymupdf.open() as doc:
            for _ in range(n):doc.new_page(width=612,height=792)
            doc.save(path)
        return path

    def test_two_learned_fallback_locations_max_per_pdf(self):
        a=self.app()
        a.learning_data['field_regions_full_page']=[
            {'rect_norm':[.03,(.42+i*.03),.21,(.44+i*.03)]} for i in range(12)]
        calls=[]
        def detector(image,x_offset=0,y_offset=0,**kwargs):
            calls.append('learned' if y_offset>634 else '4x4')
            return []
        a._find_case_candidates=detector
        with tempfile.TemporaryDirectory() as d:
            a._ocr_pdf(self.pdf(d,4))
        self.assertEqual(calls.count('learned'),2,calls)
        self.assertEqual(calls.count('4x4'),8,calls)
        self.assertEqual(calls[:2],['4x4','4x4'])
        results=[c for c in a._diag_event.call_args_list if c.args[0]=='performance_file']
        self.assertEqual(len(results),1)
        self.assertEqual(results[0].kwargs['stages']['learned_fallback']['calls'],2)
        self.assertEqual(results[0].kwargs['stages']['pdf_render']['calls'],4)

    def test_clear_08_case_prevents_all_learned_fallback_and_keeps_fast_exit(self):
        a=self.app()
        a.learning_data['field_regions_full_page']=[{'rect_norm':[.0,.5,.2,.6]}]
        a._find_case_field_label_regions=Mock(return_value=[(20,25,115,45,'incident')])
        a._read_incident_to_right=Mock(return_value=[{
            'number':'08-185','score':99.7,'rect':(130,25,240,55),
            'box_rect':(130,25,240,55),'box_verified':True,
            'engines':['EasyOCR'],'engine_scores':{'EasyOCR':99.7},'label_type':'incident'}])
        with tempfile.TemporaryDirectory() as d:
            result=a._ocr_pdf(self.pdf(d,50))
        self.assertEqual(result[0],'08-185')
        a._find_case_candidates.assert_not_called()
        self.assertEqual(a._find_case_field_label_regions.call_count,1)

    def test_no_learning_overlap_when_scan_is_requested(self):
        a=self.app()
        a._startup_ready=True; a.reader=object();a.trocr_model=object()
        a.learning_dir=Path('/not-used')
        a.busy=False; a.export_busy=False
        a._training_blocks_work=lambda:False
        a._background_learning_active=lambda:True
        a._yield_background_learning=Mock()
        a._training_worker_done=threading.Event()
        a._scan_waiting_for_training=False
        a.root=Mock();a.status=Mock()
        a._start_scan()
        self.assertTrue(a._scan_waiting_for_training)
        a._yield_background_learning.assert_called_with('scanning')
        a.root.after.assert_called_once()
        self.assertFalse(a.busy)

    def test_sent_history_does_not_stat_network_pdf_per_anchor(self):
        a=self.app()
        a.folder=Path('/arbitrary/root')
        a.records={}
        a.sent_history={f'Image{i}.pdf':{
            'destination_name':f'Image{i}_08-10.pdf', 'confirmed':'08-10',
            'manual_approved':True,'auto_labeled':False
        } for i in range(100)}
        old_is_file=Path.is_file
        try:
            Path.is_file=lambda path: (_ for _ in ()).throw(AssertionError('Unexpected per-row network stat'))
            ordered=a._sequence_order_with_sent_history()
        finally:
            Path.is_file=old_is_file
        self.assertEqual(len(ordered),100)

    def test_planner_resolves_long_brackets_and_conflicting_auto(self):
        a=self.app()
        order=[]
        for i in range(101):
            num='08-0167' if i in (0,100) else ('08-999' if i==50 else '')
            order.append((str(i),dict(
                confirmed=num,manual_approved=i in (0,100),
                auto_labeled=i==50,sequence_inferred=False,
                case_draft='',exported=False,status='Review')))
        start=time.perf_counter()
        plan,conflicts=a._sequence_consensus_plan(order)
        self.assertLess(time.perf_counter()-start,1.0)
        self.assertEqual(len(plan),99)
        self.assertFalse(conflicts)
        self.assertEqual(plan[50][0],'08-167')
        self.assertEqual(plan[50][1],['0','100'])

    def test_thousand_file_planner_fast(self):
        a=self.app()
        order=[]
        for i in range(1000):
            num='08-10' if i%3 else ''
            order.append((str(i),dict(confirmed=num,
                manual_approved=bool(num and i%9==1),
                auto_labeled=bool(num and i%9!=1),
                status='Review',exported=False)))
        start=time.perf_counter();a._sequence_consensus_plan(order);duration=time.perf_counter()-start
        self.assertLess(duration,1.0, f'planner took {duration:.3f}s')

    def test_timing_easyocr_and_trocr_are_accounted_for(self):
        a=self.app()
        class Reader:
            def readtext(self,*args,**kwargs):return [('box','08-100',.99)]
            def recognize(self,*args,**kwargs):return [('box','08-100',.99)]
        a.reader=Reader()
        a._timed_easyocr_readtext('test')
        a._timed_easyocr_recognize('test')
        self.assertEqual(a._perf_stages['easyocr'][1],2)
        self.assertGreaterEqual(a._perf_stages['easyocr'][0],0)
        a._trocr_read_scored_impl=Mock(return_value=('08-100',99.5))
        self.assertEqual(a._trocr_read_scored('crop')[0],'08-100')
        self.assertEqual(a._perf_stages['trocr'][1],1)

if __name__=='__main__':unittest.main(verbosity=2)