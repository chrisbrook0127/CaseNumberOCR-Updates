"""Real Tk regression: manual case persists, no automatic sends, refresh spinner closes.

Run with xvfb-run. Uses the exact V5.2.7 release ZIP, not mocks of the
application implementation. Uses synthetic PDF bytes for transfer checks and
an in-memory image solely for the document review UI.
"""
import base64,hashlib,io,json,os,tempfile,time,zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import numpy as np

folder=Path("release-assets")
raw=base64.b64decode(folder.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(raw).hexdigest()==folder.joinpath("SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None and set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    source=z.read("case_number_ocr.py").decode()
    assert 'self.app_version = "5.2.7"' in source
    assert 'text="SAVE REVIEW"' in source

os.environ["MFCASE_SKIP_STARTUP_MAINTENANCE"]="1"
with tempfile.TemporaryDirectory(prefix="MFCase 5.2.7 real GUI ") as tmp:
    root_folder=Path(tmp)
    first=root_folder/"Image4915.pdf"
    second=root_folder/"Image4916.pdf"
    first.write_bytes(b"%PDF-1.4\nintentionally synthetic\n")
    second.write_bytes(b"%PDF-1.4\nother synthetic original\n")
    (root_folder/"COMPLETED").mkdir()
    ns={"__name__":"mfcase_v527_ui_test","__file__":str(root_folder/"case_number_ocr.py")}
    exec(compile(source,"case_number_ocr.py","exec"),ns)
    root=tk.Tk()
    root.geometry("1600x920")
    root.update_idletasks()
    app=ns["App"](root)
    app.folder=root_folder
    app._apply_folder_load("08",{},[first,second],False)
    iid="pdf::Image4915.pdf"
    iid2="pdf::Image4916.pdf"
    rec=app.records[iid]
    rec2=app.records[iid2]
    rec["candidate"]="08-15"
    rec["score"]=91.
    rec["status"]="3x3 OCR — MANUAL REVIEW"
    rec["scan_source"]="fallback"
    # Synthetic review image contains a red bounding box; avoids any OCR
    # engine installation and ensures direct UI controls can be exercised.
    picture=np.full((1100,870,3),240,dtype=np.uint8)
    picture[70:125,650:800]=(85,85,85)
    rec["snippet"]=(0,picture,"Top-right manual read",
         (650,70,800,125),
         [{"number":"08-15","rect":(650,70,800,125),
           "score":91.,"engines":["EasyOCR"]}])
    app._insert_scan_row(iid,rec)
    app.tree.selection_set((iid,))
    app._refresh_review_controls()
    assert str(app.review_btn.cget("state"))=="normal"

    # The root PDF must not be auto-moved regardless of approval status
    # or how many deferred scheduler callbacks execute.
    rec2.update(candidate="08-22",confirmed="08-22",manual_approved=True,
                auto_labeled=True,status="Approved — ready for export")
    app._insert_scan_row(iid2,rec2)
    app._save_state()
    app._schedule_auto_export()
    app._run_scheduled_auto_export()
    app.export(automatic=True)
    for _ in range(4):root.update()
    assert first.is_file() and second.is_file()
    assert sorted(p.name for p in (root_folder/"COMPLETED").iterdir())==[]
    print("PASS: auto-approved files remain in root until an explicit SEND ALL SELECTED")

    # Simulate a shared learning outage. The CONFIRMED number must still be
    # saved to root state and cannot revert to the OCR '08-15' candidate.
    import tkinter.messagebox as msgbox
    warnings=[]
    original_warning=msgbox.showwarning
    msgbox.showwarning=lambda *args,**kwargs:warnings.append((args,kwargs))
    saved_training=[]
    app._save_learning_example=lambda *args,**kw:(
        (_ for _ in ()).throw(OSError("Simulated disconnected learning share")))

    def descend(node):
        for child in node.winfo_children():
            yield child
            yield from descend(child)
    def find_review():
        wins=[w for w in root.winfo_children() if isinstance(w,tk.Toplevel)]
        assert len(wins)==1,[str(w) for w in wins]
        return wins[0]
    def find_text_entry(win,contains):
        matches=[w for w in descend(win) if isinstance(w,ttk.Entry)
                 and contains in str(w.get())]
        assert matches,[("entry",w.get()) for w in descend(win) if isinstance(w,ttk.Entry)]
        return matches[0]
    def click_save_review(win):
        buttons=[w for w in descend(win) if isinstance(w,ttk.Button)
                 and w.cget("text")=="SAVE REVIEW"]
        assert len(buttons)==1,[w.cget("text") for w in descend(win)
                                if isinstance(w,ttk.Button)]
        buttons[0].invoke()
    app.review_selected()
    win=find_review()
    entry=find_text_entry(win,"08-15")
    entry.delete(0,"end");entry.insert(0,"08-44")
    click_save_review(win)
    root.update_idletasks()
    assert rec["confirmed"]=="08-44",rec
    assert rec["candidate"]=="08-15",rec
    assert rec["manual_approved"] and not rec["auto_labeled"],rec
    assert rec["learning_pending"] is True
    assert first.is_file() and second.is_file()
    assert app.tree.set(iid,"candidate")=="08-44",app.tree.item(iid)
    assert app.tree.set(iid,"workflow")=="COMPLETED"
    state=json.loads((root_folder/".case_ocr_state.json").read_text())
    assert state["files"]["Image4915.pdf"]["confirmed"]=="08-44"
    assert state["files"]["Image4915.pdf"]["learning_pending"] is True
    assert warnings and "saved" in warnings[0][0][0].lower()
    print("PASS: human correction 08-44 persisted/displayed over OCR 08-15 during learning outage")

    # A scan result arriving after the review cannot rewrite a human
    # confirmation, even if EasyOCR thinks 08-15 is 100% correct.
    app.queue.put(("row",iid,"08-15",rec["snippet"],
                   "Case field right-of-label full number — 3x3 labeled OCR",
                   False,100.,["EasyOCR"],False,1))
    app._poll()
    assert rec["confirmed"]=="08-44" and app.tree.set(iid,"candidate")=="08-44"
    print("PASS: later OCR results do not overwrite manual review")

    # Reopen the same PDF, edit again, and verify a successful learning call
    # clears pending-learning status without touching the original file.
    app._save_learning_example=lambda record,number:saved_training.append((record["path"].name,number))
    app.tree.selection_set((iid,))
    app.review_selected()
    win=find_review()
    entry=find_text_entry(win,"08-44")
    entry.delete(0,"end");entry.insert(0,"08-45")
    click_save_review(win)
    assert rec["confirmed"]=="08-45" and app.tree.set(iid,"candidate")=="08-45"
    assert rec["learning_pending"] is False
    assert saved_training==[("Image4915.pdf","08-45")]
    assert first.is_file()
    print("PASS: reopened REVIEW SELECTED starts at corrected number and accepts further revision")

    # The actual REFRESH button must release the animation on folder load.
    # This checks the formerly mismatched "folder" vs "rolls" job key.
    app._folder_load_generation=2
    app._loader_start("rolls","Refreshing root-folder PDFs…")
    assert app._loader_panel is not None and "rolls" in app._loading_jobs
    app.queue.put(("folder_load_result",2,"08",
                   json.loads((root_folder/".case_ocr_state.json").read_text())["files"],
                   [first,second],False))
    app._poll()
    assert app._loader_panel is None,app._loading_jobs
    assert "rolls" not in app._loading_jobs
    assert app.records[iid]["confirmed"]=="08-45"
    assert app.tree.set(iid,"candidate")=="08-45"
    assert app.records[iid]["manual_approved"]
    assert first.is_file()
    print("PASS: spinner closes on successful root refresh and retains corrected number")

    # A hung network worker must not block the application indefinitely.
    app._folder_load_generation=3
    app._loader_start("rolls","Refreshing root-folder PDFs…")
    app._refresh_timeout(3)
    assert app._loader_panel is None and "rolls" not in app._loading_jobs
    assert first.is_file() and second.is_file()
    print("PASS: refresh watchdog releases spinner without deleting root data")

    # Check manual send: only the highlighted, approved PDF moves into
    # COMPLETED and the other approved one remains in root.
    app.tree.selection_set((iid,))
    app._send_all_selected()
    deadline=time.monotonic()+10
    while time.monotonic()<deadline and first.exists():
        root.update()
        time.sleep(.03)
    assert not first.exists(),app.status.cget("text")
    assert second.exists()
    assert (root_folder/"COMPLETED"/"08-45.pdf").is_file()
    # Drain the queued result and ensure the moved file leaves SCAN.
    app._poll()
    assert not app.tree.exists(iid)
    assert app.tree.exists(iid2)
    print("PASS: SEND ALL SELECTED moves ONLY selected approved PDF; others remain editable")

    msgbox.showwarning=original_warning
    root.destroy()
print("ALL V5.2.7 MANUAL-SAVE, REFRESH AND EXPLICIT-SEND TESTS PASSED")
