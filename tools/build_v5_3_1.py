"""MFCase 5.3.1: hardened default-offline behavior and authoritative review entry.

Base: EXACT published 5.3.0 two-file update (SHA-256 pinned).
No automatic online requests, no automatic learning, no automatic PDF transfer.
All stored evidence and diagnostics local, except user's explicitly chosen PDF path.
"""
import ast,base64,hashlib,io,zipfile
from pathlib import Path

root=Path("release-assets")
blob=base64.b64decode((root/"update.base64").read_text(),validate=True)
assert hashlib.sha256(blob).hexdigest()=="d98242136cf249b5fc75a8e8ad6fbbb73b6de6f221cadf3f74497f011387611a"
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    s=z.read("case_number_ocr.py").decode()
    updater=z.read("updater_apply.py")
assert s==Path("development/v5.3.0_case_number_ocr.py").read_text()

def patch(old,new,count=1):
    global s
    assert s.count(old)==count,(s.count(old),count,old[:160])
    s=s.replace(old,new,count)

patch('self.app_version = "5.3.0"','self.app_version = "5.3.1"')
for before,after in (
    ("M.F. Case Number OCR — V5.3.0","M.F. Case Number OCR — V5.3.1"),
    ("Case Number OCR & Renamer — V5.3.0","Case Number OCR & Renamer — V5.3.1"),
    ("Application version: V5.3.0","Application version: V5.3.1"),
    ("MFCase-Connectivity/5.3.0","MFCase-Connectivity/5.3.1")):
    s=s.replace(before,after)

# Explicitly force privacy flags before IMPORTING easyocr, transformers or
# huggingface_hub. Prevent opportunistic cache model checks and telemetry.
patch('''from tkinter import ttk, filedialog, messagebox

try:
    import pymupdf''',
'''from tkinter import ttk, filedialog, messagebox

# Operational default: OFFLINE. No startup model discovery or HF telemetry.
# Only the separately clicked PREPARE OFFLINE MODELS action temporarily
# overrides HF offline flags for public model download; never for PDFs.
os.environ["HF_HUB_OFFLINE"]="1"
os.environ["TRANSFORMERS_OFFLINE"]="1"
os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"]="1"
os.environ["DO_NOT_TRACK"]="1"
os.environ["WANDB_DISABLED"]="true"
os.environ["DISABLE_TELEMETRY"]="1"

try:
    import pymupdf''')

# Explicit model preparation can contact the public HuggingFace models.
# Thread-specific ctx is not available in those libraries, so flags are
# restored in finally and no scan/update/learning may run concurrently.
patch('''    def _prepare_offline_models_worker(self):
        try:
            import easyocr''',
'''    def _prepare_offline_models_worker(self):
        prior_env={k:os.environ.get(k) for k in
                   ("HF_HUB_OFFLINE","TRANSFORMERS_OFFLINE")}
        os.environ["HF_HUB_OFFLINE"]="0"
        os.environ["TRANSFORMERS_OFFLINE"]="0"
        try:
            import easyocr''')
patch('''            self.queue.put(("offline_models_error",str(exc)))

    def _preload_models_worker(self):''',
'''            self.queue.put(("offline_models_error",str(exc)))
        finally:
            for key,previous in prior_env.items():
                if previous is None:
                    os.environ.pop(key,None)
                else:
                    os.environ[key]=previous

    def _preload_models_worker(self):''')

# Explicitly user-initiated model preparation is blocked during ordinary
# scanning, learning, software update and pending export; user may never
# inadvertently initiate model downloads while case records process.
patch('''        if self.busy or self.trocr_training or self.update_busy or self._offline_prepare_busy:
            messagebox.showinfo("Preparing offline models",''',
'''        if (self.busy or self.trocr_training or self.update_busy or
                self.export_busy or self._offline_prepare_busy):
            messagebox.showinfo("Preparing offline models",''')

# A red OCR box is evidence-selection only. NEVER change an entered manual
# case when drawing/clicking OCR regions; formerly it could overwrite the
# confirmation entry when it still matched initial_reading.
patch('''                if not (already_verified or manually_changed):
                    entry.delete(0,"end")
                    entry.insert(0,number)
                candidate_choice_var.set(number)''',
'''                # OCR box clicks select the training crop, not the verified
                # case. Only a literal empty entry may receive an OCR hint.
                if not entry.get().strip() and not manually_changed and not already_verified:
                    entry.insert(0,number)
                candidate_choice_var.set(number)''')
patch('''        def use_selected_candidate(_event=None):
            selected_value = candidate_choice_var.get().strip()
            if selected_value:
                entry.delete(0, "end")
                entry.insert(0, selected_value)''',
'''        def use_selected_candidate(_event=None):
            # Hidden OCR dropdown cannot overwrite a manual correction.
            selected_value=candidate_choice_var.get().strip()
            if selected_value and not entry.get().strip():
                entry.insert(0,selected_value)''')

# Strict defensive save invariant: human confirmation is saved BEFORE
# image-learning work. Existing code already performs this, but verify.
assert s.index('''            r["confirmed"]=value
            r["manual_approved"]=True''') < s.index('''            if not self._save_state():
                err.config(''')
assert 'self._save_learning_example(r,value)' in s

# Make non-network behavior explicit and accurate. A local or approved LAN
# root folder can still communicate via OS SMB; do not describe network
# shares as magically disconnected.
patch('''                  text="PDF recognition runs locally after models are installed. Shared learning uses the restricted network drive. GitHub is used for application updates, not case records.",''',
'''                  text="OFFLINE BY DEFAULT: OCR, local learning and logs use this computer. UPDATE is a separate manual GitHub request; PREPARE OFFLINE MODELS downloads public model files only when clicked. Selecting a network PDF folder still uses that network share.",''')
patch('''             "6. OFFLINE: installed OCR models, learning examples and diagnostic "
             "logs remain on this computer. Once models are installed, "
             "scanning needs no internet or shared-drive access.\\n\\n"''',
'''             "6. OFFLINE: once both OCR models are cached, scans and learning "
             "run without internet. The app makes no automatic update checks. "
             "If you choose PDFs on a network share, Windows still transfers "
             "those PDFs and state over your LAN; choose a local folder for "
             "fully disconnected operation.\\n\\n"''')
patch('''             "8. EXPORT WORK LOG saves a local ZIP when you select a destination. "
             "Never place identifiable case logs in the public GitHub repository. "''',
'''             "8. EXPORT WORK LOG saves a local ZIP when you select a destination. "
             "For maximum confidentiality, keep case workstations disconnected "
             "from external networks and restrict Windows outbound traffic. "
             "Never place identifiable case logs in the public GitHub repository. "''')

# Remove technical potential to send telemetry automatically in normal run.
assert 'root.after(250,self._begin_offline_startup)' in s
assert 'download_enabled=False' in s
assert 'local_files_only=True' in s
assert 'def _diag_sync_shared(self):' in s
assert 'def _schedule_auto_export(self):' in s
assert 'text="SAVE REVIEW"' in s
assert 'text="REVIEW SELECTED"' in s
assert 'text="SEND ALL SELECTED"' in s
assert 'REQUIRED_CASE_YEAR = "08"' in s
assert 'def _refresh_timeout(self,generation):' in s

t=ast.parse(s)
compile(s,"case_number_ocr.py","exec")
Path("development/v5.3.1_case_number_ocr.py").write_text(s)
out=io.BytesIO()
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",s)
    z.writestr("updater_apply.py",updater)
new=out.getvalue()
with zipfile.ZipFile(io.BytesIO(new)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    for p in z.namelist():compile(z.read(p),p,"exec")
digest=hashlib.sha256(new).hexdigest()
(root/"update.base64").write_text(base64.b64encode(new).decode("ascii"))
(root/"CaseNumberOCR_Update.zip").write_bytes(new)
(root/"SHA256.txt").write_text(digest+"\n")
(root/"RELEASE_TAG.txt").write_text("v5.3.1\n")
(root/"PUBLISH_REQUEST.txt").write_text("MFCase v5.3.1 verified privacy-first offline manual-update patch\n")
print("PASS MFCase V5.3.1",len(new),"SHA256",digest)
