import os,sys,tempfile,unittest,types
from pathlib import Path
from unittest import mock
import tkinter as tk
import numpy as np
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
import case_number_ocr as core
from case_number_ocr import App

class Tests(unittest.TestCase):
    def setUp(self):
        os.environ["MFCASE_SKIP_STARTUP_MAINTENANCE"]="1"
        self.root=tk.Tk()
        self.root.geometry("1250x790")
        self.app=App(self.root)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.root.destroy)
        self.app.folder=Path(self.tmp.name)
    def make(self,name):
        import pymupdf
        p=self.app.folder/name
        with pymupdf.open() as doc:
            doc.new_page(width=612,height=792)
            doc.save(p)
        self.app._apply_folder_load("08",{},[p],False)
        return "pdf::"+name
    def test_version_and_actual_roi(self):
        a=self.app
        assert a.app_version=="5.5.2"
        img=np.zeros((900,1972,3),dtype=np.uint8)
        rect=(1508,50,1677,114)
        assert not a._is_corner_case_rect(rect,img)
        assert a._is_in_4x4_rect(rect,img)
        assert not a._is_in_4x4_rect((50,50,200,114),img)
    def test_100pct_single_localized_read_automatically_completed(self):
        a=self.app
        iid=self.make("Image5375.pdf")
        a.scan_items=[(iid,a.records[iid]["path"])]
        bbox=[1508,50,1677,114]
        hit={"number":"08-185","rect":bbox,"box_verified":True,
             "score":99.9996,"engine_scores":{"EasyOCR":99.9996,"TrOCR":99.98},
             "engines":["EasyOCR","TrOCR"]}
        img=np.zeros((900,1972,3),dtype=np.uint8)
        a.queue.put(("row",iid,"08-185",(0,img,"08-185 — fallback",bbox,[hit]),
            "Upper-right fallback in 4x4",False,99.9996,["EasyOCR","TrOCR"],True,1))
        a._poll()
        assert a.records[iid]["confirmed"]=="08-185"
        assert a.records[iid]["auto_labeled"]
        assert not a.records[iid]["exported"]
    def test_incident_box_absolute(self):
        import pymupdf
        a=self.app
        p=a.folder/"box.pdf"
        with pymupdf.open() as d:
            d.new_page(width=896,height=408)
            d.save(p)
        a._find_case_field_label_regions=lambda image:[(270,42,320,76,"incident")]
        a._read_incident_to_right=lambda _image,_rect:[{
            "number":"08-0202","score":99.8,"rect":(300,66,450,116),
            "box_rect":(300,66,450,116),"box_verified":True,
            "engines":["EasyOCR-incident"],"engine_scores":{"EasyOCR":99.8},
            "label_type":"incident"}]
        case,snip,*_=a._ocr_pdf(p)
        assert case=="08-0202"
        expected=snip[1].shape[1]-int(round(4*72*2.2))+300
        assert snip[4][0]["rect"][0]==expected
    def test_single_click_entry(self):
        a=self.app
        iid=self.make("Image5390.pdf")
        a.records[iid]["candidate"]="08-0202"
        a._insert_scan_row(iid,a.records[iid])
        self.root.update_idletasks()
        x,y,w,h=a.tree.bbox(iid,"#4")
        event=types.SimpleNamespace(x=x+w//2,y=y+h//2,state=0)
        with mock.patch.object(a,"_save_completed_case_edit",return_value=True):
            a._on_scan_checkbox_click(event)
            self.root.update()
            assert a._scan_case_editor is not None
            a._scan_case_editor.delete(0,"end")
            a._scan_case_editor.insert(0,"08-0203")
            a._scan_case_commit()
            assert a._scan_case_editor is None
    def test_history_verified_and_inferred_audit(self):
        a=self.app
        iid=self.make("Image0001.pdf")
        r=a.records[iid]
        r.update(confirmed="08-185",candidate="08-185",manual_approved=True,
                 auto_labeled=False,original_name="Image0001.pdf")
        dst=a.folder/"COMPLETED"/"Image0001_08-185.pdf"
        dst.parent.mkdir()
        r["path"].rename(dst)
        a._apply_export_result(iid,"Image0001.pdf",str(dst),"")
        assert a.sent_history["Image0001.pdf"]["destination_name"]==dst.name
        a.sent_history={}
        a._load_sent_history({})
        assert a.sent_history["Image0001.pdf"]["confirmed"]=="08-185"
if __name__=="__main__":
    unittest.main(argv=[sys.argv[0]],verbosity=2)
