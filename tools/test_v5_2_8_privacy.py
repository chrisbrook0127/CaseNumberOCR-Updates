"""Actual V5.2.8 update-package offline security and local learning checks."""
import ast,base64,hashlib,io,os,re,tempfile,zipfile
from pathlib import Path
from unittest import mock

base=Path("release-assets")
payload=base64.b64decode((base/"update.base64").read_text(),validate=True)
assert hashlib.sha256(payload).hexdigest()==(base/"SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None
    s=z.read("case_number_ocr.py").decode()
tree=ast.parse(s)
methods={node.name:node for node in ast.walk(tree)
         if isinstance(node,ast.FunctionDef)}
assert "REQUIRED_CASE_YEAR" in s and 'self.app_version = "5.2.8"' in s
for name in ["_begin_startup_updates","_startup_learning_check",
             "_start_automatic_learning_if_ready","_diag_sync_shared",
             "_check_connection_status","_maybe_train_trocr",
             "_local_model_source","_finish_import_batch"]:
    assert name in methods,name
    frag=ast.get_source_segment(s,methods[name])
    if name in ("_begin_startup_updates","_startup_learning_check",
                "_start_automatic_learning_if_ready","_diag_sync_shared"):
        assert "return None" in frag
        assert "urlopen" not in frag and "copy2" not in frag and "connect" not in frag.lower().split("return None")[0] if name=="_diag_sync_shared" else True
assert 'os.environ["HF_HUB_OFFLINE"]="1"' in s
assert 'os.environ["TRANSFORMERS_OFFLINE"]="1"' in s
assert 'os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"' in s
assert 'download_enabled=False' in s
assert s.count("download_enabled=False")>=4
assert "local_files_only=True" in ast.get_source_segment(s,methods["_load_trocr"])
assert "local_files_only=True" in ast.get_source_segment(s,methods["_train_trocr_worker"])
assert "not self.learning_dir or self.reader is None or self.trocr_model is None" not in ast.get_source_segment(s,methods["scan"])
for path in ("_begin_startup_updates","_check_connection_status"):
    part=ast.get_source_segment(s,methods[path])
    assert "urlopen(" not in part and "https://" not in part
assert 'if automatic:\n            return  # No automatic calls' in ast.get_source_segment(s,methods["check_for_updates"])
assert "automatic_export_blocked" in ast.get_source_segment(s,methods["export"])
assert "manual_override_locked" in s
print("PASS: only user-triggered UPDATE can request GitHub; no startup or background log uploads")
print("PASS: EasyOCR and TrOCR use offline/cached model weights, telemetry disabled")
print("PASS: reviewed 08- cases remain locked and export remains manual-only")

# Simulate the real application logic without installed OCR weights.
# It must save learning locally and must not write to or even enumerate UNC.
import tkinter as tk
import numpy as np
os.environ["MFCASE_SKIP_STARTUP_MAINTENANCE"]="1"
with tempfile.TemporaryDirectory(prefix="MFCase 5.2.8 data privacy ") as tmp:
    tmpdir=Path(tmp)
    os.environ["LOCALAPPDATA"]=str(tmpdir/"private")
    (tmpdir/"private").mkdir()
    pdf=tmpdir/"Image4915.pdf"
    pdf.write_bytes(b"%PDF-1.4\nnot a real PDF because we only test crop learning\n")
    ns={"__name__":"mfcase_test","__file__":str(tmpdir/"case_number_ocr.py")}
    exec(compile(s,"case_number_ocr.py","exec"),ns)
    root=tk.Tk();root.withdraw()
    with mock.patch("urllib.request.urlopen",side_effect=AssertionError("unexpected network")):
        app=ns["App"](root)
        app.folder=tmpdir
        app._apply_folder_load("08",{},[pdf],False)
        record=app.records["pdf::Image4915.pdf"]
        image=np.full((700,900,3),238,dtype=np.uint8)
        image[22:110,730:865]=65
        record.update(candidate="08-15",score=85.5,training_type="handwritten",
            snippet=(0,image,"08-15",[730,22,865,110],
                     [{"number":"08-15","rect":[730,22,865,110],
                       "score":85.5,"engines":["EasyOCR"]}]))
        app._save_learning_example(record,"08-44")
        assert app.learning_dir.parent==Path(os.environ["LOCALAPPDATA"])/"MFCaseOCR"
        cropped=list(app.learning_dir.rglob("*.png"))
        assert cropped and all(str(p).startswith(str(app.learning_dir)) for p in cropped)
        db=app.learning_dir/"learning_examples_v3.json"
        assert db.exists()
        assert any(ex["confirmed_case_number"]=="08-44"
                   for ex in app.learning_data["examples"])
        assert app._startup_ready and not app._startup_learning_pending
        app._diag_sync_shared()
        assert not (tmpdir/"diagnostics").exists()
        app.check_for_updates(automatic=True)
        assert not app.update_busy
        app._maybe_train_trocr(automatic=True)
        assert not app.trocr_training
    print("PASS: real learning crop stored only in local private directory")
    print("PASS: automatic training/update requests and shared-diagnostic transfer blocked")
    root.destroy()
print("PASS V5.2.8 offline/privacy regression")
