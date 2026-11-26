"""v6.0.1 controlled update: boundary, order, OCR, recovery, 5k and privacy."""
import json,os,queue,tempfile,time,unittest,logging
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
import cv2
import case_number_ocr as m
import test_release_570 as h

class Case601(unittest.TestCase):
    def setup(self,items):
        fixture=h.AnchorRollback();fixture.setUp();self.addCleanup(fixture.tmp.cleanup)
        rows=fixture.setup(items);app=fixture.a
        app._order_review_ids=[];app._order_review_pending=False
        app._order_popup=None;app._order_popup_job=None
        app._save_state=Mock(return_value=True)
        app._schedule_order_popup=Mock()
        app.status=Mock();app.root=Mock()
        return app,rows

    def test_locked_position_blocks_distant_two_side_anchor(self):
        app,rows=self.setup([('08-100','manual'),('','review'),('08-500','manual'),
                             ('','review'),('08-100','manual')])
        rows['i2']['sequence_locked']=True
        app._apply_sequence_consensus_impl()
        self.assertEqual(rows['i1']['confirmed'],'')
        self.assertEqual(rows['i3']['confirmed'],'')
        self.assertEqual(rows['i2']['confirmed'],'08-500')
        self.assertEqual([iid for iid,_ in app._sequence_order_with_sent_history()],list(rows))

    def test_two_separate_regions_infer_without_crossing_lock(self):
        app,rows=self.setup([('08-100','manual'),('','review'),('08-100','auto'),
                            ('08-999','manual'),('08-200','manual'),('', 'review'),('08-200','auto')])
        rows['i3']['sequence_locked']=True
        app._apply_sequence_consensus_impl()
        self.assertEqual(rows['i1']['confirmed'],'08-100')
        self.assertEqual(rows['i5']['confirmed'],'08-200')
        self.assertEqual(rows['i3']['confirmed'],'08-999')
        self.assertEqual(rows['i1']['sequence_anchors'],['i0','i2'])
        self.assertEqual(rows['i5']['sequence_anchors'],['i4','i6'])

    def test_previous_only_prefills_but_never_completes(self):
        app,rows=self.setup([('08-100','manual'),('','review')])
        rows['i1']['score']=61
        rows['i0']['score']=99
        app._apply_sequence_consensus_impl()
        self.assertEqual(rows['i1']['candidate'],'08-100')
        self.assertFalse(rows['i1']['manual_approved'])
        self.assertEqual(rows['i1']['confirmed'],'')

    def test_existing_two_sided_inference_original_ocr_is_not_destroyed(self):
        app,rows=self.setup([('08-594','manual'),('08-570','auto'),('08-594','manual')])
        rows['i1']['scan_raw_candidate']='08-570'
        app._apply_sequence_consensus_impl()
        self.assertEqual(rows['i1']['confirmed'],'08-594')
        self.assertEqual(rows['i1']['scan_raw_candidate'],'08-570')
        self.assertEqual(app._order_review_findings(),[])

    def test_approval_warning_no_leaves_draft_unapproved(self):
        app,rows=self.setup([('08-594','manual'),('08-570','review'),('08-595','manual')])
        rows['i1'].update(case_draft='08-570',confirmed='',manual_approved=False)
        app._save_completed_case_edit=m.App._save_completed_case_edit.__get__(app)
        with patch.object(m.messagebox,'askyesno',return_value=False) as p:
            self.assertFalse(app._confirm_out_of_order_change('i1','08-570'))
        self.assertTrue(p.called)
        self.assertFalse(rows['i1'].get('sequence_locked',False))
        self.assertEqual(rows['i1']['case_draft'],'08-570')

    def test_approval_warning_yes_sets_persistent_red_lock(self):
        app,rows=self.setup([('08-594','manual'),('08-570','review'),('08-595','manual')])
        with patch.object(m.messagebox,'askyesno',return_value=True):
            self.assertTrue(app._confirm_out_of_order_change('i1','08-570'))
        self.assertTrue(rows['i1']['sequence_locked'])
        self.assertEqual(rows['i1']['order_review_ack'],'08-570')
        app._apply_sequence_consensus_impl()
        self.assertFalse(rows['i1'].get('sequence_inferred'))

    def test_normal_order_and_repeated_values_no_warning(self):
        app,_=self.setup([('08-594','manual'),('08-594','review'),('08-595','manual')])
        self.assertEqual(app._manual_order_problem('i1','08-594'),'')
        self.assertEqual(app._manual_order_problem('i1','08-595'),'')

    def test_out_of_order_and_forward_jump_detected(self):
        app,r=self.setup([('08-590','auto'),('08-591','auto'),('08-592','auto'),
                           ('08-593','auto'),('08-995','auto'),('08-595','auto')])
        for row in r.values():row['score']=98
        findings=app._order_review_findings()
        self.assertIn('i4',findings)
        self.assertNotIn('i5',findings)  # The valid neighbor is not blamed
        # Persistent red boundary prevents any issue from crossing that row.
        r['i4']['sequence_locked']=True
        self.assertNotIn('i5',app._order_review_findings())

    def test_candidate_trailing_flag_no_longer_vetoes_valid_ocr(self):
        hit={'number':'08-595','box_verified':True,'box_rect':(200,20,290,56),
             'score':98.6,'trailing_digit_uncertain':True}
        self.assertEqual(m.App._clear_localized_case([hit],97),hit)
        other=dict(hit,number='08-590')
        self.assertIsNone(m.App._clear_localized_case([hit,other],97))

    def test_sequence_training_labels_not_mistaken_for_visual_verification(self):
        approved=dict(confirmed_case_number='08-594',ocr_candidate='08-570',ocr_score=96,
                      number_only_verified=True,crop_origin='manual-field',
                      visual_handwriting_verified=True,sequence_assigned=False)
        self.assertIsNone(m.suspect_training_label(approved))
        assigned=dict(approved,sequence_assigned=True)
        self.assertEqual(m.suspect_training_label(assigned),
                         'ocr_label_conflict_needs_visual_verification')

    def test_real_image_label_is_audited_not_deleted(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);f=folder/'ink.png'
            im=np.full((75,260,3),255,dtype=np.uint8)
            cv2.putText(im,'08-594',(12,55),cv2.FONT_HERSHEY_SIMPLEX,1.2,(0,0,0),3)
            Image.fromarray(im).save(f)
            sample=dict(image='ink.png',confirmed_case_number='08-594',
                        ocr_candidate='08-570',ocr_score=96,number_only_verified=True,
                        crop_origin='manual-field',visual_handwriting_verified=True,
                        sequence_assigned=False)
            safe,bad=m.audit_examples([sample],folder)
            self.assertEqual(len(safe),1,bad)
            self.assertEqual(bad,[])
            sample['visual_handwriting_verified']=False
            safe,bad=m.audit_examples([sample],folder)
            self.assertEqual(len(safe),0)
            self.assertEqual(len(bad),1)
            self.assertTrue(f.exists())

    def test_full_list_defaults_to_true(self):
        app=m.App.__new__(m.App)
        app.auto_send_var=Mock();app.show_all_files_var=Mock();app.auto_threshold_var=Mock()
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{'LOCALAPPDATA':t}):
            app._load_user_settings()
            app.show_all_files_var.set.assert_called_with(True)

    def test_5000_journal_recovery_and_single_record_write(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);app=m.App.__new__(m.App)
            app.folder=folder;app.records=m._MFTrackedRows(app)
            app._state_dirty=set();app._state_journal_changes=0
            app._state_last_checkpoint=time.monotonic();app._sequence_review_pending=False
            app._perf_stages={}
            for i in range(5000):
                name=f'Image{i:05d}.pdf'
                app.records[f'pdf::{name}']=dict(path=folder/name,original_name=name,
                      candidate='',confirmed='',status='Not scanned',sequence_locked=False)
            self.assertTrue(app._state_full_checkpoint())
            base=folder/'.case_ocr_state.json';size=base.stat().st_size
            self.assertGreater(size,1000000)
            iid='pdf::Image04201.pdf'
            app.records[iid].update(candidate='08-594',confirmed='08-594',
                                   status='Manual verified',sequence_locked=True)
            begin=time.perf_counter()
            self.assertTrue(app._save_state_impl())
            elapsed=time.perf_counter()-begin
            journal=folder/'.case_ocr_state.journal.jsonl'
            self.assertLess(journal.stat().st_size,5000)
            self.assertEqual(len(journal.read_text().splitlines()),1)
            self.assertLess(elapsed,1.0,elapsed)
            self.assertEqual(json.loads(base.read_text())['files']['Image04201.pdf']['confirmed'],'')
            # Simulate exact loader reconciliation, without producing 5,000 PDFs.
            restored=json.loads(base.read_text())['files']
            for line in journal.read_text().splitlines():
                restored.update(json.loads(line)['files'])
            self.assertEqual(restored['Image04201.pdf']['confirmed'],'08-594')
            self.assertTrue(restored['Image04201.pdf']['sequence_locked'])
            # Compaction leaves valid legacy snapshot and an empty journal.
            self.assertTrue(app._state_full_checkpoint())
            self.assertEqual(journal.stat().st_size,0)
            saved=json.loads(base.read_text())['files']
            self.assertEqual(len(saved),5000)
            self.assertTrue(saved['Image04201.pdf']['sequence_locked'])

    def test_snapshot_plus_recovery_journal_from_folder_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            name='Image0007.pdf'
            (root/name).write_bytes(b'%PDF-1.4\n% EOF')
            state={'version':2,'files':{name:{'candidate':'08-570','confirmed':'',
                  'status':'REVIEW','sequence_locked':False}}}
            (root/'.case_ocr_state.json').write_text(json.dumps(state))
            final=dict(state['files'][name],candidate='08-594',confirmed='08-594',
                       status='Manual verified',sequence_locked=True)
            journal=root/'.case_ocr_state.journal.jsonl'
            journal.write_text(json.dumps({'version':3,'files':{name:final}})+'\n'+
                               '{"version":3,"files":{',encoding='utf8')
            app=m.App.__new__(m.App);app.queue=queue.Queue()
            app._natural_pdf_sort_key=Mock(return_value=(0,))
            app._folder_load_worker(root,7)
            event=app.queue.get_nowait()
            self.assertEqual(event[0],'folder_load_result')
            self.assertEqual(event[1],7)
            recovered=event[3][name]
            self.assertEqual(recovered['confirmed'],'08-594')
            self.assertTrue(recovered['sequence_locked'])
            self.assertEqual(len(event[4]),1)
            logging.shutdown()  # Release Windows FileHandler before temporary-folder cleanup

    def test_locked_row_never_enters_rescan_candidates(self):
        app,rows=self.setup([('08-594','manual'),('', 'review')])
        app.folder=rows['i0']['path'].parent
        for row in rows.values():row['path'].write_bytes(b'%PDF-1.4')
        rows['i0']['sequence_locked']=True
        app._rescan_all_candidates=m.App._rescan_all_candidates.__get__(app)
        selected=app._rescan_all_candidates()
        self.assertEqual(selected,['i1'])
        app._pending_scan_items=m.App._pending_scan_items.__get__(app)
        self.assertNotIn('i0',[iid for iid,_ in app._pending_scan_items(only_iids={'i0','i1'})])

    def test_5000_planner_leaves_locked_file_and_blocks_crossing(self):
        app,rows=self.setup([('08-300','manual')]+[('', 'review')]*2498+
                            [('08-999','manual')]+[('', 'review')]*2499+
                            [('08-300','manual')])
        rows['i2499']['sequence_locked']=True
        begin=time.perf_counter()
        plan,issues=app._sequence_consensus_plan(list(rows.items()))
        self.assertLess(time.perf_counter()-begin,5)
        self.assertFalse(plan)
        self.assertEqual(len(rows),5000)
        self.assertFalse(issues)

if __name__=='__main__':unittest.main()
