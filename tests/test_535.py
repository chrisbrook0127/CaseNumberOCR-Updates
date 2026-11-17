from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import test_rules
m = test_rules.m

class AdjustableRules(unittest.TestCase):
    app = test_rules.Rules.app
    sequence = test_rules.Rules.sequence

    def test_threshold_bounds_and_inclusive_either_engine(self):
        a = self.app()
        for selected, expected in [(0,75),(75,75),(85,85),(95,95),(100,100),(200,100),('bad',95),('nan',95)]:
            a.auto_threshold_var = SimpleNamespace(get=lambda: selected)
            self.assertEqual(a._get_auto_threshold(), expected)
            for scores in ({'EasyOCR':expected,'TrOCR':0},{'EasyOCR':0,'TrOCR':expected}):
                self.assertIsNone(a._auto_label_block_reason(max(scores.values()),False,'08-101101',scores))
            self.assertIsNotNone(a._auto_label_block_reason(expected-.01,True,'08-101101'))
            self.assertIsNotNone(a._auto_label_block_reason(100,True,'09-101101'))

    def test_high_score_without_confirmation_is_not_anchor(self):
        a = self.sequence(['08-1111',None,'08-1111'], **{'0':{'confirmed':'','manual_approved':False,'auto_labeled':False}})
        self.assertEqual(a.records['1']['confirmed'],'')

    def test_confirmed_75_percent_and_manual_can_anchor_thousands(self):
        a = self.sequence(['08-1111','08-111','08-1111'], **{'0':{'score':75},'2':{'score':0,'auto_labeled':False}})
        self.assertEqual(a.records['1']['confirmed'],'08-1111')
        self.assertTrue(a.records['1']['sequence_inferred'])

    def test_manual_anchor_uses_confirmed_not_old_candidate(self):
        a = self.sequence(['08-1111',None,'08-1111'], **{'0':{'candidate':'08-111','auto_labeled':False}})
        self.assertEqual(a.records['1']['confirmed'],'08-1111')

    def test_multiple_manual_conflicts_require_review(self):
        a = self.sequence(['08-1111','08-111','08-112','08-1111'], **{'1':{'auto_labeled':False},'2':{'auto_labeled':False}})
        self.assertTrue(all(a.records[i]['sequence_conflict'] for i in ('1','2')))

    def test_button_blocks_busy_and_unfinished_scan(self):
        a = self.app();a.folder=Path('/synthetic');a.records={'x':{}}
        a._apply_sequence_consensus=Mock()
        with patch.object(m.messagebox,'showinfo'):
            a.busy=True;a._run_before_after()
            a.busy=False;a._sequence_review_pending=True;a._run_before_after()
        a._apply_sequence_consensus.assert_not_called()
        self.assertTrue(a._sequence_review_pending)

    def test_button_commits_edit_then_checks(self):
        a=self.app();a.folder=Path('/synthetic');a.records={'x':{}}
        calls=[]
        a._scan_case_commit=lambda: calls.append('save')
        a._apply_sequence_consensus=lambda: calls.append('check') or 1
        a._run_before_after()
        self.assertEqual(calls,['save','check'])
        a._scan_case_commit=lambda:False
        a._run_before_after()
        self.assertEqual(calls,['save','check'])
