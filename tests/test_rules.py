import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import case_number_ocr as m

class Rules(unittest.TestCase):
    def app(self):
        a=m.App.__new__(m.App)
        a.auto_label_var=SimpleNamespace(get=lambda:True)
        a.busy=a.export_busy=a.trocr_training=False
        a.completed_tree=Mock();a.completed_tree.exists.return_value=False
        a._diag_event=Mock();a._log=Mock()
        for method in ('_insert_scan_row','_refresh_review_controls','_update_completed_count','_save_state','_move_to_completed'):
            setattr(a,method,Mock())
        return a
    def sequence(self,values,**overrides):
        a=self.app();a.folder=Path('/synthetic');a.records={}
        for i,v in enumerate(values):
            a.records[str(i)]={'path':a.folder/f'{i:03d}.pdf','candidate':v or '',
                'confirmed':v or '', 'score':100,'engine_scores':{'EasyOCR':100,'TrOCR':99},
                'engine_agreement':True,'scan_source':'incident','manual_approved':bool(v),
                'auto_labeled':bool(v),'status':'Scanned'}
        for k,attrs in overrides.items():a.records[k].update(attrs)
        a.scan_items=list(a.records.items())
        a._apply_sequence_consensus()
        return a
    def test_complete_numbers(self):
        for s in ('08-01114','08-101','08-1','08-1234567'):
            with self.subTest(s=s): self.assertEqual(m.CASE_RE.findall(s),[s])
    def test_no_partial_matches(self):
        for s in ('08-0I114','08-10I','08-l01','08-01 114','08-01 I14','08-101|','08-12345678','108-101','X08-101','08-101A'):
            with self.subTest(s=s): self.assertEqual(m.CASE_RE.findall(s),[])
    def test_labels_and_punctuation(self):
        self.assertEqual(m.INCIDENT_NUMBER_RE.findall('INCIDENT NO. 08 - 101; OFFENSE'),['08 - 101'])
        self.assertEqual(m.CASE_RE.findall('08-101 INCIDENT NO.'),['08-101'])
    def test_other_year(self):
        self.assertIsNone(m.CASE_RE.fullmatch('09-101'))
    def test_zeroes_preserved_for_export(self):
        self.assertEqual(m.normalize_case_number('08-01114'),'08-01114')
        self.assertEqual(m._mfcase_export_case_name('08-01114'),'08-01114')
        self.assertEqual(m._mfcase_export_case_name('08-101'),'08-101')
    def test_dual_engine_boundaries(self):
        a=self.app()
        for easy,trocr,allowed in [(96,76,True),(76,96,True),(100,75,False),(75,100,False),(95,95,False),(95.01,75.01,True),(100,0,False),(94,94,False)]:
            with self.subTest(easy=easy,trocr=trocr):
                result=a._auto_label_block_reason(max(easy,trocr),True,'08-101',{'EasyOCR':easy,'TrOCR':trocr})
                self.assertEqual(result is None,allowed)
    def test_disagreement_and_missing_scores(self):
        a=self.app()
        self.assertIsNotNone(a._auto_label_block_reason(100,False,'08-101',{'EasyOCR':100,'TrOCR':100}))
        self.assertIsNotNone(a._auto_label_block_reason(100,True,'08-101'))
    def test_auto_off(self):
        a=self.app();a.auto_label_var.get=lambda:False
        self.assertIsNotNone(a._auto_label_block_reason(100,True,'08-101',{'EasyOCR':100,'TrOCR':100}))
    def test_merge_keeps_independent_scores(self):
        hits=m.App._merge_case_hits([{'number':'08-101','score':98,'engines':['EasyOCR'],'engine_scores':{'EasyOCR':98}}],
            [{'number':'08-101','score':80,'engines':['TrOCR'],'engine_scores':{'TrOCR':80}}])
        self.assertEqual(hits[0]['engine_scores'],{'EasyOCR':98,'TrOCR':80})
    def test_different_numbers_never_share_scores(self):
        hits=m.App._merge_case_hits([{'number':'08-101','score':98,'engines':['EasyOCR'],'engine_scores':{'EasyOCR':98}}],
            [{'number':'08-111','score':99,'engines':['TrOCR'],'engine_scores':{'TrOCR':99}}])
        self.assertEqual(len(hits),2)
        self.assertNotIn('TrOCR',hits[0]['engine_scores'])
    def test_100_percent_sandwich_is_corrected(self):
        a=self.sequence(['08-101','08-111','08-101'])
        self.assertEqual(a.records['1']['confirmed'],'08-101')
        self.assertTrue(a.records['1']['sequence_inferred'])
        self.assertEqual(a.records['1']['sequence_original_candidate'],'08-111')
    def test_multiple_same_wrong_reads_require_review(self):
        a=self.sequence(['08-101','08-111','08-111','08-101'])
        for i in ('1','2'):
            self.assertEqual(a.records[i]['confirmed'],'')
            self.assertTrue(a.records[i]['sequence_conflict'])
    def test_multiple_different_wrong_reads_require_review(self):
        a=self.sequence(['08-101','08-111','08-112','08-101'])
        self.assertTrue(all(a.records[i]['sequence_conflict'] for i in ('1','2')))
    def test_alternating_conflicts_do_not_cascade(self):
        a=self.sequence(['08-101','08-111','08-101','08-111','08-101'])
        for i in ('1','2','3'):
            self.assertFalse(a.records[i]['manual_approved'])
            self.assertEqual(a.records[i]['confirmed'],'')
    def test_blank_span(self):
        a=self.sequence(['08-101',None,None,'08-101'])
        self.assertEqual([a.records[i]['confirmed'] for i in ('1','2')],['08-101']*2)
    def test_weak_wrong_plus_blanks(self):
        a=self.sequence(['08-101',None,'08-111',None,'08-101'],**{'2':{'engine_scores':{},'manual_approved':False,'auto_labeled':False}})
        self.assertEqual(a.records['2']['confirmed'],'08-101')
    def test_different_boundaries_do_not_infer(self):
        a=self.sequence(['08-101',None,'08-102'])
        self.assertEqual(a.records['1']['confirmed'],'')
    def test_normal_transition_unchanged(self):
        a=self.sequence(['08-101','08-101','08-102','08-102'])
        self.assertTrue(all(r['confirmed']==r['candidate'] and not r.get('sequence_conflict') for r in a.records.values()))
    def test_manual_correction_wins(self):
        a=self.sequence(['08-101','08-111','08-101'],**{'1':{'auto_labeled':False}})
        self.assertEqual(a.records['1']['confirmed'],'08-111')
        self.assertFalse(a.records['1'].get('sequence_inferred',False))
    def test_manual_correction_can_anchor(self):
        a=self.sequence(['08-101',None,'08-101'],**{'0':{'auto_labeled':False,'score':0,'engine_scores':{}}})
        self.assertEqual(a.records['1']['confirmed'],'08-101')
    def test_missing_second_engine_cannot_anchor(self):
        a=self.sequence(['08-101',None,'08-101'],**{'0':{'engine_scores':{'EasyOCR':100}}})
        self.assertEqual(a.records['1']['confirmed'],'')
    def test_unscanned_slot_blocks_inference(self):
        a=self.sequence(['08-101',None,'08-101'],**{'1':{'status':'Not scanned'}})
        self.assertEqual(a.records['1']['confirmed'],'')
    def test_inferred_value_never_anchors(self):
        a=self.sequence(['08-101',None,'08-101'],**{'0':{'sequence_inferred':True}})
        self.assertEqual(a.records['1']['confirmed'],'')
    def test_busy_does_not_run_sequence(self):
        a=self.app();a.busy=True
        self.assertEqual(a._apply_sequence_consensus(),0)
    def test_split_confusable_incident_rejects_prefix(self):
        def box(x,text):return ([[x,0],[x+30,0],[x+30,12],[x,12]],text,.99)
        hits=m.App._incident_hits_from_detections([box(110,'08-01'),box(145,'I14')],(0,0,100,12),(1,0),(1,0))
        self.assertEqual(hits,[])
    def test_split_digits_preserve_full_number(self):
        def box(x,text):return ([[x,0],[x+30,0],[x+30,12],[x,12]],text,.99)
        hits=m.App._incident_hits_from_detections([box(110,'08-01'),box(145,'114')],(0,0,100,12),(1,0),(1,0))
        self.assertEqual(hits[0]['number'],'08-01114')
    def test_perfect_easyocr_still_calls_trocr(self):
        a=self.app();a.reader=Mock();a.reader.readtext.return_value=[([[110,10],[180,10],[180,25],[110,25]],'08-101',1.0)]
        a._trocr_read_scored=Mock(return_value=('08-101',80))
        hits=a._find_case_candidates(np.zeros((80,250,3),dtype=np.uint8))
        a._trocr_read_scored.assert_called()
        self.assertEqual(hits[0]['engine_scores'],{'EasyOCR':100,'TrOCR':80})
    def test_case_double_click_does_not_open_review(self):
        a=self.app();a.tree=Mock();a.tree.identify_row.return_value='x';a.tree.identify_column.return_value='#4'
        a.records={'x':{}};a._start_scan_case_edit=Mock();a._show_review_item=Mock()
        self.assertEqual(a._unified_double_click(SimpleNamespace(x=1,y=2)),'break')
        a._start_scan_case_edit.assert_called_once_with('x');a._show_review_item.assert_not_called()
    def test_filename_double_click_opens_review(self):
        a=self.app();a.tree=Mock();a.tree.identify_row.return_value='x';a.tree.identify_column.return_value='#2'
        a.records={'x':{'path':Path('/synthetic/001.pdf'),'status':'Scanned'}};a._show_review_item=Mock()
        a._unified_double_click(SimpleNamespace(x=1,y=2));a._show_review_item.assert_called_once_with(0)
    def test_inline_save_preserves_manual_and_learning_label(self):
        a=self.app();a.records={'x':{'path':Path('/synthetic/001.pdf'),'confirmed':'08-111','auto_labeled':True,'manual_approved':True}}
        a._export_in_flight_iids=set();a._completed_thumbnail_path=Mock(return_value=None)
        a._save_learning_label_for_file=Mock();a.completed_edit_drafts={};a._close_completed_edit=Mock()
        a._insert_completed_row=Mock();a.status=Mock();a.completed_send_status=Mock()
        a._save_completed_case_edit('x',SimpleNamespace(get=lambda:'08-01114'))
        self.assertEqual(a.records['x']['confirmed'],'08-01114')
        self.assertFalse(a.records['x']['auto_labeled'])
        a._save_learning_label_for_file.assert_called_once_with('001.pdf','08-01114')
        a._save_state.assert_called()
    def test_unsaved_edit_blocks_send(self):
        a=self.app();a._scan_case_editor=Mock();a.status=Mock();a.export=Mock()
        m.App.export(a);a.export.assert_not_called()

    def test_two_different_neighbors_require_manual(self):
        a=self.sequence(['08-100','08-101','08-102'])
        self.assertEqual(a.records['1']['confirmed'],'')
        self.assertTrue(a.records['1']['sequence_conflict'])

    def test_saved_engine_scores_and_manual_correction_roundtrip(self):
        import json
        a=self.app()
        with tempfile.TemporaryDirectory() as d:
            a.folder=Path(d)
            a._sequence_review_pending=True
            a.records={'x':{'path':a.folder/'test.pdf','confirmed':'08-01114',
                'manual_approved':True,'auto_labeled':False,
                'engine_scores':{'EasyOCR':100,'TrOCR':80}}}
            self.assertTrue(m.App._save_state(a))
            saved=json.loads((a.folder/'.case_ocr_state.json').read_text())['files']['test.pdf']
            self.assertEqual(saved['confirmed'],'08-01114')
            self.assertTrue(saved['sequence_review_pending'])
            self.assertFalse(saved['auto_labeled'])
            self.assertEqual(saved['engine_scores'],{'EasyOCR':100,'TrOCR':80})

    def test_two_complete_cases_remain_conflicting(self):
        self.assertEqual(m.INCIDENT_NUMBER_RE.findall('08-101 08-102'),['08-101','08-102'])
        def box(x,text):return ([[x,0],[x+40,0],[x+40,12],[x,12]],text,.99)
        hits=m.App._incident_hits_from_detections([box(110,'08-101'),box(155,'08-102')],(0,0,100,12),(1,0),(1,0))
        self.assertEqual([h['number'] for h in hits],['08-101','08-102'])

if __name__=='__main__':unittest.main()
