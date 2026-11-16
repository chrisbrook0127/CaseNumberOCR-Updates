"""MFCase V5.2.8 - privacy-first offline operation, manual updates only.

Immutable source: published V5.2.7. Preserve strict 08- cases, OCR rules,
manual review, proof logging, natural-order ranges, and send-only-on-click.
No automatic GitHub, HuggingFace, remote learning, or diagnostic upload.
"""
from pathlib import Path
import ast, base64, hashlib, io, subprocess, zipfile

ROOT=Path("release-assets")
base=base64.b64decode(subprocess.check_output(
    ["git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),
    validate=True)
assert hashlib.sha256(base).hexdigest()=="65f7c168d8d4ffb13a591e28483dc45717f093e2346de6df342ffd5323d7d33a"
with zipfile.ZipFile(io.BytesIO(base)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    source=z.read("case_number_ocr.py").decode()
    updater=z.read("updater_apply.py")
assert source==Path("development/v5.2.7_case_number_ocr.py").read_text()
s=source

def swap(before,after,count=1):
    global s
    actual=s.count(before)
    assert actual==count,("Patch mismatch",actual,count,before[:155])
    s=s.replace(before,after,count)

def method(name,body):
    global s
    t=ast.parse(s)
    hits=[n for n in ast.walk(t) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(hits)==1,(name,len(hits))
    n=hits[0]
    lines=s.splitlines(keepends=True)
    s="".join(lines[:n.lineno-1])+body.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(s,"case_number_ocr.py","exec")

swap('self.app_version = "5.2.7"','self.app_version = "5.2.8"')
for old,new in [
    ("M.F. Case Number OCR — V5.2.7","M.F. Case Number OCR — V5.2.8"),
    ("Case Number OCR & Renamer — V5.2.7","Case Number OCR & Renamer — V5.2.8"),
    ("Application version: V5.2.7","Application version: V5.2.8"),
    ("MFCase-Connectivity/5.2.7","MFCase-Connectivity/5.2.8")]:
    s=s.replace(old,new)

# Disable Hugging Face telemetry and all third-party automatic model checks
# BEFORE OCR packages import. Only the user's UPDATE can access GitHub.
swap('''from contextlib import contextmanager
from pathlib import Path''',
'''from contextlib import contextmanager
# No pretrained model may contact any server while processing evidence.
os.environ["HF_HUB_OFFLINE"]="1"
os.environ["TRANSFORMERS_OFFLINE"]="1"
os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"
os.environ["DO_NOT_TRACK"]="1"
from pathlib import Path''')

# Default to private, user-owned LOCAL learning storage. No UNC access on
# startup, scan, review, training, or diagnostic. Existing private shared
# learning remains untouched and can be imported explicitly by the user.
swap('''        self.learning_dir = None
        self.learning_data =''',
'''        self.learning_dir = (Path(os.environ.get("LOCALAPPDATA") or
                        (Path.home()/".local"/"share"))/"MFCaseOCR"/"learning_private")
        self.learning_dir.mkdir(parents=True,exist_ok=True)
        self.learning_data =''')
swap('''        self._startup_learning_pending = True''',
'''        self._startup_learning_pending = False''',2)
swap('''        # Production starts its maintenance check automatically. The opt-out is
        # only for automated/headless regression tests, not the normal launcher.
        if os.environ.get("MFCASE_SKIP_STARTUP_MAINTENANCE") != "1":
            root.after(400, self._begin_startup_updates)
        else:
            self._startup_ready = True  # explicitly opted out by test harness only''',
'''        # Fully offline from the first instant. No automatic update check,
        # training, learning-share connection or model download.
        self._startup_ready = True
        self._load_learning_data()
        self._refresh_learning_button_state()
        self._refresh_scan_button()
        self.status.config(text="OFFLINE READY — updates are manual. Select a root folder.")''')

# Startup helper methods remain callable from legacy code, but cannot ever
# initiate network access or model training by themselves.
method("_begin_startup_updates",'''
    def _begin_startup_updates(self):
        """Startup is offline. Software updates require explicit UPDATE."""
        return None
''')
method("_startup_learning_check",'''
    def _startup_learning_check(self):
        """Learning is local. Training requires explicit UPDATE LEARNING."""
        return None
''')
method("_start_automatic_learning_if_ready",'''
    def _start_automatic_learning_if_ready(self):
        """Never launch automatic learning or connect to a remote share."""
        return None
''')
# Legacy startup checks may dispatch via queue; ensure they are disabled too.
method("_startup_software_complete",'''
    def _startup_software_complete(self,error=None):
        """No startup updater exists in privacy/offline mode."""
        self._startup_auto_update=False
        return None
''')
method("_retry_startup",'''
    def _retry_startup(self):
        """Offline mode is always ready when installed libraries are present."""
        self._startup_ready=True
        self._refresh_scan_button()
''')
method("_check_connection_status",'''
    def _check_connection_status(self):
        """All checks are local-only. Never probe GitHub or SMB on SETTINGS."""
        label=getattr(self,"connection_status_label",None)
        if label is None:
            return
        share="DISABLED — private local learning"
        cached_trocr=bool(getattr(self,"trocr_model",None) is not None)
        easy=bool(getattr(self,"reader",None) is not None)
        status=("OFFLINE MODE — no automatic network calls. "
                f"EasyOCR: {'LOADED' if easy else 'loads from local cache on SCAN'}; "
                f"TrOCR: {'LOADED' if cached_trocr else 'local cache only'}; "
                f"learning: {share}. GitHub: manual UPDATE only.")
        label.config(text=status)
''')
swap('''        self._show_main_view("select")''',
'''        self._show_main_view("select")''',1)

# Keep the two existing, independent manual buttons. UPDATE LEARNING never
# stays disabled simply because there are not yet enough examples.
method("_refresh_learning_button_state",'''
    def _refresh_learning_button_state(self):
        button=getattr(self,"finish_batch_btn",None)
        if button is None:
            return
        pending=len(self._pending_learning_examples()) if self.learning_dir else 0
        attempted=bool(self.learning_data.get("trocr_v3_attempted_ids",[]))
        needed=5 if attempted else 10
        button.config(
            text=f"UPDATE LEARNING ({pending} ready; min {needed})",
            state=("disabled" if self.busy or self.trocr_training
                   or self.update_busy else "normal"))
''')
swap('''            header, text="UPDATE LEARNING", command=self._finish_import_batch, state="disabled")''',
'''            header, text="UPDATE LEARNING", command=self._finish_import_batch, state="normal")''')

# Training never happens except as a direct action on UPDATE LEARNING,
# even if internal callers try to pass automatic=True.
swap('''    def _maybe_train_trocr(self, automatic=False):
        if self.trocr_training or not self.learning_dir:''',
'''    def _maybe_train_trocr(self, automatic=False):
        if automatic:
            return False
        if self.trocr_training or not self.learning_dir:''')
swap('''        self._learning_auto_run = bool(automatic)''',
'''        self._learning_auto_run = False''')
# A base model may only be loaded from already cached local files, for
# both scanning and manual training. Never allow implicit Hugging Face I/O.
swap('''                processor = TrOCRProcessor.from_pretrained(model_source)
                model = VisionEncoderDecoderModel.from_pretrained(model_source)''',
'''                processor = TrOCRProcessor.from_pretrained(model_source,local_files_only=True)
                model = VisionEncoderDecoderModel.from_pretrained(model_source,local_files_only=True)''')
swap('''                processor = TrOCRProcessor.from_pretrained(base_name)
                base_model = VisionEncoderDecoderModel.from_pretrained(base_name).to(device)''',
'''                processor = TrOCRProcessor.from_pretrained(base_name,local_files_only=True)
                base_model = VisionEncoderDecoderModel.from_pretrained(
                    base_name,local_files_only=True).to(device)''')
# EasyOCR must fail closed if weights absent; NEVER download them on scan.
swap('''easyocr.Reader(["en"], gpu=gpu, verbose=False)''',
'''easyocr.Reader(["en"], gpu=gpu, verbose=False,download_enabled=False)''',1)
swap('''easyocr.Reader(["en"], gpu=False, verbose=False)''',
'''easyocr.Reader(["en"], gpu=False, verbose=False,download_enabled=False)''',2)
swap('''easyocr.Reader(["en"], gpu=reader_gpu, verbose=False)''',
'''easyocr.Reader(["en"], gpu=reader_gpu, verbose=False,download_enabled=False)''')
# Only cached trained TrOCR model may be loaded. Old copied model cache
# remains usable offline, even if its source was originally the SMB share.
method("_local_model_source",'''
    def _local_model_source(self):
        """Select only fully present models from private local disk."""
        private=Path(self.learning_dir) if self.learning_dir else None
        for name in ("trocr_v2_finetuned","trocr_v2_previous"):
            candidate=private/name if private else None
            if candidate and (candidate/"config.json").is_file():
                return candidate
        cache=(Path(os.environ.get("LOCALAPPDATA") or
                    (Path.home()/".cache"))/"MFCaseOCR"/"models")
        if cache.is_dir():
            valid=sorted((p for p in cache.glob("trocr-*")
                if p.is_dir() and (p/".mfcase_complete").is_file()
                and (p/"config.json").is_file()),
                key=lambda p:p.stat().st_mtime,reverse=True)
            if valid:
                self._cached_model_dir=valid[0]
                return valid[0]
        return None
''')
# Cache is optional and local-only; allow scanning with EasyOCR when TrOCR
# is not installed, rather than blocking every PDF on network state.
swap('''        if not self._startup_ready or not self.learning_dir or self.reader is None or self.trocr_model is None:
            self.status.config(text="Scanning is locked until shared learning and all OCR models are ready.")
            return''',
'''        if not self._startup_ready:
            self.status.config(text="Application is not ready.")
            return''')
# Reader was already made offline above.
# Top EasyOCR error presented only after local cache checks, not a download.
swap('''            self.queue.put(("engine_status", f"EasyOCR initialized • {'GPU' if reader_gpu else 'CPU'}"))''',
'''            self.queue.put(("engine_status", f"Offline EasyOCR initialized • {'GPU' if reader_gpu else 'CPU'}"))''')

# No remote log sync, not even to the old restricted SMB share, whether
# training is manual or a scan completed. EXPORT WORK LOG is explicit.
method("_diag_sync_shared",'''
    def _diag_sync_shared(self):
        """Privacy: logs are local and only export on explicit user command."""
        return None
''')
swap('''        if finished:
            self._diag_event("learning_update_finished",
                             percent=percent,stage=stage)
            threading.Thread(target=self._diag_sync_shared,
                             daemon=True).start()''',
'''        if finished:
            self._diag_event("learning_update_finished",
                             percent=percent,stage=stage)''')
swap('''                    threading.Thread(target=self._diag_sync_shared,daemon=True).start()
                    self._log("Scan-run summary: %s",''',
'''                    self._log("Scan-run summary: %s",''')
swap('''        self._diag_sync_shared()
        self._refresh_learning_stats()''',
'''        self._refresh_learning_stats()''')

# A selected scan folder may itself be a UNC path. Keep the job-state JSON
# there (required for recovery), but local-only detailed OCR/log files.
swap('''            logging.basicConfig(
                filename=str(folder / ".case_ocr_log.txt"), level=logging.INFO,''',
'''            local_logs=(Path(os.environ.get("LOCALAPPDATA") or
                       (Path.home()/".local"/"share"))/"MFCaseOCR"/"logs")
            local_logs.mkdir(parents=True,exist_ok=True)
            logging.basicConfig(
                filename=str(local_logs / "case_ocr_log.txt"), level=logging.INFO,''')
swap('''        self.log_path = self.folder / ".case_ocr_log.txt"''',
'''        self.log_path=(Path(os.environ.get("LOCALAPPDATA") or
                     (Path.home()/".local"/"share"))/"MFCaseOCR"/"logs"/"case_ocr_log.txt")''')

# Lock Github access to the existing repo + single signed/checksummed ZIP.
# It is invoked only by the UPDATE button. GitHub receives an HTTP request
# with workstation IP (never any PDF, case number or diagnostic log).
swap('''        repo = str(cfg.get("repository", "")).strip()
        asset_name = str(cfg.get("asset_name", "CaseNumberOCR_Update.zip")).strip()''',
'''        repo = str(cfg.get("repository", "")).strip()
        asset_name = str(cfg.get("asset_name", "CaseNumberOCR_Update.zip")).strip()
        if repo!="chrisbrook0127/CaseNumberOCR-Updates" or asset_name!="CaseNumberOCR_Update.zip":
            raise RuntimeError("Updater restricted to the official MFCase GitHub release.")''')
swap('''    def check_for_updates(self, automatic=False):
        if self.update_busy:
            return
        self._startup_auto_update = bool(automatic)''',
'''    def check_for_updates(self, automatic=False):
        if automatic:
            return  # No automatic calls can access the Internet.
        if self.update_busy:
            return
        self._startup_auto_update = False''')
swap('''            if not payload["download_url"]:
                raise RuntimeError("The GitHub release asset has no download URL.")''',
'''            if (not str(payload["download_url"] or "").startswith(
                "https://github.com/chrisbrook0127/CaseNumberOCR-Updates/releases/download/")):
                raise RuntimeError("Unexpected update asset URL; download denied.")''')
swap('''            expected = str(release.get("digest") or "")
            if expected.startswith("sha256:") and actual_digest.lower() != expected.split(":", 1)[1].lower():''',
'''            expected = str(release.get("digest") or "")
            if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}",expected):
                raise RuntimeError("Release has no verifiable SHA-256; update denied.")
            if actual_digest.lower() != expected.split(":", 1)[1].lower():''')

# Keep all informational text accurate. These are plain text replacements
# because installed variants differ in line wrapping/indentation.
for old,new in [
 ("Full OCR logs remain on the authorized workstation and restricted shared learning drive. They are never automatically uploaded to the public GitHub repository.",
  "PRIVATE MODE: learning, OCR crops and logs remain on this PC. No case data is uploaded to GitHub or the former shared learning drive. If you choose a network PDF folder, regular PDF and state file operations use that selected folder."),
 ("PDF recognition runs locally after models are installed. Shared learning uses the restricted network drive. GitHub is used for application updates, not case records.",
  "OFFLINE/PRIVATE MODE. The UPDATE button alone checks GitHub. UPDATE LEARNING trains local samples on request; no network synchronization."),
 ("7. OFFLINE: after downloading OCR models, PDF recognition runs ",
  "7. OFFLINE: no startup calls to GitHub or model servers. Recognition uses "),
 ("locally. Shared learning and centralized logs require the ",
  "only locally cached EasyOCR and TrOCR model weights. "),
 ("authorized network drive; updates require GitHub internet access. ",
  "Run PREPARE_OFFLINE_MODELS.bat while online before disconnecting if needed. "),
 ("The current startup policy may prevent scanning until shared ",
  "After that, no internet is required to scan. "),
 ("learning and OCR models are available.",
  "Learning examples and diagnostics stay local."),
 ("8. EXPORT WORK LOG produces a diagnostic ZIP for troubleshooting. ",
  "8. UPDATE LEARNING is manual and local. EXPORT WORK LOG is an optional "),
 ("Do not upload sensitive case material to the public GitHub repo. ",
  "diagnostic ZIP that is saved only when requested. Do not upload "),
 ("Use the restricted shared learning drive for coworker diagnostics.",
  "case data or logs to any public online service."),
 ("1. SELECT FILES chooses a folder. The root PDFs and the root/COMPLETED ",
  "1. SELECT FILES shows only PDFs still in the selected root folder. "),
 ("subfolder are displayed together; no other subfolders are scanned.",
  "Files already transferred to COMPLETED are not shown."),
 ("Handwritten or Printed if known, and press SAVE AND SEND. SKIP leaves ",
  "Handwritten or Printed if known, and press SAVE REVIEW. SKIP leaves ")
]:
    assert old in s,("Missing guide text",old[:65])
    s=s.replace(old,new)
# Manual correction should not be replaced if the reviewer typed a value
# and then clicks an unrelated OCR red box. That box teaches the location
# but NEVER overrides a user-edited confirmation without an explicit prompt.
swap('''        entry.insert(0, initial_reading)
        def use_selected_candidate(_event=None):''',
'''        entry.insert(0, initial_reading)
        manual_entry_changed={"value":False}
        def mark_manual_entry(_event=None):
            manual_entry_changed["value"]=True
        entry.bind("<KeyRelease>",mark_manual_entry,add="+")
        entry.bind("<<Paste>>",mark_manual_entry,add="+")
        def use_selected_candidate(_event=None):''')
swap('''            if selected_value:
                entry.delete(0, "end")
                entry.insert(0, selected_value)
        candidate_selector.bind''',
'''            if selected_value and not manual_entry_changed["value"]:
                entry.delete(0, "end")
                entry.insert(0, selected_value)
        candidate_selector.bind''')
swap('''                entry.delete(0, "end")
                entry.insert(0, number)
                candidate_choice_var.set(number)

                if chosen:''',
'''                if not manual_entry_changed["value"]:
                    entry.delete(0, "end")
                    entry.insert(0, number)
                else:
                    selection_status.config(text="OCR location selected. The case number "
                        "you typed is protected and will not be replaced.")
                candidate_choice_var.set(number)

                if chosen:''')
swap('''            r["status"]=f"Manual case confirmed as {value} — awaiting SEND ALL SELECTED"
            self._move_to_completed(iid)''',
'''            r["status"]=f"Manual case confirmed as {value} — awaiting SEND ALL SELECTED"
            r["manual_override_locked"]=True
            self._move_to_completed(iid)''')
swap('''            "scan_selected": bool(rec.get("scan_selected", True)),''',
'''            "scan_selected": bool(rec.get("scan_selected", True)),
            "manual_override_locked": bool(rec.get("manual_override_locked",False)),''')
swap('''                "scan_selected": bool(old.get("scan_selected", True)),''',
'''                "scan_selected": bool(old.get("scan_selected", True)),
                "manual_override_locked": bool(old.get("manual_override_locked",False)),''')
swap('''                    if r.get("manual_approved") and not r.get("auto_labeled"):''',
'''                    if (r.get("manual_override_locked") or
                            (r.get("manual_approved") and not r.get("auto_labeled"))):''')
swap('''        record["manual_approved"] = True
        record["auto_labeled"] = False
        record["sequence_inferred"] = False''',
'''        record["manual_approved"] = True
        record["auto_labeled"] = False
        record["manual_override_locked"] = True
        record["sequence_inferred"] = False''')
# Old confirmed manual approvals remain locked on refresh, even before
# V5.2.8 wrote the explicit lock flag.
swap('''                "manual_override_locked": bool(old.get("manual_override_locked",False)),''',
'''                "manual_override_locked": bool(
                     old.get("manual_override_locked",False) or
                     (old.get("manual_approved") and not old.get("auto_labeled")
                      and old.get("confirmed"))),''')

# Protect manually typed values on EVERY Entry write, including keyboard,
# mouse paste, accessibility tools and programmatic inserts. KeyRelease alone
# is insufficient on Tk for paste/IME events or synthetic/manual edits.
swap('''        entry = ttk.Entry(controls, font=("Segoe UI", 14))
        entry.grid(row=label_row + 1, column=0, sticky="ew", pady=(5, 4))
        entry.insert(0, initial_reading)
        manual_entry_changed={"value":False}
        def mark_manual_entry(_event=None):
            manual_entry_changed["value"]=True
        entry.bind("<KeyRelease>",mark_manual_entry,add="+")
        entry.bind("<<Paste>>",mark_manual_entry,add="+")''',
'''        entry_var=tk.StringVar(value=initial_reading)
        entry = ttk.Entry(controls, font=("Segoe UI", 14),
                          textvariable=entry_var)
        entry.grid(row=label_row + 1, column=0, sticky="ew", pady=(5, 4))
        manual_entry_changed={"value":False,"programmatic":False}
        def mark_manual_entry(*_):
            if not manual_entry_changed["programmatic"]:
                manual_entry_changed["value"]=True
        entry_var.trace_add("write",mark_manual_entry)
        def fill_from_ocr(value):
            if manual_entry_changed["value"]:
                return
            manual_entry_changed["programmatic"]=True
            try:entry_var.set(value)
            finally:manual_entry_changed["programmatic"]=False''')
swap('''            if selected_value and not manual_entry_changed["value"]:
                entry.delete(0, "end")
                entry.insert(0, selected_value)''',
'''            if selected_value:
                fill_from_ocr(selected_value)''')
swap('''                if not manual_entry_changed["value"]:
                    entry.delete(0, "end")
                    entry.insert(0, number)
                else:''',
'''                if not manual_entry_changed["value"]:
                    fill_from_ocr(number)
                else:''')
compile(s,"case_number_ocr.py","exec")
t=ast.parse(s)
assert 'self.app_version = "5.2.8"' in s
assert 'REQUIRED_CASE_YEAR = "08"' in s
assert 'download_enabled=False' in s
assert 'HF_HUB_OFFLINE' in s
assert 'manual_override_locked' in s
assert 'text="REVIEW SELECTED"' in s
assert 'text="SAVE REVIEW"' in s
assert 'def _apply_sequence_consensus(self):' in s
assert 'def _scan_progress_update(' in s
assert 'automatic_export_blocked' in s
assert 'os.utime(destination, None)' in s

Path("development/v5.2.8_case_number_ocr.py").write_text(s)
out=io.BytesIO()
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
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
(ROOT/"RELEASE_TAG.txt").write_text("v5.2.8\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text(
    "V5.2.8: manual-only update and local learning; offline OCR; no automatic diagnostics sharing; protect manual reviews\n")
print("PASS build MFCase V5.2.8",len(release),digest)
