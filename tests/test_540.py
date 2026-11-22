import tempfile,unittest,queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
import test_rules,test_export_completed,test_534
m=test_rules.m

class Workflow540(unittest.TestCase):
    app=test_rules.Rules.app
    def test_approval_renames_then_send_moves_and_retains_content(self):
        with tempfile.TemporaryDirectory() as d:
            a=test_export_completed.CompletedExport().export_app(Path(d))
            a._apply_sequence_consensus()
            one=Path(d)/'approved1_08-1114.pdf'
            self.assertEqual(one.read_bytes(),b'approved1')
            self.assertEqual(a.records['approved1']['original_name'],'approved1.pdf')
            self.assertFalse((Path(d)/'COMPLETED').exists())
            self.assertEqual(a.records['approved1']['confirmed'],'08-01114')
            a.records['approved1']['confirmed']='08-1010'
            a._apply_sequence_consensus()
            renamed=Path(d)/'approved1_08-1010.pdf'
            self.assertFalse(one.exists());self.assertEqual(renamed.read_bytes(),b'approved1')
            a._apply_sequence_consensus()
            self.assertEqual(len(list(Path(d).glob('approved1*.pdf'))),1)
            a._export_worker(Path(d)/'COMPLETED',[('approved1',renamed,'08-1010')])
            self.assertEqual((Path(d)/'COMPLETED'/renamed.name).read_bytes(),b'approved1')
            self.assertFalse(renamed.exists());self.assertTrue((Path(d)/'review.pdf').exists())

    def test_invalid_prefix_never_renames_even_if_flagged_approved(self):
        with tempfile.TemporaryDirectory() as d:
            a=test_export_completed.CompletedExport().export_app(Path(d))
            for r in a.records.values():r.update(confirmed='114',manual_approved=True)
            a._apply_sequence_consensus()
            self.assertTrue((Path(d)/'approved1.pdf').exists())
            self.assertFalse(any(Path(d).glob('*_*.pdf')))

    def test_auto_rename_collision_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as d:
            a=test_export_completed.CompletedExport().export_app(Path(d))
            target=Path(d)/'approved1_08-1114.pdf';target.write_bytes(b'existing')
            a._apply_sequence_consensus()
            self.assertEqual(target.read_bytes(),b'existing')
            self.assertEqual((Path(d)/'approved1_08-1114 (1).pdf').read_bytes(),b'approved1')

    def test_ctrl_a_includes_completed_and_review(self):
        a=self.app();a._startup_ready=True;a.tree=Mock();a.status=Mock()
        a.tree.get_children.return_value=('approved','review','unscanned')
        a._refresh_rescan_button=Mock();a._rescan_eligible=Mock(return_value=False)
        self.assertEqual(a._select_all_for_rescan(),'break')
        a.tree.selection_set.assert_called_once_with(('approved','review','unscanned'))

    def test_shift_image_click_selects_range(self):
        a=self.app();a.tree=Mock();a.tree.exists.return_value=True
        a.tree.get_children.return_value=('a','b','c','d');a._scan_selection_anchor='a'
        a._select_preview_row('c',SimpleNamespace(state=1))
        a.tree.selection_set.assert_called_once_with(['a','b','c'])

    def test_label_without_number_falls_back_within_same_4x4(self):
        a=self.app();a._find_case_field_label_regions=Mock(return_value=[(10,10,40,30,'incident')])
        a._read_incident_to_right=Mock(return_value=[])
        a._find_case_candidates=Mock(return_value=[])
        pix=SimpleNamespace(height=900,width=1100,n=3,samples=np.full((900,1100,3),255,dtype=np.uint8).tobytes())
        page=Mock();page.get_pixmap.return_value=pix
        class Doc(list):
            def close(self):pass
        pdf=Mock();pdf.open.return_value=Doc([page])
        with patch.object(m,'pymupdf',pdf):result=a._ocr_pdf(Path('synthetic.pdf'))
        self.assertEqual(result[0],'');pdf.open.assert_called_once()
        self.assertEqual(a._find_case_candidates.call_args_list[0].args[0].shape,(634,634,3))

    def test_prefixless_localized_hit_is_rejected(self):
        a=self.app();a._find_case_field_label_regions=Mock(return_value=[(10,10,40,30,'incident')])
        a._read_incident_to_right=Mock(return_value=[{'number':'114','score':100,'rect':(50,10,150,30)}])
        a._find_case_candidates=Mock(return_value=[])
        pix=SimpleNamespace(height=900,width=1100,n=3,samples=np.full((900,1100,3),255,dtype=np.uint8).tobytes())
        page=Mock();page.get_pixmap.return_value=pix
        class Doc(list):
            def close(self):pass
        pdf=Mock();pdf.open.return_value=Doc([page])
        with patch.object(m,'pymupdf',pdf):result=a._ocr_pdf(Path('synthetic.pdf'))
        self.assertEqual(result[0],'')

    def test_manual_edit_saves_and_approves_without_second_button(self):
        with tempfile.TemporaryDirectory() as d:
            a=test_export_completed.CompletedExport().export_app(Path(d))
            a._export_in_flight_iids=set();a.completed_edit_drafts={}
            a._completed_thumbnail_path=Mock(return_value=None)
            a._save_learning_example=Mock();a._ensure_preview=Mock()
            a._close_completed_edit=Mock();a._insert_completed_row=Mock()
            a._save_state=lambda:m.App._save_state(a)
            entry=SimpleNamespace(get=lambda:'08-00101')
            self.assertTrue(a._save_completed_case_edit('review',entry))
            r=a.records['review']
            self.assertEqual(r['confirmed'],'08-00101');self.assertTrue(r['manual_approved'])
            self.assertEqual(r['path'].name,'review_08-101.pdf')
            self.assertEqual(r['path'].read_bytes(),b'review')
            a._save_learning_example.assert_called_once_with(r,'08-00101')
            import json
            state=json.loads((Path(d)/'.case_ocr_state.json').read_text())['files']
            self.assertEqual(state['review_08-101.pdf']['confirmed'],'08-00101')
