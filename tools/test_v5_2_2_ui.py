"""Real Tk smoke tests of MFCase 5.2.2 root-only UI and REVIEW SELECTED."""
import ast,base64,hashlib,io,json,os,tempfile,zipfile
from pathlib import Path
import tkinter as tk

rootdir=Path("release-assets")
raw=base64.b64decode(rootdir.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(raw).hexdigest()==rootdir.joinpath("SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    source=z.read("case_number_ocr.py").decode()
    assert "def finish_box(event):" in source
    assert "def update_crop_preview():" in source
    assert "def _save_learning_example(" in source
os.environ["MFCASE_SKIP_STARTUP_MAINTENANCE"]="1"
with tempfile.TemporaryDirectory(prefix="MFCase V5.2.2 QA ") as tmp:
    folder=Path(tmp)
    incomplete=folder/"Image4869.pdf"
    approved=folder/"Image4870.pdf"
    for path in [incomplete,approved]:
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    completed=folder/"COMPLETED"
    completed.mkdir()
    sent=completed/"08-29.pdf"
    sent.write_bytes(b"%PDF-1.4\nsent bytes\n%%EOF\n")
    arbitrary=folder/"Misc"
    arbitrary.mkdir()
    (arbitrary/"Image4871.pdf").write_bytes(b"%PDF-1.4\n")
    (folder/".case_ocr_state.json").write_text(json.dumps({
        "version":2,"files":{
            "Image4869.pdf":{"candidate":"08-30","status":"Not scanned"},
            "Image4870.pdf":{"candidate":"08-32","confirmed":"08-32",
                    "manual_approved":True,"status":"Approved — ready for export"},
            "Image4845.pdf":{"candidate":"08-29","confirmed":"08-29",
                    "exported":True,"export_name":"08-29.pdf",
                    "status":"Moved to COMPLETED: 08-29.pdf"}
        }}))
    ns={"__name__":"mfcase_qa","__file__":str(folder/"case_number_ocr.py")}
    exec(compile(source,"case_number_ocr.py","exec"),ns)
    root=tk.Tk()
    root.withdraw()
    app=ns["App"](root)
    app.folder=folder
    app._folder_load_worker(folder,1)
    events=[]
    while not app.queue.empty():
        events.append(app.queue.get_nowait())
    relevant=[e for e in events if e[0]=="folder_load_result"]
    assert len(relevant)==1,relevant
    _,_,year,saved,pdfs,state_error=relevant[0]
    assert {p.name for p in pdfs}=={"Image4869.pdf","Image4870.pdf"},pdfs
    assert all(p.parent==folder for p in pdfs)
    app._apply_folder_load(year,saved,pdfs,state_error)
    root.update_idletasks()

    ids=app.tree.get_children("")
    assert len(ids)==2,ids
    byname={app.records[i]["path"].name:i for i in ids}
    assert set(byname)=={"Image4869.pdf","Image4870.pdf"}
    pending=byname["Image4869.pdf"]
    ready=byname["Image4870.pdf"]
    assert app.tree.set(ready,"workflow")=="COMPLETED"
    assert app.tree.set(pending,"workflow")=="NOT SCANNED"
    assert app.review_btn.winfo_manager()=="pack"
    assert app.review_btn.cget("text")=="REVIEW SELECTED"
    assert app.completed_nav_btn.winfo_manager()==""
    assert app.settings_nav_btn.winfo_manager()=="pack"
    print("PASS: root-only listing; completed-but-unsent visible; REVIEW SELECTED restored")

    # Select completed PDF; the restored button must invoke original review
    # queue (same _show_review_item containing the real crop drawing code).
    captured=[]
    original_show=app._show_review_item
    try:
        app._show_review_item=lambda index:captured.append(index)
        app.tree.selection_set((ready,))
        app._refresh_review_controls()
        assert str(app.review_btn.cget("state"))=="normal"
        app.review_btn.invoke()
        assert app.review_queue==[ready] and captured==[0]
        app.tree.selection_set((pending,))
        app._refresh_review_controls()
        assert str(app.review_btn.cget("state"))=="disabled"
    finally:
        app._show_review_item=original_show
    print("PASS: button opens selected confirmed PDF in original full-crop review window")

    # A failed send retains the original PDF and its pending COMPLETED row.
    app._apply_export_result(ready,approved.name,"","Simulated access denied")
    assert approved.exists()
    assert app.tree.exists(ready)
    assert app.tree.set(ready,"workflow")=="COMPLETED"
    assert "Transfer failed" in app.tree.set(ready,"status")
    print("PASS: failed transfer remains visible in root")

    # A real successful worker move removes source and row, and leaves the
    # already sent PDF in COMPLETED without overwriting it.
    app._export_worker(completed,[(ready,approved,"08-0032")])
    events=[]
    while not app.queue.empty():
        events.append(app.queue.get_nowait())
    results=[e for e in events if e[0]=="export_result"]
    assert len(results)==1,results
    _,iid,original_name,target,error=results[0]
    assert iid==ready and not error,(iid,target,error)
    app._apply_export_result(iid,original_name,target,error)
    assert not approved.exists()
    assert (completed/"08-32.pdf").is_file()
    assert sent.is_file() and sent.read_bytes()==b"%PDF-1.4\nsent bytes\n%%EOF\n"
    assert not app.tree.exists(ready)
    assert len(app.tree.get_children(""))==1
    print("PASS: successful send removes row; no duplicate PDF in root")

    # Reload or refresh still enumerates root only, not nested COMPLETED PDFs.
    app._folder_load_worker(folder,2)
    more=[]
    while not app.queue.empty():
        more.append(app.queue.get_nowait())
    reload=[e for e in more if e[0]=="folder_load_result"][-1]
    _,_,year,state,pdfs,error=reload
    assert len(pdfs)==1 and pdfs[0].name=="Image4869.pdf"
    app._apply_folder_load(year,state,pdfs,error)
    assert len(app.tree.get_children(""))==1
    print("PASS: reload does not display PDFs already in COMPLETED")

    # A previously exported filename returned to root must NOT be mistaken
    # for an already sent PDF.
    returned=folder/"Image4870.pdf"
    returned.write_bytes(b"%PDF-1.4\nreturned bytes")
    oldstate={"Image4870.pdf":{"exported":True,"confirmed":"08-32",
              "status":"Moved to COMPLETED: 08-32.pdf","manual_approved":True}}
    app._apply_folder_load("08",oldstate,[incomplete,returned],False)
    ids={app.records[i]["path"].name:i for i in app.tree.get_children("")}
    assert app.tree.set(ids["Image4870.pdf"],"workflow")=="NOT SCANNED"
    print("PASS: restored root original is not incorrectly treated as SENT")

    # True image thumbnails are embedded beside the PDF file column.
    import numpy as np
    assert "preview" in app.tree["columns"]
    root.geometry("1550x900")
    root.deiconify()
    root.update()
    iid=ids["Image4870.pdf"]
    demo=np.full((480,760,3),255,dtype=np.uint8)
    demo[38:85,520:690]=(14,45,180)
    app.records[iid]["candidate"]="08-33"
    app.records[iid]["snippet"]=(0,demo,"08-33 — incident",
         (520,38,690,85),
         [{"number":"08-33","rect":(520,38,690,85),"score":98.3,
           "engines":["EasyOCR-incident"]}])
    app._insert_scan_row(iid,app.records[iid])
    root.update()
    app._refresh_scan_previews()
    assert iid in app._scan_preview_photos, list(app._scan_preview_photos)
    photo=app._scan_preview_photos[iid]
    assert photo.width()>=200 and photo.height()>=64,(photo.width(),photo.height())
    assert iid in app._scan_preview_widgets
    bbox=app.tree.bbox(iid,"preview")
    assert bbox and bbox[2]>=200,bbox
    print("PASS: readable OCR field thumbnail placed beside PDF filename")

    root.destroy()
print("PASS: MFCase V5.2.2 root-only review GUI and transfer regressions")
