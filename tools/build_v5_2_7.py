"""Build MFCase V5.2.7 from exact, published V5.2.6; manual-only send.

Regression fixes:
- User-entered case number is the authoritative displayed/saved value.
- A failed learning sync cannot revert the user's manual confirmation.
- Approved files remain visible in root and are NEVER auto-exported.
- No PDF, including an OCR-detected blank, is moved without SEND ALL SELECTED.
- REFRESH spinner uses matching start/finish keys and a bounded wait.
"""
from pathlib import Path
import ast,base64,hashlib,io,zipfile

ROOT=Path("release-assets")
payload=base64.b64decode(ROOT.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(payload).hexdigest()=="1cd31372fda81e93aa019d07e5407571a9af51edd7997df48519c1aefc856a4a"
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    original=z.read("case_number_ocr.py").decode()
    updater=z.read("updater_apply.py")
assert original==Path("development/v5.2.6_case_number_ocr.py").read_text()
s=original

def swap(old,new,n=1):
    global s
    assert s.count(old)==n,(s.count(old),n,old[:140])
    s=s.replace(old,new,n)

def method(name,body):
    global s
    t=ast.parse(s)
    found=[n for n in ast.walk(t) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(found)==1,(name,len(found))
    n=found[0]
    lines=s.splitlines(keepends=True)
    s="".join(lines[:n.lineno-1])+body.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(s,"case_number_ocr.py","exec")

swap('self.app_version = "5.2.6"','self.app_version = "5.2.7"')
for before,after in [
    ("M.F. Case Number OCR — V5.2.6","M.F. Case Number OCR — V5.2.7"),
    ("Case Number OCR & Renamer — V5.2.6","Case Number OCR & Renamer — V5.2.7"),
    ("Application version: V5.2.6","Application version: V5.2.7"),
    ("MFCase-Connectivity/5.2.6","MFCase-Connectivity/5.2.7")]:
    s=s.replace(before,after)

# Core bug: REFRESH started "folder" but _poll finishes "rolls", leaving
# _loading_jobs nonempty and the overlay on screen FOREVER.
swap('''        self._folder_load_generation = 0''',
'''        self._folder_load_generation = 0
        self._folder_load_timeout_id = None''')
swap('''        """Reload both root PDFs and the root/COMPLETED subfolder."""''',
'''        """Refresh direct root PDFs only, retaining all saved manual decisions."""''')
swap('''        self._loader_start("folder","Refreshing root and COMPLETED PDFs…")''',
'''        self.refresh_root_btn.config(state="disabled")
        self._loader_start("rolls","Refreshing root-folder PDFs…")
        if self._folder_load_timeout_id is not None:
            self.root.after_cancel(self._folder_load_timeout_id)
        self._folder_load_timeout_id=self.root.after(
            16000,lambda generation=generation:self._refresh_timeout(generation))''')
# Both folder completion and error cancel a prior refresh timeout.
swap('''                    if generation == self._folder_load_generation:
                        try:
                            self._apply_folder_load(year, saved, pdfs, state_error)
                        finally:
                            self._loader_finish("rolls")''',
'''                    if generation == self._folder_load_generation:
                        if self._folder_load_timeout_id is not None:
                            self.root.after_cancel(self._folder_load_timeout_id)
                            self._folder_load_timeout_id=None
                        try:
                            self._apply_folder_load(year, saved, pdfs, state_error)
                        finally:
                            self._loader_finish("rolls")''')
swap('''                    if generation == self._folder_load_generation:
                        self._loader_finish("rolls")
                        self.refresh_root_btn.config(state="normal")''',
'''                    if generation == self._folder_load_generation:
                        if self._folder_load_timeout_id is not None:
                            self.root.after_cancel(self._folder_load_timeout_id)
                            self._folder_load_timeout_id=None
                        self._loader_finish("rolls")
                        self.refresh_root_btn.config(state="normal")''')
swap('''                        self.folder = None
                        self.status.config(text="Could not load the selected PDF folder.")''',
'''                        self.status.config(text="Could not refresh root PDFs; current list remains unchanged.")''')
refresh_timeout=r'''
    def _refresh_timeout(self,generation):
        """Never keep the Tk loading overlay indefinitely on an SMB delay.

        The worker may still finish and update the list later. Existing saved
        decisions remain intact; the UI stays usable during slow network I/O.
        """
        self._folder_load_timeout_id=None
        if generation!=self._folder_load_generation:
            return
        self._loader_finish("rolls")
        self.refresh_root_btn.config(state="normal")
        self.choose_folder_btn.config(state="normal")
        self.status.config(
            text="Folder refresh is taking longer than expected. Current PDFs "
                 "remain visible; delayed network results will load when ready.")
        self._diag_event("folder_refresh_wait",
            outcome="overlay_released_while_network_worker_pending",
            generation=generation)

'''
swap('''    def refresh_root_folder(self):''',refresh_timeout+'''    def refresh_root_folder(self):''')
swap('''        if not self._startup_ready or self.busy or self.trocr_training or self.export_busy:
            return
        if not self.folder or not self.folder.is_dir():''',
'''        if not self._startup_ready or self.busy or self.trocr_training or self.export_busy:
            return
        if "rolls" in self._loading_jobs:
            self.status.config(text="Folder refresh is already in progress.")
            return
        if not self.folder or not self.folder.is_dir():''')

# Confirmed case is the authoritative user-facing answer. Preserve the
# unmodified candidate as evidence for the OCR diagnostics and learning.
swap('''        self.tree.heading("candidate", text="Candidate")''',
'''        self.tree.heading("candidate", text="CASE NUMBER")''')
swap('''                record.get("candidate","") if candidate_override is None else candidate_override,''',
'''                (record.get("confirmed") if record.get("manual_approved")
                 and record.get("confirmed") else record.get("candidate",""))
                if candidate_override is None else candidate_override,''',1)
swap('''                "scan_selected": bool(rec.get("scan_selected", True)),''',
'''                "scan_selected": bool(rec.get("scan_selected", True)),
                "learning_pending": bool(rec.get("learning_pending", False)),''')
swap('''                "scan_selected": bool(old.get("scan_selected", True)),''',
'''                "scan_selected": bool(old.get("scan_selected", True)),
                "learning_pending": bool(old.get("learning_pending", False)),''')
swap('''            os.replace(temp, target)
        except Exception:
            logging.exception("Failed to save queue state")''',
'''            os.replace(temp, target)
            return True
        except Exception:
            logging.exception("Failed to save queue state")
            return False''')

# Avoid an OCR worker overwriting a human correction made while the scan
# continues. Human approval may change only through deliberate REVIEW.
swap('''                    normalized_candidate = normalize_case_number(cand) if cand else ""
                    r.update(''',
'''                    normalized_candidate = normalize_case_number(cand) if cand else ""
                    if r.get("manual_approved") and not r.get("auto_labeled"):
                        self._diag_event("manual_override_preserved",
                            file=Path(r["path"]).name,ocr_candidate=normalized_candidate,
                            confirmed=r.get("confirmed"),score=float(score or 0))
                        if snip is not None and r.get("snippet") is None:
                            r["snippet"]=snip
                        self._insert_scan_row(iid,r)
                        self._scan_progress_update(idx,"SCANNING")
                        self._save_state()
                        continue
                    r.update(''')
swap('''                                "sending to COMPLETED")''',
'''                                "awaiting SEND ALL SELECTED")''')
swap('''                    self._scan_progress_update(len(self.scan_items),"OCR COMPLETE — SENDING")''',
'''                    self._scan_progress_update(len(self.scan_items),"SCAN COMPLETE — READY TO SEND")''')
swap('''                        text="Scan complete. Sending all approved PDFs to COMPLETED; "
                             "unverified files remain in REVIEW.")''',
'''                        text="Scan complete. All PDFs remain in the root folder. "
                             "Review or edit them, then press SEND ALL SELECTED.")''')
swap('''                        self._scan_progress_update(len(self.scan_items),"SCAN & SEND COMPLETE")''',
'''                        self._scan_progress_update(len(self.scan_items),"SELECTED SEND COMPLETE")''')

# No move is authorized by OCR confidence, by scan completion, by learning,
# or by an old delayed after() callback. Explicit SEND ALL SELECTED ONLY.
method("_schedule_auto_export",'''
    def _schedule_auto_export(self):
        """Do not auto-transfer. User must press SEND ALL SELECTED."""
        return None
''')
method("_run_scheduled_auto_export",'''
    def _run_scheduled_auto_export(self):
        """Legacy delayed callback cannot cause a PDF transfer."""
        self._auto_export_after=None
        return None
''')
swap('''    def export(self, selected_iids=None, automatic=False):
        """Move verified root PDFs into COMPLETED after scan/review."""
        if self.export_busy:''',
'''    def export(self, selected_iids=None, automatic=False):
        """Move verified root PDFs ONLY for a user-initiated SEND selection."""
        if automatic:
            self._diag_event("automatic_export_blocked",
                             reason="explicit_send_all_selected_required")
            return
        if self.export_busy:''')

# Plain blank PDFs ALSO remain in the root and on SCAN until the user takes
# an explicit action. Do not move them to BLANKS during scanning.
swap('''                    if self._pdf_is_blank(source_path):
                        blanks_dir = self.folder / "BLANKS"
                        blanks_dir.mkdir(exist_ok=True)
                        destination = blanks_dir / source_path.name
                        if destination.exists():
                            self.queue.put(("blank_conflict", iid, destination, idx))
                        else:
                            shutil.move(str(source_path), str(destination))
                            self.queue.put(("blank", iid, destination, idx))
                        continue''',
'''                    if self._pdf_is_blank(source_path):
                        self._diag_event("blank_kept_in_root",
                            file=source_path.name,
                            reason="explicit_user_send_required")
                        self.queue.put((
                            "row", iid, "", None,
                            "Blank PDF — retained in root for manual review",
                            False, 0.0, [], False, idx))
                        continue''')
swap('''                    elif not normalized_candidate:
                        r["status"]="No valid 08- case number in 3x3 — checking before/after fallback"''',
'''                    elif not normalized_candidate:
                        r["status"]=(
                            "Blank PDF — retained in root for manual review"
                            if note.startswith("Blank PDF") else
                            "No valid 08- case number in 3x3 — checking before/after fallback")''')

# Manual SAVE is now two distinct operations: first save the user's
# confirmed value to the root-folder state; THEN attempt optional learning.
# Learning failure records a retry flag; it NEVER reverts an approved case.
swap('''        send_after_approval=True''','''        send_after_approval=False''')
start=s.index("        def confirm():\n",s.index("    def _show_review_item(self, index):"))
end=s.index("\n        action_row = label_row + 5",start)
replacement='''        def confirm():
            value=normalize_case_number(entry.get().strip())
            if not CASE_RE.fullmatch(value):
                err.config(text="Case number must start with 08- followed by 1–7 digits.")
                return
            original_ocr=str(r.get("candidate") or "")
            prior_confirmed=str(r.get("confirmed") or "")
            r["training_type"]=training_type_var.get()
            r["confirmed"]=value
            r["manual_approved"]=True
            r["auto_labeled"]=False
            r["sequence_inferred"]=False
            r["sequence_conflict"]=False
            r["scan_ambiguous"]=False
            r["sequence_anchors"]=[]
            r["sequence_excluded"]=False
            r["sequence_suggestion"]=""
            r["export_error"]=""
            r["send_after_manual_approval"]=False
            r["status"]=f"Manual case confirmed as {value} — awaiting SEND ALL SELECTED"
            self._move_to_completed(iid)
            r["status"]=f"Manual case confirmed as {value} — awaiting SEND ALL SELECTED"
            self._insert_scan_row(iid,r)
            if not self._save_state():
                err.config(text="Could not save your correction to the root-folder "
                          "state file. The case remains entered here; check network "
                          "access, then click SAVE REVIEW again.")
                return
            learning_error=None
            try:
                self._save_learning_example(r,value)
            except Exception as exc:
                learning_error=str(exc)
                self._log("Case saved locally, but learning sync failed: %s",exc)
            r["learning_pending"]=bool(learning_error)
            if learning_error:
                r["status"]=(f"Manual case {value} SAVED — learning pending; "
                             "awaiting SEND ALL SELECTED")
            else:
                r["status"]=(f"Manual case {value} SAVED and learning updated — "
                             "awaiting SEND ALL SELECTED")
            self._diag_event("manual_case_confirmation",
                file=Path(r["path"]).name,ocr_candidate=original_ocr,
                previous_confirmed=prior_confirmed,confirmed=value,
                learning_saved=not bool(learning_error),
                transfer="deferred_until_send_all_selected")
            self._insert_scan_row(iid,r)
            self._save_state()
            self.status.config(text=r["status"])
            win.destroy()
            self._apply_sequence_consensus()
            next_index=index+1
            if learning_error:
                messagebox.showwarning("Case number saved",
                    f"{value} is saved and remains in SCAN. Shared learning "
                    f"did not save: {learning_error}\\\\n\\\\n"
                    "Reopen REVIEW SELECTED and save again when the learning "
                    "share is available. No PDF was sent.")
            if next_index<len(self.review_queue):
                self._show_review_item(next_index)
'''
s=s[:start]+replacement+s[end:]

swap('''        ttk.Button(controls,text="SAVE AND SEND",
                   command=confirm)''',
'''        ttk.Button(controls,text="SAVE REVIEW",
                   command=confirm)''')
start=s.index('''        ttk.Label(controls,
                  text=("Approval saves learning and sends this file to COMPLETED.''')
end=s.index('''                  wraplength=280).grid(row=action_row + 2,''',start)
s=s[:start]+'''        ttk.Label(controls,
                  text="SAVE REVIEW stores the corrected 08- case and learning "
                       "without moving any PDF. You can reopen and revise it "
                       "until you press SEND ALL SELECTED.",
'''+s[end:]
# Saving in the former hidden completed editor must update the visible SCAN
# row; learning sync cannot silently revert the manual correction.
swap('''        self._save_learning_label_for_file(record["path"].name, value)
        self.completed_edit_drafts.pop(iid, None)''',
'''        try:
            self._save_learning_label_for_file(record["path"].name,value)
            record["learning_pending"]=False
        except Exception as exc:
            record["learning_pending"]=True
            self._log("Corrected case retained; learning update pending: %s",exc)
        record["status"]=f"Manual case {value} saved — awaiting SEND ALL SELECTED"
        self._insert_scan_row(iid,record)
        self.completed_edit_drafts.pop(iid, None)''')
swap('''        message = f"Case number saved as {value} for {record['path'].name}. Click SEND TO KURT when ready."''',
'''        message = (f"Case number saved as {value} for {record['path'].name}. "
                   "It remains in root until SEND ALL SELECTED.")''')
swap('''        """Keep every PDF visible across review, approval and physical transfer."""''',
'''        """Keep case number and PDF visible until explicit user transfer."""''')

# Document the explicit-send-only workflow and correct stale references.
s=s.replace(
    "Approval saves learning and sends this file to COMPLETED.",
    "Approval saves the corrected case number and learning; no PDF is sent until SEND ALL SELECTED.")
s=s.replace(
    "After a scan, move every eligible COMPLETED PDF, including saved reviews.",
    "No PDF is transferred automatically. Use SEND ALL SELECTED.")
s=s.replace(
    "Click SAVE AND SEND",
    "Click SAVE REVIEW, then SEND ALL SELECTED when ready")
s=s.replace(
    "When scanning finishes, approved records transfer automatically",
    "Approved records remain on SCAN until SEND ALL SELECTED")

# Saving one review returns to the SCAN list. Do not open another PDF
# automatically, which could hide the just-saved correction and create
# overlapping modal review windows if the user reopens a file immediately.
swap('''            next_index=index+1
            if learning_error:''',
     '''            if learning_error:''')
swap('''            if next_index<len(self.review_queue):
                self._show_review_item(next_index)
''','')

compile(s,"case_number_ocr.py","exec")
tree=ast.parse(s)
assert 'self.app_version = "5.2.7"' in s
assert 'root.title("M.F. Case Number OCR — V5.2.7")' in s
assert '"rolls","Refreshing root-folder PDFs…"' in s
assert 'def _refresh_timeout(self,generation):' in s
assert 'text="SAVE REVIEW"' in s
assert 'awaiting SEND ALL SELECTED' in s
assert 'def _schedule_auto_export(self):' in s
assert 'automatic_export_blocked' in s
assert 'learning_pending' in s
assert 'REQUIRED_CASE_YEAR = "08"' in s
assert 'def _apply_sequence_consensus(self):' in s
assert 'def _scan_progress_update(' in s
assert 'def _make_scan_preview(' in s
assert 'text="REVIEW SELECTED"' in s
assert 'os.utime(destination, None)' in s

Path("development/v5.2.7_case_number_ocr.py").write_text(s)
out=io.BytesIO()
with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",s)
    z.writestr("updater_apply.py",updater)
release=out.getvalue()
with zipfile.ZipFile(io.BytesIO(release)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    for n in z.namelist():compile(z.read(n),n,"exec")
digest=hashlib.sha256(release).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(release).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(release)
(ROOT/"SHA256.txt").write_text(digest+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.2.7\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("V5.2.7: manual review persists, explicit SEND ALL SELECTED only, refresh overlay fixed\n")
print("PASS V5.2.7 build",len(release),"SHA256",digest)
