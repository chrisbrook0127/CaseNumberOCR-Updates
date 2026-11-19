import tempfile,unittest,queue,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_rules
import test_export_completed
m=test_rules.m

class AnchorRevalidation(unittest.TestCase):
    app=test_rules.Rules.app
    sequence=test_rules.Rules.sequence

    def test_zero_format_is_filename_only(self):
        for source,expected in [('08-0084','08-84'),('08-0000000','08-0'),('08-0101','08-101'),('08-1010','08-1010'),('08-1000','08-1000'),('08-848484','08-848484')]:
            self.assertEqual(m.normalize_case_number(source),source)
            self.assertEqual(m._mfcase_export_case_name(source),expected)

    def test_differently_padded_anchors_do_not_match(self):
        a=self.sequence(['08-0084',None,'08-84'])
        self.assertFalse(a.records['1'].get('manual_approved'))
        a=self.sequence(['08-0084',None,None,'08-0084'])
        for i in ('1','2'):self.assertEqual(a.records[i]['confirmed'],'08-0084')

    def test_changed_anchor_withdraws_old_inference(self):
        a=self.sequence(['08-84','08-44','08-84'])
        a.records['0'].update(confirmed='08-85',candidate='08-85',auto_labeled=False)
        a._apply_sequence_consensus()
        middle=a.records['1']
        self.assertEqual(middle['confirmed'],'');self.assertEqual(middle['candidate'],'08-44')
        self.assertFalse(middle['manual_approved']);self.assertFalse(middle['auto_labeled'])
        a.records['2'].update(confirmed='08-85',candidate='08-85',auto_labeled=False)
        a._apply_sequence_consensus()
        self.assertEqual(middle['confirmed'],'08-85')
        self.assertEqual(middle['sequence_original_candidate'],'08-44')

    def test_removed_anchor_withdraws_blank_inference(self):
        a=self.sequence(['08-84',None,'08-84'])
        del a.records['0'];a._apply_sequence_consensus()
        self.assertEqual(a.records['1']['candidate'],'');self.assertEqual(a.records['1']['confirmed'],'')

    def test_repeated_check_idempotent_and_preserves_original(self):
        a=self.sequence(['08-84','08-44','08-84'])
        self.assertEqual(a._apply_sequence_consensus(),0)
        self.assertEqual(a.records['1']['sequence_original_candidate'],'08-44')

    def test_renamed_inference_invalidated_without_touching_file(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.sequence(['08-84','08-44','08-84']);a.folder=Path(d)
            for iid,r in a.records.items():r['path']=a.folder/f'{iid}.pdf';r['path'].write_bytes(b'original')
            middle=a.records['1'];middle['exported']=True
            a.records['0']['confirmed']='08-85';a._apply_sequence_consensus()
            self.assertFalse(middle['manual_approved']);self.assertFalse(middle['exported'])
            self.assertEqual(middle['path'].read_bytes(),b'original')

    def test_draft_invalidates_dependent_approval(self):
        a=self.sequence(['08-84',None,'08-84']);a.status=Mock();a._save_state.return_value=True
        a._save_scan_draft('0',SimpleNamespace(get=lambda:'08-85'))
        self.assertFalse(a.records['1']['manual_approved'])
        self.assertIsNone(a._sequence_case_number(a.records['0']))

    def test_green_accept_reruns_sequence_automatically(self):
        a=self.sequence(['08-84',None,'08-85']);a.status=Mock();a._export_in_flight_iids=set()
        a._save_state.return_value=True;a._completed_thumbnail_path=Mock(return_value=None)
        a._save_learning_example=Mock();a._ensure_preview=Mock();a.completed_edit_drafts={}
        a._close_completed_edit=Mock();a._insert_completed_row=Mock();a.completed_send_status=Mock()
        a.records['2']['case_draft']='08-84';a._quick_approve('2')
        self.assertEqual(a.records['1']['confirmed'],'08-84')

    def test_rename_always_checks_before_selecting_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            a=test_export_completed.CompletedExport().export_app(Path(d))
            seen=[]
            def check():
                seen.append('check');a.records['approved1'].update(confirmed='',manual_approved=False)
            a._apply_sequence_consensus=Mock(side_effect=check)
            class ImmediateThread:
                def __init__(self,target,args,**kw):self.target=target;self.args=args
                def start(self):self.target(*self.args)
            with patch.object(m.threading,'Thread',ImmediateThread):a.export_completed()
            self.assertEqual(seen,['check'])
            self.assertTrue((a.folder/'approved1.pdf').exists())
            self.assertTrue((a.folder/'08-101.pdf').exists())

    def test_rename_aborts_on_sequence_failure(self):
        with tempfile.TemporaryDirectory() as d:
            a=test_export_completed.CompletedExport().export_app(Path(d))
            a._apply_sequence_consensus=Mock(side_effect=OSError('cannot save'))
            a.export_completed()
            self.assertTrue(a._sequence_review_pending)
            self.assertTrue((a.folder/'approved1.pdf').exists())
            self.assertFalse(a.export_busy)

    def test_padded_and_unpadded_rename_safely_to_same_case(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app();a.queue=queue.Queue();folder=Path(d)
            one=folder/'Image1.pdf';two=folder/'Image2.pdf'
            one.write_bytes(b'one');two.write_bytes(b'two')
            a._export_worker(folder,[('1',one,'08-0084'),('2',two,'08-84')])
            self.assertEqual((folder/'08-84.pdf').read_bytes(),b'one')
            self.assertEqual((folder/'08-84 (1).pdf').read_bytes(),b'two')

    def test_revalidation_even_when_ocr_auto_confirm_off(self):
        a=self.sequence(['08-84',None,'08-84'])
        a.auto_label_var.get=lambda:False;a.records['0']['confirmed']='08-85'
        a._apply_sequence_consensus();self.assertFalse(a.records['1']['manual_approved'])

    def test_unverify_preserves_learning_and_withdraws_dependents(self):
        a=self.sequence(['08-84',None,'08-84']);a.status=Mock();a._export_in_flight_iids=set()
        a._completed_thumbnail_path=Mock(return_value=None);a.completed_photo_refs={}
        a.completed_checked_iids=set();a.completed_edit_drafts={};a.completed_unverify_buttons={}
        a._close_completed_edit=Mock();a._remove_learning_data_for_file=Mock();a._update_scan_file_count=Mock()
        a._unverify_record('0')
        a._remove_learning_data_for_file.assert_not_called()
        self.assertFalse(a.records['1']['manual_approved'])

    def test_evidence_survives_save_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.sequence(['08-84','08-44','08-84']);a.folder=Path(d)
            for iid,r in a.records.items():r['path']=a.folder/f'{iid}.pdf'
            self.assertTrue(m.App._save_state(a))
            saved=json.loads((a.folder/'.case_ocr_state.json').read_text())['files']
            self.assertEqual(saved['1.pdf']['sequence_original_candidate'],'08-44')
            self.assertEqual(saved['1.pdf']['sequence_original_score'],100)
            a.records={str(i):dict(saved[f'{i}.pdf'],path=a.folder/f'{i}.pdf') for i in range(3)}
            a.records['0']['confirmed']='08-85';a._apply_sequence_consensus()
            self.assertFalse(a.records['1']['manual_approved'])
            self.assertEqual(a.records['1']['candidate'],'08-44')

    def test_failed_state_write_blocks_inference_commit(self):
        a=self.sequence(['08-84',None,'08-84']);a._save_state.return_value=False
        with self.assertRaises(OSError):a._apply_sequence_consensus()
