import json, queue, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
import test_rules
m=test_rules.m

class SinglePassAndApproval(unittest.TestCase):
    app=test_rules.Rules.app

    def test_overlapping_duplicate_digits_not_concatenated(self):
        def box(x,w,text):return ([[x,0],[x+w,0],[x+w,12],[x,12]],text,.99)
        hits=m.App._incident_hits_from_detections(
            [box(110,24,'08-'),box(138,24,'84'),box(139,24,'84'),box(138,24,'84')],
            (0,0,100,12),(1,0),(1,0))
        self.assertEqual([h['number'] for h in hits],['08-84'])
        hits=m.App._incident_hits_from_detections([box(110,90,'08-848484')],(0,0,100,12),(1,0),(1,0))
        self.assertEqual([h['number'] for h in hits],['08-848484'])

    def scan(self,found):
        a=self.app();a._find_case_field_label_regions=Mock(return_value=[(20,20,100,40,'incident')] if found else [])
        a._read_incident_to_right=Mock(return_value=[{'number':'08-84','score':96,'rect':(700,20,800,40),'engines':['EasyOCR'],'engine_scores':{'EasyOCR':96}}])
        a._find_case_candidates=Mock(return_value=[])
        pix=SimpleNamespace(height=900,width=1100,n=3,samples=np.full((900,1100,3),255,dtype=np.uint8).tobytes())
        page=Mock();page.get_pixmap.return_value=pix
        class Doc(list):
            def close(self):pass
        pdf=Mock();pdf.open.return_value=Doc([page])
        with patch.object(m,'pymupdf',pdf):result=a._ocr_pdf(Path('synthetic.pdf'))
        pdf.open.assert_called_once()
        self.assertEqual(a._find_case_field_label_regions.call_args_list[0].args[0].shape,(634,634,3))
        return result

    def test_success_uses_single_4x4_region(self):
        self.assertEqual(self.scan(True)[0],'08-84')

    def test_no_read_does_not_expand_or_repeat_pdf_scan(self):
        result=self.scan(False)
        self.assertEqual(result[0],'')
        self.assertIn('4x4',result[2])

    def test_edit_is_draft_until_green_check(self):
        a=self.app();a.status=Mock();a._export_in_flight_iids=set()
        a.records={'x':{'path':Path('/synthetic/1.pdf'),'candidate':'08-44','confirmed':'08-44','manual_approved':True,'auto_labeled':True}}
        a._save_state.return_value=True
        a._save_learning_example=Mock()
        self.assertTrue(a._save_scan_draft('x',SimpleNamespace(get=lambda:'08-84')))
        r=a.records['x'];self.assertFalse(r['manual_approved']);self.assertEqual(r['confirmed'],'')
        self.assertIsNone(a._sequence_case_number(r));a._save_learning_example.assert_not_called()
        a._completed_thumbnail_path=Mock(return_value=None);a.completed_edit_drafts={}
        a._close_completed_edit=Mock();a._insert_completed_row=Mock();a.completed_send_status=Mock();a._ensure_preview=Mock()
        a._quick_approve('x')
        self.assertEqual(r['confirmed'],'08-84');self.assertTrue(r['manual_approved'])
        self.assertNotIn('case_draft',r);a._save_learning_example.assert_called_once_with(r,'08-84')

    def test_low_confidence_guess_can_be_quick_approved(self):
        a=self.app();a.records={'x':{'candidate':'08-84','score':12}}
        a._save_completed_case_edit=Mock(return_value=True)
        a._quick_approve('x')
        self.assertEqual(a._save_completed_case_edit.call_args.args[1].get(),'08-84')

    def test_invalid_or_busy_quick_approval_blocked(self):
        a=self.app();a.status=Mock();a.records={'x':{'candidate':'09-84'}}
        a._start_scan_case_edit=Mock();a._save_completed_case_edit=Mock()
        a._quick_approve('x');a._save_completed_case_edit.assert_not_called()
        a.records['x']['candidate']='08-84';a.busy=True;a._quick_approve('x')
        a._save_completed_case_edit.assert_not_called()

    def test_send_already_renamed_preserves_original_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app();a.queue=queue.Queue();p=Path(d)/'Image1_08-84.pdf';p.write_bytes(b'original')
            a.records={'x':{'original_name':'Image1.pdf'}}
            dest=Path(d)/'COMPLETED'
            a._export_worker(dest,[('x',p,'08-84')])
            event=a.queue.get_nowait();self.assertEqual(event[3],str(dest/p.name));self.assertEqual(event[4],'')
            self.assertFalse(p.exists());self.assertEqual((dest/p.name).read_bytes(),b'original')

    def test_rename_failure_retains_source_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app();a.queue=queue.Queue();source=Path(d)/'Image1.pdf';source.write_bytes(b'original')
            dest=Path(d)/'COMPLETED';dest.mkdir()
            target=dest/'Image1_08-84.pdf';target.write_bytes(b'prior')
            with patch.object(m.os,'replace',side_effect=OSError('locked')):
                a._export_worker(dest,[('x',source,'08-84')])
            self.assertEqual(source.read_bytes(),b'original');self.assertEqual(target.read_bytes(),b'prior')
            self.assertFalse((dest/'Image1_08-84 (1).pdf').exists())
            self.assertTrue(a.queue.get_nowait()[4])

    def test_renamed_state_saved_by_current_filename_and_restored(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app();a.folder=Path(d);p=a.folder/'08-84.pdf';p.write_bytes(b'pdf')
            a.records={'x':{'path':p,'original_name':'Image1.pdf','confirmed':'08-84','candidate':'08-84','manual_approved':True,'exported':True}}
            self.assertTrue(m.App._save_state(a))
            saved=json.loads((a.folder/'.case_ocr_state.json').read_text())['files']
            self.assertIn('08-84.pdf',saved);self.assertNotIn('Image1.pdf',saved)
            a.year_var=Mock();a.tree=Mock();a.tree.get_children.return_value=[]
            a.completed_tree.get_children.return_value=[];a.completed_edit_widgets={};a.completed_edit_drafts={}
            a.completed_checked_iids=set();a.scan_checked_iids=set();a.completed_photo_refs={};a.status=Mock()
            for name in ('_refresh_scan_button','_refresh_learning_button_state','_update_scan_file_count','_schedule_auto_export'):
                setattr(a,name,Mock())
            a._insert_completed_row=Mock();a._ensure_preview=Mock()
            a._apply_folder_load('08',saved,[p],False)
            r=next(iter(a.records.values()));self.assertFalse(r['exported']);self.assertEqual(r['original_name'],'Image1.pdf')
            self.assertTrue(a._eligible_for_kurt('x',r))

    def test_before_after_uses_original_order_after_rename(self):
        a=self.app();a.folder=Path('/synthetic');a.records={}
        for iid,name,original,case in [('a','08-84.pdf','Image1.pdf','08-84'),('b','Image2.pdf','Image2.pdf','08-44'),('c','Image3.pdf','Image3.pdf','08-44'),('d','08-84 (1).pdf','Image4.pdf','08-84')]:
            a.records[iid]={'path':a.folder/name,'original_name':original,'candidate':case,'confirmed':case,'manual_approved':True,'auto_labeled':False,'exported':iid in ('a','d')}
        a._apply_sequence_consensus()
        for iid in ('b','c'):self.assertEqual(a.records[iid]['confirmed'],'08-84')

    def test_green_button_is_positioned_beside_case_column(self):
        a=self.app();a.tree=Mock();a.tree.winfo_height.return_value=100
        a.tree.identify_row.return_value='x';a.tree.bbox.side_effect=lambda iid,col: (200,30,230,74) if col=='preview' else (565,30,60,74)
        a._scan_preview_widgets={};a._scan_approve_widgets={};a._scan_preview_photos={};a._scan_preview_keys={}
        a._make_scan_preview=Mock(return_value='preview');a.records={'x':{'candidate':'08-84'}}
        a._quick_approve=Mock();button=Mock();label=Mock()
        with patch.object(m.tk,'Button',return_value=button) as create, patch.object(m.tk,'Label',return_value=label):
            a._refresh_scan_previews()
        self.assertEqual(create.call_args.kwargs['text'],'✓')
        self.assertEqual(create.call_args.kwargs['bg'],'#188038')
        button.place.assert_called_once_with(x=578,y=51,width=34,height=32)
        create.call_args.kwargs['command']();a._quick_approve.assert_called_once_with('x')
        a.tree.identify_row.return_value='';a._refresh_scan_previews()
        button.place_forget.assert_called()
