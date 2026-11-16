"""MFCase 5.3.0 security/privacy and targeted OCR update.

Exact immutable V5.2.7 baseline SHA256. Case evidence and OCR are never
transmitted; only explicit user clicks on UPDATE or PREPARE OFFLINE MODELS can
download public program/model assets.
"""
from pathlib import Path
import ast,base64,hashlib,io,subprocess,zipfile

ROOT=Path("release-assets")
original_zip=base64.b64decode(subprocess.check_output(
    ["git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),
    validate=True)
assert hashlib.sha256(original_zip).hexdigest()=="65f7c168d8d4ffb13a591e28483dc45717f093e2346de6df342ffd5323d7d33a"
with zipfile.ZipFile(io.BytesIO(original_zip)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    s=z.read("case_number_ocr.py").decode("utf-8")
    updater=z.read("updater_apply.py")
assert s==Path("development/v5.2.7_case_number_ocr.py").read_text()

def swap(a,b,n=1):
    global s
    count=s.count(a)
    assert count==n,(count,n,a[:175])
    s=s.replace(a,b,n)

def method(name,body):
    global s
    t=ast.parse(s)
    found=[n for n in ast.walk(t) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(found)==1,(name,len(found))
    node=found[0]
    lines=s.splitlines(keepends=True)
    s="".join(lines[:node.lineno-1])+body.strip("\n")+"\n"+"".join(lines[node.end_lineno:])
    compile(s,"case_number_ocr.py","exec")

swap('self.app_version = "5.2.7"','self.app_version = "5.3.0"')
for a,b in [("M.F. Case Number OCR — V5.2.7","M.F. Case Number OCR — V5.3.0"),
            ("Case Number OCR & Renamer — V5.2.7","Case Number OCR & Renamer — V5.3.0"),
            ("Application version: V5.2.7","Application version: V5.3.0"),
            ("MFCase-Connectivity/5.2.7","MFCase-Connectivity/5.3.0")]:
    s=s.replace(a,b)

# All network work is opt-in. In particular never ping GitHub or Hugging Face
# when an officer opens a case folder or starts MFCase. Disable platform
# telemetry features of model-client libraries before they import.
swap('''        self._startup_learning_pending = True''',
'''        self._startup_learning_pending = False
        self._offline_prepare_busy = False''',2)
swap('''        # Production starts its maintenance check automatically. The opt-out is
        # only for automated/headless regression tests, not the normal launcher.
        if os.environ.get("MFCASE_SKIP_STARTUP_MAINTENANCE") != "1":
            root.after(400, self._begin_startup_updates)
        else:
            self._startup_ready = True  # explicitly opted out by test harness only''',
'''        # Production is local-only. Network access is possible only after an
        # explicit click on UPDATE or PREPARE OFFLINE MODELS.
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY","1")
        os.environ.setdefault("DO_NOT_TRACK","1")
        if os.environ.get("MFCASE_SKIP_STARTUP_MAINTENANCE") == "1":
            self._startup_ready = True  # headless test harness
        else:
            root.after(250,self._begin_offline_startup)''')

start_local=r'''
    def _begin_offline_startup(self):
        """Initialize from user-local disks. No HTTP or SMB is accessed."""
        if self._startup_ready or self._loading_jobs:
            return
        try:
            self._startup_learning_pending=False
            directory=Path(os.environ.get("LOCALAPPDATA") or
                           str(Path.home()/".local"/"share"))/"MFCase"/"learning"
            directory.mkdir(parents=True,exist_ok=True)
            self.learning_dir=directory
            self._load_learning_data(refresh_ui=True)
            self._diag_event("offline_startup",
                model_downloads_allowed=False,software_updates_automatic=False,
                learning_updates_automatic=False,diagnostic_sync=False)
            self.status.config(text="Offline startup: loading cached OCR models. "
                 "No internet or shared drive connection is required.")
            self._begin_model_preload()
        except Exception as exc:
            self._fail_startup(str(exc))

'''
swap('    def _begin_startup_updates(self):',start_local+'    def _begin_startup_updates(self):')

# Old startup function remains for backward compatibility but cannot be
# entered from launch. Guard even if an old callback invokes it.
method("_begin_startup_updates",r'''
    def _begin_startup_updates(self):
        """Legacy entry point is offline-only; no background update checks."""
        self._begin_offline_startup()
''')
method("_start_automatic_learning_if_ready",r'''
    def _start_automatic_learning_if_ready(self):
        """Never train or synchronize automatically at application startup."""
        self._startup_learning_pending=False
        return False
''')
# A stale call cannot schedule training from a startup event.
method("_startup_software_complete",r'''
    def _startup_software_complete(self,error=None):
        self._startup_auto_update=False
        self._loader_finish("startup")
        self._begin_offline_startup()
''')

# SETTINGS must not ping GitHub; passive inspection never creates a connection.
method("_check_connection_status",r'''
    def _check_connection_status(self):
        """Report LOCAL state without HTTP, SMB probes or other network I/O."""
        label=getattr(self,"connection_status_label",None)
        if label is None:
            return
        models=bool(self.reader is not None and self.trocr_model is not None)
        learning=bool(self.learning_dir and
                      Path(self.learning_dir).is_dir())
        label.config(text=(
            f"OCR: {'OFFLINE READY' if models else 'LOCAL MODELS NOT READY'}   |   "
            f"Learning: {'LOCAL' if learning else 'NOT READY'}   |   "
            "Internet: NOT CONTACTED   |   "
            "Updates: MANUAL BUTTONS ONLY   |   "
            "Work logs: LOCAL ONLY"))
''')
method("_diag_sync_shared",r'''
    def _diag_sync_shared(self):
        """Never transmit diagnostic logs to SMB or the public internet."""
        return None
''')
# Explicit UPDATE and UPDATE LEARNING remain two independent UI buttons.
swap('''        self.finish_batch_btn.pack(side="right", padx=(8, 0), ipadx=10, ipady=6)''',
'''        self.finish_batch_btn.pack(side="right", padx=(8, 0), ipadx=10, ipady=6)
        self.prepare_offline_btn=ttk.Button(
            header,text="PREPARE OFFLINE MODELS",
            command=self._prepare_offline_models)
        self.prepare_offline_btn.pack(side="right",padx=(8,0),ipadx=8,ipady=6)''')

# The one-time, *explicit*, model provisioning action downloads only public
# OCR weights, never case PDFs or learning. App afterwards works air-gapped.
prepare=r'''
    def _prepare_offline_models(self):
        if self.busy or self.trocr_training or self.update_busy or self._offline_prepare_busy:
            messagebox.showinfo("Preparing offline models",
                "Finish scanning, training or updates before preparing models.")
            return
        if not messagebox.askyesno("Prepare offline models",
            "This action connects to the public model providers to DOWNLOAD "
            "the EasyOCR and TrOCR program models only. It never uploads "
            "PDFs, case numbers, learning images or logs. Model files may "
            "require over 1 GB of disk space. Continue?"):
            return
        self._offline_prepare_busy=True
        self.prepare_offline_btn.config(state="disabled",text="DOWNLOADING MODELS…")
        self.status.config(text="Downloading public OCR models by explicit request; "
                                "no case files are transmitted.")
        threading.Thread(target=self._prepare_offline_models_worker,
            daemon=True,name="MFCase-ExplicitOfflineModelDownload").start()

    def _prepare_offline_models_worker(self):
        try:
            import easyocr
            from transformers import TrOCRProcessor,VisionEncoderDecoderModel
            # Only PUBLIC model asset downloads, never content from PDFs.
            easyocr.Reader(["en"],gpu=False,download_enabled=True,verbose=False)
            name="microsoft/trocr-base-handwritten"
            processor=TrOCRProcessor.from_pretrained(name,local_files_only=False)
            model=VisionEncoderDecoderModel.from_pretrained(name,local_files_only=False)
            folder=(Path(os.environ.get("LOCALAPPDATA") or
                  str(Path.home()/".local"/"share"))/
                  "MFCaseOCR"/"models"/"offline-base")
            folder.mkdir(parents=True,exist_ok=True)
            # Save completely before marking the checkpoint available offline.
            model.save_pretrained(str(folder),safe_serialization=True)
            processor.save_pretrained(str(folder))
            (folder/".mfcase_complete").write_text("offline-base-v1",encoding="ascii")
            self.queue.put(("offline_models_ready",str(folder)))
        except Exception as exc:
            self.queue.put(("offline_models_error",str(exc)))

'''
swap('    def _preload_models_worker(self):',prepare+'    def _preload_models_worker(self):')
# Handle provisioning responses on Tk thread. A failed previous preload
# never prevents retry after the user intentionally downloads models.
swap('''                elif kind == "startup_models_ready":
                    self._complete_startup()''',
'''                elif kind == "offline_models_ready":
                    self._offline_prepare_busy=False
                    self.prepare_offline_btn.config(state="normal",
                                                    text="PREPARE OFFLINE MODELS")
                    self.reader=None
                    self.trocr_model=None
                    self.trocr_processor=None
                    self.trocr_error=""
                    self.status.config(text="Models installed locally. "
                                            "Initializing offline OCR…")
                    self._begin_model_preload()
                elif kind == "offline_models_error":
                    self._offline_prepare_busy=False
                    self.prepare_offline_btn.config(state="normal",
                                                    text="PREPARE OFFLINE MODELS")
                    self.status.config(text="Model preparation failed: "+str(msg[1]))
                    messagebox.showerror("Offline models",
                        "Could not prepare all models; nothing was uploaded. "
                        "Check the connection or available disk space.\\n\\n"+str(msg[1]))
                elif kind == "startup_models_ready":
                    self._complete_startup()''')

# Reuse copied trained model on local drive even when the historical SMB
# share is unavailable. Prior cache remains intact and read-only to scanner.
method("_local_model_source",r'''
    def _local_model_source(self):
        """Use only already-local checkpoints; no network discovery/copy."""
        roots=[]
        if self.learning_dir is not None:
            roots.extend([Path(self.learning_dir)/"trocr_v2_finetuned",
                          Path(self.learning_dir)/"trocr_v2_previous"])
        local_root=(Path(os.environ.get("LOCALAPPDATA") or
                    str(Path.home()/".local"/"share"))/"MFCaseOCR"/"models")
        if local_root.is_dir():
            old=sorted(
                [p for p in local_root.glob("trocr-*")
                 if (p/".mfcase_complete").is_file() and
                    (p/"config.json").is_file()],
                key=lambda p:p.stat().st_mtime,reverse=True)
            roots.extend(old)
            roots.append(local_root/"offline-base")
        for path in roots:
            if (path/"config.json").is_file():
                self._cached_model_dir=path
                return path
        return None
''')
# Every normal model load must be local. Missing checkpoints are a visible
# setup error; never fallback to automatic Hugging Face downloads.
swap('''                processor = TrOCRProcessor.from_pretrained(model_source)
                model = VisionEncoderDecoderModel.from_pretrained(model_source)''',
'''                processor = TrOCRProcessor.from_pretrained(
                    model_source,local_files_only=True)
                model = VisionEncoderDecoderModel.from_pretrained(
                    model_source,local_files_only=True)''')
swap('''                reader = easyocr.Reader(["en"], gpu=gpu, verbose=False)''',
'''                reader = easyocr.Reader(["en"], gpu=gpu, verbose=False,
                                        download_enabled=False)''')
swap('''                reader = easyocr.Reader(["en"], gpu=False, verbose=False)''',
'''                reader = easyocr.Reader(["en"], gpu=False, verbose=False,
                                        download_enabled=False)''',1)
swap('''                    self.reader = easyocr.Reader(["en"], gpu=False, verbose=False)''',
'''                    self.reader = easyocr.Reader(["en"], gpu=False, verbose=False,
                                                  download_enabled=False)''')
swap('''            self.reader = easyocr.Reader(["en"], gpu=reader_gpu, verbose=False)''',
'''            self.reader = easyocr.Reader(
                ["en"],gpu=reader_gpu,verbose=False,download_enabled=False)''',1)
# Training is user-initiated and cannot pull models from the internet.
swap('''                processor = TrOCRProcessor.from_pretrained(base_name)
                base_model = VisionEncoderDecoderModel.from_pretrained(base_name).to(device)''',
'''                local_model=self._local_model_source()
                source=str(local_model) if local_model else base_name
                processor = TrOCRProcessor.from_pretrained(
                    source,local_files_only=True)
                base_model = VisionEncoderDecoderModel.from_pretrained(
                    source,local_files_only=True).to(device)''')

# UI reflects offline readiness without a public-GitHub connectivity probe.
swap('''        self.status.config(text="Ready — shared learning and recognition models loaded. Select a root folder.")''',
'''        self.status.config(text="OFFLINE READY — local OCR/learning loaded. "
                                "Click UPDATE or UPDATE LEARNING manually when desired.")''')
swap('''        self.status.config(text="Not ready — " + reason)''',
'''        self.status.config(text="OFFLINE MODELS NOT READY — "+reason+
                            ". Click PREPARE OFFLINE MODELS once while online.")''')

# Refresh and review stay available for human edits until explicit send.
assert 'text="SAVE REVIEW"' in s and 'def _schedule_auto_export(self):' in s
assert 'automatic_export_blocked' in s and 'def _send_all_selected(self):' in s

# Strict prefix first, then all prior 95%, label and range rules. Do not turn
# other years or numbers without 08- into case candidates.
swap('''        year_prefix = REQUIRED_CASE_YEAR  # OCR recognizes only explicit 08- case numbers.''',
'''        year_prefix = REQUIRED_CASE_YEAR  # HARD GATE: no non-08 case enters OCR candidates.''')
swap('''        def add_candidate(number, engine, score, rect, label_kind=None, label_distance=float("inf")):
            entry = candidates.setdefault(number, {''',
'''        def add_candidate(number, engine, score, rect, label_kind=None, label_distance=float("inf")):
            number=re.sub(r"\\s+","",str(number or ""))
            if not CASE_RE.fullmatch(number) or not number.startswith("08-"):
                return
            entry = candidates.setdefault(number, {''')

# Faster: when EasyOCR has directly found a strong strict '08-...' box,
# don't run a redundant TrOCR generation and contrast-enhanced pass on THAT
# same box; retain OCR conflict checking for OTHER physical boxes and labels.
swap('''            if run_trocr and likely_case_line and not fax_pagination:''',
'''            if (run_trocr and likely_case_line and not fax_pagination
                    and not (strict_matches and float(confidence)>=0.95)):''')
swap('''            for _bbox, raw_text, _raw_score, (bx0, by0, bx1, by1) in parsed:
                if ("/" in str(raw_text) and''',
'''            for _bbox, raw_text, _raw_score, (bx0, by0, bx1, by1) in parsed:
                # No duplicate enhanced recognition for a strongly recognized
                # exact 08- candidate. Keep enhancement for uncertain reads.
                if (float(_raw_score)>=0.95 and
                    case_pattern.search(re.sub(r"\\s*-\\s*","-",str(raw_text)))):
                    continue
                if ("/" in str(raw_text) and''')

# Use 3x3 FIRST; if and only if no candidate with the literal '08-' survives,
# run a 5x5 pass. Never upgrade a non-08 OCR interpretation.
swap('''    def _ocr_pdf(self,path):
        """OCR ONLY the page's top-right 3 x 3 inches, at 2.2x raster.''',
'''    def _ocr_pdf(self,path,_search_inches=3):
        """Search 3x3 first; retry top-right 5x5 only if no 08- case found.''')
swap('''            dimension_px=int(round(3*72*2.2))''',
'''            dimension_px=int(round(_search_inches*72*2.2))''')
swap('''            if not results:
                index=max(0,len(pages)-1)''',
'''            if not results:
                if _search_inches==3:
                    self._diag_event("ocr_second_pass",
                        reason="no_08_candidate_in_3x3",search_inches=5)
                    return self._ocr_pdf(path,_search_inches=5)
                index=max(0,len(pages)-1)''')
swap('''             hits=self._diag_field_filter(hits,kind)''',
'''             hits=self._diag_field_filter(hits,kind)''',0) if False else None
swap('''             hits=self._prefer_case_margin(hits,kind,image)''',
'''             hits=self._prefer_case_margin(hits,kind,image)''',0) if False else None
swap('''            if not hits:
                self._diag_event("ocr_decision",page=index+1,source=kind,''',
'''            if not hits:
                if _search_inches==3:
                    self._diag_event("ocr_second_pass",
                        reason="all_3x3_candidates_fail_required_08_prefix",
                        search_inches=5)
                    return self._ocr_pdf(path,_search_inches=5)
                self._diag_event("ocr_decision",page=index+1,source=kind,''')
swap('''            if ambiguous:
                note=("3x3 case field has conflicting OCR reads: "''',
'''            if ambiguous:
                note=(f"{_search_inches}x{_search_inches} case field has conflicting OCR reads: "''')
swap('''            if len(doc)>1:
                note+=f" • page {index+1} of {len(doc)}"''',
'''            if _search_inches==5:
                note=note.replace("3x3","5x5")
                note+=" • second-pass 5x5 after no 08- found in 3x3"
            if len(doc)>1:
                note+=f" • page {index+1} of {len(doc)}"''')
swap('''                    else "fallback" if note.startswith("Upper-right")''',
'''                    else "fallback" if note.startswith("Upper-right")''',0) if False else None

# Labeled 5x5 first- or second-pass candidates are subject to the same true
# 08- prefix and confidence gates; unlabeled reads stay reviewable.
# Do not alter before/after 08- matching or manual override behavior.

# Ensure user-facing instructions no longer falsely claim startup downloading
# and the displayed update/learning choices reflect explicit-only actions.
s=s.replace(
    "The program checks for an update automatically and will restart ",
    "Only UPDATE checks for a software update. No automatic network checks run. ")
s=s.replace(
    "Shared learning is checked next. ",
    "Local learning is loaded from your workstation. ")
s=s.replace(
    "Automatic training uses reduced CPU and finishes before scanning, ",
    "Training begins only when you press UPDATE LEARNING, ")
s=s.replace(
    "Shared learning and centralized logs require the authorized network drive; updates require GitHub internet access. ",
    "Learning, diagnostics and OCR remain local; updates require an explicit GitHub request. ")

compile(s,"case_number_ocr.py","exec")
tree=ast.parse(s)
assert 'self.app_version = "5.3.0"' in s
assert 'root.title("M.F. Case Number OCR — V5.3.0")' in s
assert 'REQUIRED_CASE_YEAR = "08"' in s
assert 'def _ocr_pdf(self,path,_search_inches=3):' in s
assert 'return self._ocr_pdf(path,_search_inches=5)' in s
assert 'download_enabled=False' in s
assert 'def _diag_sync_shared(self):' in s
assert 'text="PREPARE OFFLINE MODELS"' in s
assert 'def _prepare_offline_models_worker(self):' in s
assert 'def _begin_offline_startup(self):' in s
assert 'text="SAVE REVIEW"' in s
assert 'automatic_export_blocked' in s
assert 'os.utime(destination, None)' in s
assert 'def _apply_sequence_consensus(self):' in s

Path("development/v5.3.0_case_number_ocr.py").write_text(s)
out=io.BytesIO()
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",s)
    z.writestr("updater_apply.py",updater)
blob=out.getvalue()
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    for p in z.namelist():compile(z.read(p),p,"exec")
hashvalue=hashlib.sha256(blob).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(blob).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(blob)
(ROOT/"SHA256.txt").write_text(hashvalue+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.3.0\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("V5.3.0: offline-only default; strict 08-; 3x3-to-5x5 retry; explicit-only updates and model setup; manual save preserved\n")
print("PASS BUILD",len(blob),hashvalue)
