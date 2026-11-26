"""Carry-forward safeguards, ink gating and persistent upload credentials."""
import base64,json,queue,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
import case_number_ocr as m
import test_release_570 as anchor_tests

class Release5615(unittest.TestCase):
    def setup_records(self,rows):
        helper=anchor_tests.AnchorRollback();helper.setUp();self.addCleanup(helper.tmp.cleanup)
        self.a=helper.a;records=helper.setup(rows)
        for r in records.values():r['score']=99 if r.get('auto_labeled') else 0
        return records
    def test_multiple_low_tail_files_follow_original_strong_anchor(self):
        r=self.setup_records([('08-123','auto'),('08-999','review'),('','review'),('08-888','review')])
        r['i1']['score']=75;r['i2']['score']=0;r['i3']['score']=74.9
        self.assertEqual(self.a._apply_sequence_consensus_impl(),3)
        for iid in ('i1','i2','i3'):
            self.assertEqual(r[iid]['confirmed'],'08-123');self.assertEqual(r[iid]['sequence_anchors'],['i0'])
            self.assertIsNone(self.a._sequence_case_number(r[iid]))
        self.assertEqual(r['i1']['score'],75)
    def test_above_75_and_no_anchor_remain_review(self):
        r=self.setup_records([('','review'),('08-123','auto'),('08-456','review')]);r['i2']['score']=75.01
        self.assertEqual(self.a._apply_sequence_consensus_impl(),0)
    def test_bounding_conflict_blocks_single_sided_guess(self):
        r=self.setup_records([('08-123','auto'),('08-999','review'),('08-456','auto')]);r['i1']['score']=50
        self.assertEqual(self.a._apply_sequence_consensus_impl(),0)
    def test_manual_boundary_cannot_be_skipped(self):
        r=self.setup_records([('08-123','auto'),('08-456','manual'),('08-999','review')]);r['i2']['score']=40
        self.assertEqual(self.a._apply_sequence_consensus_impl(),0);self.assertEqual(r['i1']['confirmed'],'08-456')
    def test_two_sided_support_replaces_single_sided_provenance(self):
        r=self.setup_records([('08-123','auto'),('08-999','review')]);r['i1']['score']=40
        self.a._apply_sequence_consensus_impl()
        r['i2']=dict(r['i0'],path=self.a.folder/'Image0002.pdf',original_name='Image0002.pdf')
        self.a._apply_sequence_consensus_impl();self.assertEqual(r['i1']['sequence_anchors'],['i0','i2'])
    def test_new_conflicting_right_anchor_revokes_carry_forward(self):
        r=self.setup_records([('08-123','auto'),('08-999','review')]);r['i1']['score']=40
        self.a._apply_sequence_consensus_impl()
        r['i2']=dict(r['i0'],path=self.a.folder/'Image0002.pdf',original_name='Image0002.pdf',confirmed='08-456')
        self.a._apply_sequence_consensus_impl();self.assertEqual(r['i1']['confirmed'],'');self.assertEqual(r['i1']['candidate'],'08-999')
    def test_speckled_crop_rejected_before_handwriting_model(self):
        image=np.full((40,160,3),240,dtype=np.uint8)
        for y in range(6,34,3):
            for x in range(8,150,8):image[y,x]=20
        a=m.App.__new__(m.App);a.trocr_model=object();a._perf_stages={};a._diag_event=Mock()
        a._trocr_read_scored=Mock(return_value=('08-477',99));a._automatic_handwritten_4x4=Mock(return_value=[])
        hits=a._scan_one_learned_zone(image,{'rect':(0,0,160,40),'count':10})
        self.assertEqual(hits,[]);a._trocr_read_scored.assert_not_called()
    def test_real_glyph_shapes_pass_existing_ink_gate(self):
        import cv2
        image=np.full((45,180,3),255,dtype=np.uint8)
        cv2.putText(image,'08-123',(8,32),cv2.FONT_HERSHEY_SIMPLEX,.8,(0,0,0),2)
        self.assertTrue(m.App._plausible_case_ink_crop(image,(0,0,180,45))[0])
    def test_blank_settings_save_retains_encrypted_token_and_enabled_default(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'_github_log_settings_path',return_value=Path(d)/'settings.json'):
            settings={'protected_token':'encrypted-original','enabled':True}
            m._save_github_log_settings(settings,True,'')
            loaded=m._read_github_log_settings();self.assertEqual(loaded,settings)
    def test_replacement_clears_expired_state_without_plaintext_persistence(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'_github_log_settings_path',return_value=Path(d)/'settings.json'),patch.object(m,'_protect_github_token',return_value=b'ciphertext'):
            m._save_github_log_settings({'token_rejected':True,'token_expires_at':'2000-01-01'},True,'SECRET_TOKEN')
            loaded=m._read_github_log_settings();self.assertFalse(m._github_token_is_expired(loaded));self.assertNotIn('SECRET_TOKEN',json.dumps(loaded))
    def test_expired_saved_token_prompts_without_network_request(self):
        a=m.App.__new__(m.App);a.queue=queue.Queue()
        with patch.object(m,'_read_github_log_settings',return_value={'enabled':True,'token_expires_at':'2000-01-01T00:00:00Z'}),patch.object(m.urllib.request,'urlopen') as send:
            a._diag_sync_shared(Path('/private/run.jsonl'))
        send.assert_not_called();messages=list(a.queue.queue)
        self.assertTrue(any(x[0]=='diagnostic_token_needed' for x in messages))
    def test_http401_prompts_for_replacement_and_keeps_local_log(self):
        a=m.App.__new__(m.App);a.queue=queue.Queue()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run.jsonl';p.write_text(json.dumps({'event':'run_end','version':'5.6.15'})+'\n')
            with patch.object(m,'_read_github_log_settings',return_value={'enabled':True}),patch.dict(m.os.environ,{'MFCASE_GITHUB_LOG_TOKEN':'fixture'}),patch.object(m,'_save_github_log_settings') as save,patch.object(m.urllib.request,'urlopen',side_effect=m.urllib.error.HTTPError('https://api.github.com',401,'Unauthorized',{},None)):
                a._diag_sync_shared(p)
            self.assertTrue(p.exists());self.assertTrue(save.call_args.args[0]['token_rejected'])
            self.assertTrue(any(x[0]=='diagnostic_token_needed' for x in a.queue.queue))

    def test_saved_token_reused_for_two_scans_without_reentry(self):
        a=m.App.__new__(m.App);a.queue=queue.Queue()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run';p.write_text(json.dumps({'event':'run_end','version':'5.6.15'})+'\n')
            response=Mock();response.status=201;response.headers={}
            response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            settings={'enabled':True,'protected_token':base64.b64encode(b'encrypted').decode()}
            with patch.object(m,'_read_github_log_settings',return_value=settings),patch.dict(m.os.environ,{'MFCASE_GITHUB_LOG_TOKEN':''}),patch.object(m,'_protect_github_token',return_value=b'fixture-secret') as decrypt,patch.object(m.urllib.request,'urlopen',return_value=response) as send:
                a._diag_sync_shared(p);a._diag_sync_shared(p)
            self.assertEqual(send.call_count,2);self.assertEqual(decrypt.call_count,2)
            self.assertFalse(any(x[0]=='diagnostic_token_needed' for x in a.queue.queue))
    def test_permission_failure_does_not_discard_valid_token(self):
        a=m.App.__new__(m.App);a.queue=queue.Queue()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run';p.write_text(json.dumps({'event':'run_end','version':'5.6.15'})+'\n')
            with patch.object(m,'_read_github_log_settings',return_value={'enabled':True}),patch.dict(m.os.environ,{'MFCASE_GITHUB_LOG_TOKEN':'fixture'}),patch.object(m,'_save_github_log_settings') as save,patch.object(m.urllib.request,'urlopen',side_effect=m.urllib.error.HTTPError('https://api.github.com',403,'Forbidden',{},None)):
                a._diag_sync_shared(p)
            save.assert_not_called();self.assertFalse(any(x[0]=='diagnostic_token_needed' for x in a.queue.queue))

if __name__=='__main__':unittest.main()
