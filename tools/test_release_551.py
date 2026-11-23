"""Small release regressions. Full GUI suite also run locally before release."""
import os,sys,tempfile,unittest,tkinter as tk
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
import case_number_ocr as core
from case_number_ocr import App

class Smoke(unittest.TestCase):
    def setUp(self):
        os.environ["MFCASE_SKIP_STARTUP_MAINTENANCE"]="1"
        self.root=tk.Tk()
        self.app=App(self.root)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.root.destroy)
        self.app._log=lambda *args,**kwargs:None

    def background(self):
        a=self.app
        a.trocr_training=True
        a._learning_auto_run=True
        a.learning_stop_event.clear()
        return a

    def test_version_and_single_view(self):
        a=self.app
        self.assertEqual(a.app_version,"5.5.1")
        self.assertIn("V5.5.1",self.root.title())
        self.assertEqual(a.review_tab.winfo_manager(),"place")
        self.assertEqual(a.completed_tab.winfo_manager(),"")
        a._show_main_view("completed")
        self.assertEqual(a._active_main_view,"select")
        self.assertFalse(hasattr(a,"completed_nav_btn"))
        self.assertEqual(len(a.action_bar.grid_slaves()),8)

    def test_spinner_is_nonblocking(self):
        a=self.background()
        a._set_background_learning_indicator(True)
        self.assertEqual(a.learning_activity.winfo_manager(),"pack")
        self.assertFalse(a._training_blocks_work())
        a._set_background_learning_indicator(False)
        self.assertEqual(a.learning_activity.winfo_manager(),"")

    def test_choose_folder_while_auto_training(self):
        a=self.background()
        class NoThread:
            def __init__(self,**kwargs): pass
            def start(self): pass
        with patch.object(core.filedialog,"askdirectory",return_value=self.tmp.name) as dlg, \
             patch.object(core.threading,"Thread",NoThread), \
             patch.object(core.messagebox,"showinfo") as prompt:
            a.choose_folder()
        dlg.assert_called_once()
        prompt.assert_not_called()
        self.assertEqual(a.folder,Path(self.tmp.name))
        self.assertTrue(a.learning_stop_event.is_set())

    def test_yield_keeps_loaded_ocr(self):
        a=self.background()
        model=object()
        a.trocr_model=model
        a._set_background_learning_indicator(True)
        a._yield_background_learning("scan")
        self.assertTrue(a.learning_stop_event.is_set())
        self.assertIs(a.trocr_model,model)

    def test_send_not_blocked_by_auto_learning(self):
        a=self.background()
        a.folder=Path(self.tmp.name)
        a._startup_ready=True
        a._eligible_for_kurt=lambda iid,record,**kwargs:True
        a.records={"f":{"confirmed":"08-9","manual_approved":True,
            "path":Path(self.tmp.name)/"f.pdf","exported":False}}
        a._update_completed_count()
        self.assertEqual(str(a.send_selected_btn["state"]),"normal")

if __name__=="__main__":
    unittest.main(argv=[sys.argv[0]],verbosity=2)
