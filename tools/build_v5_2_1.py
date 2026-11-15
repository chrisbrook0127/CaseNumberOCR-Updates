"""MFCase 5.2.1: show root-only PDF files; restore REVIEW SELECTED.

Built from immutable, published V5.2.0 (not a retired release).
The user has explicitly requested no listing of already-sent PDFs in
root/COMPLETED. Only approved files still physically in root are listed.
"""
from pathlib import Path
import ast, base64, hashlib, io, subprocess, zipfile

root=Path("release-assets")
pinned=base64.b64decode(subprocess.check_output(
    ["git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),
    validate=True)
assert hashlib.sha256(pinned).hexdigest()=="b481635a729fa080e26aa357aa3ef9bec1ca8d8b8295b2e1a3bd04d00eeb0342"
with zipfile.ZipFile(io.BytesIO(pinned)) as inp:
    assert inp.testzip() is None
    assert set(inp.namelist())=={"case_number_ocr.py","updater_apply.py"}
    src=inp.read("case_number_ocr.py").decode("utf-8")
    updater=inp.read("updater_apply.py")
assert src.count('self.app_version = "5.2.0"')==1
src=src.replace('self.app_version = "5.2.0"','self.app_version = "5.2.1"',1)
assert src.count('root.title("M.F. Case Number OCR — V5.2.0")')==1
src=src.replace('root.title("M.F. Case Number OCR — V5.2.0")',
                'root.title("M.F. Case Number OCR — V5.2.1")',1)
assert src.count('Case Number OCR & Renamer — V5.2.0')==1
src=src.replace('Case Number OCR & Renamer — V5.2.0',
                'Case Number OCR & Renamer — V5.2.1',1)
assert src.count('Application version: V5.2.0')==1
src=src.replace('Application version: V5.2.0',
                'Application version: V5.2.1',1)

def replace_once(old,new):
    global src
    c=src.count(old)
    assert c==1, ("Unexpected source change",c,old[:150])
    src=src.replace(old,new,1)

def replace_method(name,body):
    global src
    tree=ast.parse(src)
    matches=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(matches)==1,(name,len(matches))
    n=matches[0]
    lines=src.splitlines(keepends=True)
    src="".join(lines[:n.lineno-1])+body.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(src,"case_number_ocr.py","exec")

# Enumerate only direct PDF children of the root, never root/COMPLETED or
# any other nested directory. Preserve stable natural filename order.
replace_once('''            pdfs = [p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower()==".pdf"]
            completed=folder/"COMPLETED"
            if completed.is_dir():
                pdfs.extend(p for p in completed.iterdir()
                            if p.is_file() and p.suffix.lower()==".pdf")
            pdfs.sort(key=self._natural_pdf_sort_key)''',
'''            pdfs=sorted(
                (p for p in folder.iterdir()
                 if p.is_file() and p.suffix.lower()==".pdf"),
                key=self._natural_pdf_sort_key)''')

# When a previously transferred original is put back into root, it is
# a new root file for scanning. A stale SENT state must not hide it.
replace_once('''            old = saved.get(pdf.name, {})
            if not old:
                old=next((v for v in saved.values()
                          if v.get("export_name")==pdf.name and
                          pdf.parent.name.upper()=="COMPLETED"),{})
            sent=pdf.parent.name.upper()=="COMPLETED"''',
'''            old=saved.get(pdf.name,{})
            if old.get("exported"):
                old={}  # A PDF physically in root is not already sent.
            sent=False''')
replace_once('''"completed_iid": None, "exported": bool(old.get("exported",False) or sent),''',
''' "completed_iid": None, "exported": False,''')

# Guard the unified list against other callbacks inadvertently re-inserting
# a PDF moved into COMPLETED. This is the same root-only invariant as load.
replace_once('''        if not hasattr(self,"tree"):return
        values=("☑" if iid in self.scan_checked_iids else "☐",''',
'''        if not hasattr(self,"tree"):return
        if self.folder and Path(record["path"]).parent != Path(self.folder):
            if self.tree.exists(iid):
                self.tree.delete(iid)
            return
        values=("☑" if iid in self.scan_checked_iids else "☐",''')

# A successful transfer removes the PDF from the Scan list. Pending
# COMPLETED files remain visible until the move actually succeeds.
replace_once('''            # SENT remains visible in the single Scan list.
            self._insert_scan_row(iid,rec)''',
'''            # Physical move succeeded: exclude this non-root PDF from SCAN.
            if self.tree.exists(iid):
                self.tree.delete(iid)''')
replace_once('''            rec["completed_iid"] = None
            self._insert_scan_row(iid,rec)
            message = f"Moved to COMPLETED:''',
'''            rec["completed_iid"] = None
            message = f"Moved to COMPLETED:''')

# Put the original full preview/redraw button back, without reinstating
# the retired second REVIEW screen.
replace_once('''            action_bar, text="REVIEW FILES", command=self.review_next, state="disabled")
        # The old REVIEW FILES button is not shown; double-click a SCAN row.''',
'''            action_bar, text="REVIEW SELECTED", command=self.review_selected, state="disabled")
        self.review_btn.pack(side="left",padx=5,ipadx=8,ipady=3)''')

# Select, then review; selected root files must be scanned or already
# approved. A manually completed-but-unsent record is reviewable again.
controls=r'''
    def _refresh_review_controls(self):
        if not hasattr(self,"review_btn") or not hasattr(self,"tree"):
            return
        ready=set(self._review_ready_ids())
        enabled=not self.trocr_training and any(
            iid in ready and self.tree.exists(iid) and
            (not self.folder or Path(self.records[iid]["path"]).parent==Path(self.folder))
            for iid in self.tree.selection())
        state="normal" if enabled else "disabled"
        self.review_btn.config(state=state)
'''
replace_method("_refresh_review_controls",controls)
# Refresh the button on every row selection, not just on OCR progress.
replace_once('''        self._refresh_scan_button()
        self._refresh_rescan_button()
        self._save_state()

    def _refresh_scan_checkbox_heading''',
'''        self._refresh_scan_button()
        self._refresh_rescan_button()
        self._refresh_review_controls()
        self._save_state()

    def _refresh_scan_checkbox_heading''')

# Reject export and review of exported files left in the in-memory
# record dictionary after their move. They are not root file entries.
replace_once('''        selected = [
            iid for iid in self.tree.selection()
            if iid in self._review_ready_ids()]''',
'''        ready=set(self._review_ready_ids())
        selected = [
            iid for iid in self.tree.selection()
            if iid in ready and not self.records[iid].get("exported")
            and (not self.folder or Path(self.records[iid]["path"]).parent==Path(self.folder))]''')

# Failed transfers have not left root; update the visible status.
replace_once('''            if self.completed_tree.exists(iid):
                self.completed_tree.item(iid, values=self._completed_row_values(iid, rec))
        else:
            target = Path(destination)''',
'''            if self.completed_tree.exists(iid):
                self.completed_tree.item(iid, values=self._completed_row_values(iid, rec))
            self._insert_scan_row(iid,rec)
        else:
            target = Path(destination)''')

# Update instructions to describe actual single-screen behavior.
src=src.replace(
    'The root PDFs and the root/COMPLETED subfolder are displayed together; no other subfolders are scanned.',
    'Only PDFs directly in the selected root folder are listed. Already sent PDFs in root/COMPLETED do not appear.')
src=src.replace(
    '5. SENT means the renamed PDF has actually been moved to the '
    '"COMPLETED subfolder. The program never creates duplicate sent PDFs. "',
    '5. SENT means the renamed PDF has been moved into the '
    '"COMPLETED subfolder and no longer appears in SCAN. "')
# Verify shipped source preserves OCR rules, review crop functions and updater.
compile(src,"case_number_ocr.py","exec")
tree=ast.parse(src)
assert src.count('self.app_version = "5.2.1"')==1
assert 'text="REVIEW SELECTED", command=self.review_selected' in src
assert 'self.review_btn.pack(' in src
assert 'def _show_review_item(self, index):' in src
assert 'def finish_box(event):' in src
assert 'dimension_px=int(round(3*72*2.2))' in src
assert 'minimum_independent_matches=3' in src
assert 'self._save_learning_example(r, value)' in src
assert 'os.utime(destination, None)' in src
assert 'def _diag_export(' in src
assert 'def _diag_sync_shared(' in src
method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="_folder_load_worker")
assert 'completed.iterdir()' not in ast.get_source_segment(src,method)
output=io.BytesIO()
with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as out:
    out.writestr("case_number_ocr.py",src.encode("utf-8"))
    out.writestr("updater_apply.py",updater)
payload=output.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    compile(z.read("case_number_ocr.py"),"case_number_ocr.py","exec")
    compile(z.read("updater_apply.py"),"updater_apply.py","exec")
digest=hashlib.sha256(payload).hexdigest()
(root/"update.base64").write_text(base64.b64encode(payload).decode("ascii"))
(root/"CaseNumberOCR_Update.zip").write_bytes(payload)
(root/"SHA256.txt").write_text(digest+"\n")
(root/"RELEASE_TAG.txt").write_text("v5.2.1\n")
(root/"PUBLISH_REQUEST.txt").write_text("V5.2.1: root-only SCAN and restored REVIEW SELECTED\n")
print("PASS: V5.2.1 root-only review build",len(payload),"SHA256:",digest)
