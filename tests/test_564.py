import sys,unittest,tempfile,json,os
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import case_number_ocr as m
class Var:
 def __init__(self,v):self.v=v
 def get(self):return self.v
 def set(self,v):self.v=v
class FullList(unittest.TestCase):
 def app(self):
  a=m.App.__new__(m.App);a.auto_send_var=Var(False);a.show_all_files_var=Var(True);a._audit_event=Mock();return a
 def test_new_and_legacy_settings_default_full_list_on(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}):
   a=self.app();a._load_user_settings();self.assertTrue(a.show_all_files_var.get());self.assertFalse(a.auto_send_var.get())
   a._user_settings_file.parent.mkdir(parents=True);a._user_settings_file.write_text('{"auto_send_completed":false}')
   a._load_user_settings();self.assertTrue(a.show_all_files_var.get())
 def test_uncheck_saved_across_restart(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}):
   a=self.app();a._load_user_settings();a.show_all_files_var.set(False);a._save_user_settings()
   b=self.app();b._load_user_settings();self.assertFalse(b.show_all_files_var.get());self.assertFalse(b.auto_send_var.get())
 def test_toggle_rebuilds_only_view_and_preserves_records(self):
  a=self.app();a.records={'a':{'status':'Not scanned'},'b':{'status':'REVIEW'},'c':{'confirmed':'08-341','manual_approved':True}}
  self.assertTrue(all(a._show_in_scan_list(r) for r in a.records.values()))
  a.show_all_files_var.set(False);a._save_user_settings=Mock();a._rebuild_scan_list=Mock();a._toggle_full_list()
  self.assertEqual([i for i,r in a.records.items() if a._show_in_scan_list(r)],['b'])
  self.assertEqual(len(a.records),3);a._save_user_settings.assert_called_once();a._rebuild_scan_list.assert_called_once()
if __name__=='__main__':unittest.main()
