"""Printed evidence selection, shared detection, and public-log privacy."""
import base64,gzip,json,queue,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
import case_number_ocr as m

def hit(number,score,rect,engine='EasyOCR',label='incident'):
    return dict(number=number,score=score,rect=rect,box_rect=rect,box_verified=True,
                engines=[engine],engine_scores={engine:score},label_type=label)

class Release5614(unittest.TestCase):
    def app(self):
        a=m.App.__new__(m.App);a._perf_stages={};a._diag_event=Mock();a.queue=queue.Queue()
        return a
    def test_high_printed_not_replaced_by_taller_weak_crop(self):
        for strong,weak in [(96.45,81.688),(99.997,91.225)]:
            hits=[hit('08-123',strong,(600,52,730,101)),
                  hit('08-123',weak,(583,32,939,127),'TrOCR')]
            best=m._select_primary_case_hit(hits,'incident',(1000,1000,3))
            self.assertEqual(best['score'],strong)
            self.assertEqual(self.app()._resolve_ocr_alternates(hits,'incident',best),[hits[0]])
    def test_same_ink_unlabeled_stronger_read_keeps_label_context(self):
        hits=[hit('08-123',82,(600,32,900,127),'TrOCR'),
              hit('08-123',99,(620,52,750,101),label=None)]
        self.assertEqual(m._select_primary_case_hit(hits,'incident',(1000,1000,3))['score'],99)
    def test_unrelated_lower_field_does_not_win_on_confidence(self):
        hits=[hit('08-123',98,(600,52,730,101)),hit('08-999',100,(600,150,730,200),'TrOCR')]
        self.assertEqual(m._select_primary_case_hit(hits,'incident',(1000,1000,3))['number'],'08-123')
    def test_large_crop_does_not_merge_neighboring_number_line(self):
        self.assertFalse(m._same_physical_case_box((500,10,850,110),(600,80,730,110)))

    def test_shared_printed_call_covers_outside_crop_and_quarter(self):
        a=self.app();image=np.zeros((1000,800,3),dtype=np.uint8);quarter=image[:500,400:]
        a._page_ocr_budget={'thread':threading.get_ident(),'printed':0,'handwriting':0,'cache':{}}
        a._prepare_printed_region(image,[{'rect':(50,600,200,650)}],(quarter,400,0))
        canvas,x,y=a._page_ocr_budget['printed_region']
        self.assertTrue(np.all(canvas[520,20]==255)) # unapproved pixels masked
        self.assertTrue(np.all(canvas[620,20]==0)) # learned region retained
        a.reader=Mock();a.reader.readtext.return_value=[
          ([[10,610],[100,610],[100,640],[10,640]],'08-123',.99),
          ([[500,20],[600,20],[600,60],[500,60]],'08-456',.98)]
        first=a._automatic_detections(image[600:650,50:200],50,600)
        second=a._automatic_detections(quarter,400,0)
        self.assertEqual(first[0][1],'08-123');self.assertEqual(second[0][1],'08-456')
        a.reader.readtext.assert_called_once()
    def test_handwriting_cannot_preempt_stronger_printed_crop(self):
        a=self.app();a.trocr_model=object();a._probe_trocr_default_bias=Mock();a._trocr_bias_values=set()
        a._trocr_read_scored=Mock(return_value=('08-999',96))
        a._automatic_handwritten_4x4=Mock(return_value=[hit('08-123',99,(500,20,650,70))])
        image=np.random.default_rng(9).integers(0,255,(1000,800,3),dtype=np.uint8)
        hits=a._scan_one_learned_zone(image,{'rect':(500,20,650,70),'count':5})
        self.assertEqual([h['number'] for h in hits],['08-123'])
        a._automatic_handwritten_4x4.assert_called_once()
    def private_event(self):
        return dict(event='ocr_candidates',version='5.6.14',file='SECRET_PERSON.pdf',
            scan_folder=r'\\PRIVATE_SERVER\CASE_FOLDER',token='SECRET_CREDENTIAL',text='PRIVATE NARRATIVE',
            all_candidates=[dict(number='08-7654321',score=99,rect=[1,2,3,4],engines=['EasyOCR','PRIVATE'],
                                 engine_scores={'EasyOCR':99,'PRIVATE':100},label_type='incident')],
            stages={'PRIVATE_SERVER':{'ms':9}},error='SECRET_ERROR',unknown={'score':'PRIVATE'})
    def test_privacy_allowlist_and_identity_comparison(self):
        aliases={};one=m._public_scan_event(self.private_event(),aliases)
        other=dict(self.private_event(),all_candidates=[{'number':'08-7654321','score':90}])
        two=m._public_scan_event(other,aliases)
        encoded=json.dumps(one)
        for secret in ('SECRET','PRIVATE','7654321','CASE_FOLDER'):self.assertNotIn(secret,encoded)
        self.assertEqual(one['all_candidates'][0]['number']['identity'],two['all_candidates'][0]['number']['identity'])
        self.assertEqual(one['file_id'],two['file_id'])
        self.assertIsNone(m._public_scan_event({'event':'PRIVATE'},aliases))
    def test_upload_payload_has_no_raw_document_values(self):
        a=self.app()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'private.jsonl';path.write_text(json.dumps(self.private_event())+'\n')
            response=Mock();response.status=201;response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            with patch.object(m,'_read_github_log_settings',return_value={'enabled':True}),patch.dict(m.os.environ,{'MFCASE_GITHUB_LOG_TOKEN':'token-fixture'}),patch.object(m.urllib.request,'urlopen',return_value=response) as send:
                a._diag_sync_shared(path)
            request=send.call_args.args[0];payload=json.loads(request.data)
            log=gzip.decompress(base64.b64decode(payload['content'])).decode()
            self.assertEqual(request.method,'PUT');self.assertTrue(request.full_url.startswith('https://api.github.com/repos/chrisbrook0127/CaseNumberOCR-Updates/contents/scan-diagnostics/'))
            for secret in ('SECRET','PRIVATE','7654321','token-fixture'):self.assertNotIn(secret,log)
            self.assertIn('uploaded to GitHub',a.queue.get()[1])
    def test_legacy_review_and_learning_hooks_do_not_upload(self):
        a=self.app();a._diag_file=Path('/private/log')
        with patch.object(m,'_read_github_log_settings') as settings,patch.object(m.urllib.request,'urlopen') as send:
            a._diag_sync_shared()
        settings.assert_not_called();send.assert_not_called()

    def test_missing_token_or_disabled_never_sends(self):
        for settings in ({'enabled':True},{'enabled':False}):
            a=self.app()
            with patch.object(m,'_read_github_log_settings',return_value=settings),patch.dict(m.os.environ,{'MFCASE_GITHUB_LOG_TOKEN':''}),patch.object(m.urllib.request,'urlopen') as send:
                a._diag_sync_shared(Path('/not-opened/private.jsonl'))
            send.assert_not_called()
    def test_failure_retains_log_and_does_not_disclose_exception(self):
        a=self.app()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log';p.write_text(json.dumps(self.private_event())+'\n');original=p.read_bytes()
            with patch.object(m,'_read_github_log_settings',return_value={'enabled':True}),patch.dict(m.os.environ,{'MFCASE_GITHUB_LOG_TOKEN':'token-fixture'}),patch.object(m.urllib.request,'urlopen',side_effect=RuntimeError('SECRET token-fixture')):
                a._diag_sync_shared(p)
            self.assertEqual(p.read_bytes(),original);self.assertNotIn('SECRET',a.queue.get()[1])

if __name__=='__main__':unittest.main()
