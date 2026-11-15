"""V5.2.0 behavioral smoke tests for actual packaged source under virtual X display."""
import ast,base64,hashlib,io,json,os,tempfile,threading,zipfile
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import messagebox
import numpy as np
from PIL import Image

folder=Path("release-assets")
data=base64.b64decode(folder.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(data).hexdigest()==folder.joinpath("SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(data)) as z:
    source=z.read("case_number_ocr.py").decode()
tree=ast.parse(source)
assert len([x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=="App"])==1
os.environ["MFCASE_SKIP_STARTUP_MAINTENANCE"]="1"

with tempfile.TemporaryDirectory() as tmp:
    location=Path(tmp)
    file=location/"Image4869.pdf"
    file.write_bytes(b"%PDF-1.4\n%case sample\n%%EOF\n")
    comp=location/"COMPLETED"
    comp.mkdir()
    sent=comp/"08-29.pdf"
    sent.write_bytes(b"%PDF-1.4\n%previously sent\n%%EOF\n")
    ns={"__name__":"mfcase_qa","__file__":str(location/"case_number_ocr.py")}
    exec(compile(source,"case_number_ocr.py","exec"),ns)
    root=tk.Tk()
    root.withdraw()
    app=ns["App"](root)
    app.folder=location
    app.learning_dir=location/"shared-learning"
    app.learning_dir.mkdir()
    saved={
      "Image4845.pdf":{"confirmed":"08-29","candidate":"08-29","exported":True,
                       "export_name":"08-29.pdf","original_name":"Image4845.pdf",
                       "status":"Moved to COMPLETED: 08-29.pdf","manual_approved":True},
      "Image4869.pdf":{"status":"Not scanned"}}
    app._apply_folder_load("08",saved,[file,sent],False)
    root.update_idletasks()
    ids=app.tree.get_children("")
    assert len(ids)==2,(ids,list(app.records))
    by_name={app.records[i]["path"].name:i for i in ids}
    iid_sent=by_name["08-29.pdf"]
    iid_root=by_name["Image4869.pdf"]
    assert app.tree.set(iid_sent,"workflow")=="SENT"
    assert app.tree.set(iid_root,"workflow")=="NOT SCANNED"
    assert app.completed_nav_btn.winfo_manager()==""
    assert app.review_btn.winfo_manager()==""
    assert app.settings_nav_btn.winfo_manager()=="pack"
    app._show_main_view("settings")
    assert app._active_main_view=="settings"
    assert "Shared learning" in app.connection_status_label.cget("text")
    app._show_main_view("select")
    assert app._active_main_view=="select"
    print("PASS: unified SCAN/SETTINGS, SENT and unscanned rows")

    # Only a confirmed yes allows a SENT PDF to move out of COMPLETED for
    # re-review; never overwrite an existing root PDF.
    opened=[]
    previous=messagebox.askyesno
    old_show=app._show_review_item
    old_id=app.tree.identify_row
    try:
        app.tree.identify_row=lambda y:iid_sent
        app._show_review_item=lambda idx:opened.append(idx)
        messagebox.askyesno=lambda *a,**k:False
        app._unified_double_click(SimpleNamespace(y=0))
        assert sent.is_file() and not (location/"Image4845.pdf").exists()
        assert not opened
        messagebox.askyesno=lambda *a,**k:True
        app._unified_double_click(SimpleNamespace(y=0))
        restored=location/"Image4845.pdf"
        assert restored.is_file() and not sent.exists()
        assert opened==[0] and app.tree.set(iid_sent,"workflow")=="REVIEW"
    finally:
        messagebox.askyesno=previous
        app.tree.identify_row=old_id
        app._show_review_item=old_show
    print("PASS: SENT double-click confirmation and collision-free restore")

    # Sending the same restored PDF moves it back, without doubling data.
    app._export_worker(comp,[(iid_sent,restored,"08-0029")])
    events=[]
    while not app.queue.empty():
        events.append(app.queue.get_nowait())
    exports=[x for x in events if x[0]=="export_result"]
    assert len(exports)==1,exports
    _,record_id,original_name,destination,error=exports[0]
    assert not error and Path(destination).name=="08-29.pdf"
    app._apply_export_result(record_id,original_name,destination,error)
    assert not restored.exists() and sent.is_file()
    assert len(list(comp.glob("*.pdf")))==1
    assert app.tree.set(iid_sent,"workflow")=="SENT"
    assert "08-29" in app.tree.set(iid_sent,"file")
    print("PASS: moved PDF stored once in root/COMPLETED; SENT stays visible")

    # Learning thumbnail must be taken from chosen red box unless a manual
    # redraw is completed. The entered answer must be the stored target.
    image=np.full((700,1000,3),255,np.uint8)
    image[25:80,750:910]=(5,220,40)   # OCR red box location
    image[240:330,620:830]=(230,20,50) # manually redrawn location
    record={
      "path":file,"candidate":"08-29","score":100.0,
      "snippet":(0,image,"08-29",(750,25,910,80),
                 [{"number":"08-29","rect":(750,25,910,80),"score":100.0,
                   "engines":["EasyOCR"]}]),
      "manual_crop_rect":(620,240,830,330),
      "manual_crop_origin":"candidate",
      "training_type":"printed","engines":["EasyOCR"],
      "engine_agreement":False,"scan_source":"incident"}
    # Replace network persistence and UI status, but execute the actual image
    # selection and saving implementation.
    app.learning_data={"examples":[],"field_regions":[]}
    app.data_lock=threading.RLock()
    app.reader=None
    app._persist_learning_data=lambda **kw:None
    app._refresh_learning_stats=lambda:None
    app._refresh_learning_button_state=lambda:None
    app._save_state=lambda:None
    app._diag_event=lambda *a,**k:None
    app._diag_sync_shared=lambda:None
    app._save_learning_example(record,"08-29")
    first=app.learning_data["examples"][-1]
    assert first["crop_origin"]=="candidate-number-box"
    assert first["confirmed_case_number"]=="08-29"
    assert first["training_type"]=="printed"
    saved_img=Image.open(app.learning_dir/first["image"])
    center=saved_img.getpixel((saved_img.width//2,saved_img.height//2))
    assert center[1]>150 and center[0]<100,center

    record["manual_crop_origin"]="drawn"
    record["training_type"]="handwritten"
    app._save_learning_example(record,"08-29")
    second=app.learning_data["examples"][-1]
    assert second["crop_origin"]=="manual-field"
    assert second["training_type"]=="handwritten"
    saved_img=Image.open(app.learning_dir/second["image"])
    center=saved_img.getpixel((saved_img.width//2,saved_img.height//2))
    assert center[0]>150 and center[1]<100,center

    record["manual_crop_origin"]="drawn"
    app._save_learning_example(record,"08-30")
    third=app.learning_data["examples"][-1]
    assert third["confirmed_case_number"]=="08-30"
    print("PASS: red OCR box, manually drawn box, optional category and corrected target")
    root.destroy()
print("PASS: real application smoke tests completed")
