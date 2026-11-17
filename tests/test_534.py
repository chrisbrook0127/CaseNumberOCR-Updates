import unittest,tempfile,threading,copy
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
import numpy as np
import test_rules
m=test_rules.m

class Changes(unittest.TestCase):
    app=test_rules.Rules.app
    def test_displayed_count_counts_rows_not_selection(self):
        a=self.app();a.tree=Mock();a.tree.get_children.return_value=('a','b','c')
        a.scan_file_count_label=Mock();a.scan_displayed_count_label=Mock()
        a.scan_checked_iids={'b'};a._refresh_scan_checkbox_heading=Mock()
        a._update_scan_file_count()
        a.scan_displayed_count_label.config.assert_called_with(text='3 files displayed')
        a.tree.get_children.return_value=();a._update_scan_file_count()
        a.scan_displayed_count_label.config.assert_called_with(text='0 files displayed')
    def test_all_case_lengths_and_thousands_preserve_digits(self):
        for number in ['1','11','101','1000','1001','1011','1111','01114','11011','101101','1111111','9999999']:
            with self.subTest(number=number):
                value='08-'+number
                self.assertIsNotNone(m.CASE_RE.fullmatch(value))
                self.assertEqual(m._mfcase_export_case_name(value),value)
                a=self.app();a.reader=Mock()
                a.reader.recognize.return_value=[(None,value,1.0)]
                hits=a._recognize_case_strip(np.zeros((30,300),dtype=np.uint8))
                self.assertEqual(hits[0]['number'],value)
    def test_new_handwritten_examples_do_not_reclassify_existing(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.app();a.folder=Path(d);a.learning_dir=a.folder/'learning';a.learning_dir.mkdir()
            a.data_lock=threading.RLock();a.reader=None
            old=[];before={}
            for category in ('handwritten','printed','unsure'):
                path=a.learning_dir/'examples_v3'/category/'old.png';path.parent.mkdir(parents=True);path.write_bytes(category.encode())
                before[path]=path.read_bytes()
                old.append({'training_type':category,'image':str(path.relative_to(a.learning_dir)),'confirmed_case_number':'08-101'})
            checkpoint=a.learning_dir/'trocr_v2_finetuned'/'config.json';checkpoint.parent.mkdir();checkpoint.write_text('existing checkpoint');before[checkpoint]=checkpoint.read_bytes()
            a.learning_data={'examples':copy.deepcopy(old)}
            a._persist_learning_data=Mock();a._refresh_learning_stats=Mock();a._refresh_learning_button_state=Mock();a._diag_sync_shared=Mock()
            r={'path':a.folder/'sample.pdf','training_type':'printed','candidate':'08-101',
               'snippet':(0,np.zeros((300,500,3),dtype=np.uint8),'',(40,20,200,60),[])}
            a._save_learning_example(r,'08-1001')
            self.assertEqual(a.learning_data['examples'][:3],old)
            new=a.learning_data['examples'][-1]
            self.assertEqual(new['training_type'],'handwritten')
            self.assertTrue(new['image'].startswith('examples_v3/handwritten/'))
            self.assertTrue((a.learning_dir/new['image']).exists())
            for path,data in before.items():self.assertEqual(path.read_bytes(),data)
            self.assertEqual(r['training_type'],'printed')
    def editor(self):
        a=self.app();a.status=Mock();a.records={'x':{'path':Path('/sample.pdf'),'candidate':'08-19'}}
        a.tree=Mock();a.tree.bbox.return_value=(10,10,100,30)
        a._save_completed_case_edit=Mock(return_value=True)
        class Entry:
            def __init__(self,*args,**kwargs):self.value='';self.bindings={}
            def insert(self,index,value):self.value=value
            def get(self):return self.value
            def place(self,**kwargs):pass
            def bind(self,key,callback):self.bindings[key]=callback
            def focus_set(self):pass
            def selection_range(self,*args):pass
            def destroy(self):pass
        with patch.object(m.ttk,'Entry',Entry):a._start_scan_case_edit('x')
        return a,a._scan_case_editor
    def test_changed_valid_inline_value_saves_on_focus_out(self):
        a,e=self.editor();e.value='08-1001';e.bindings['<FocusOut>'](SimpleNamespace(type='10'))
        a._save_completed_case_edit.assert_called_once_with('x',e)
        self.assertIsNone(a._scan_case_editor)
    def test_opening_cell_does_not_approve_unchanged_read(self):
        a,e=self.editor();e.bindings['<FocusOut>'](SimpleNamespace(type='10'))
        a._save_completed_case_edit.assert_not_called()
    def test_invalid_inline_value_never_completes_file(self):
        a,e=self.editor();e.value='08-100I';e.bindings['<FocusOut>'](SimpleNamespace(type='10'))
        a._save_completed_case_edit.assert_not_called();self.assertIs(a._scan_case_editor,e)
    def test_escape_cancels_then_focus_out_does_not_save(self):
        a,e=self.editor();e.value='08-1001';e.bindings['<Escape>'](None)
        e.bindings['<FocusOut>'](SimpleNamespace(type='10'))
        a._save_completed_case_edit.assert_not_called()
    def test_offline_button_is_not_constructed(self):
        import inspect
        self.assertNotIn('prepare_offline_btn',inspect.getsource(m.App._ui))

if __name__=='__main__':unittest.main()
