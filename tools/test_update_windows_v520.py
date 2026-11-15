"""Exercise the actual published two-file release through updater_apply on Windows."""
import base64,hashlib,io,tempfile,types,zipfile,sys,os
from pathlib import Path
root=Path("release-assets")
raw=base64.b64decode(root.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(raw).hexdigest()==root.joinpath("SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    actual_app=z.read("case_number_ocr.py")
    actual_updater=z.read("updater_apply.py")
source=actual_updater.decode()
module=types.ModuleType("real_updater")
module.__file__="<release-updater>"
exec(compile(source,module.__file__,"exec"),module.__dict__)
with tempfile.TemporaryDirectory(prefix="MFCase Update Windows Test ") as work:
    folder=Path(work)
    app_dir=folder/"M.F. Cases App (spaces)"
    app_dir.mkdir()
    archive=folder/"CaseNumberOCR_Update.zip"
    archive.write_bytes(raw)
    old_app=b'old working app\n'
    old_helper=b'old helper\n'
    (app_dir/"case_number_ocr.py").write_bytes(old_app)
    (app_dir/"updater_apply.py").write_bytes(old_helper)
    (app_dir/".case_ocr_state.json").write_text('{"files":{"Image4831.pdf":{}}}')
    learn=app_dir/"private-learning";learn.mkdir()
    (learn/"example.txt").write_bytes(b"private example")
    completed=app_dir/"COMPLETED";completed.mkdir()
    (completed/"08-29.pdf").write_bytes(b"%PDF-1.4\nsaved PDF\n")
    module.apply_update(app_dir,archive)
    assert (app_dir/"case_number_ocr.py").read_bytes()==actual_app
    assert (app_dir/"updater_apply.py").read_bytes()==actual_updater
    assert (app_dir/".case_ocr_state.json").read_text()=='{"files":{"Image4831.pdf":{}}}'
    assert (learn/"example.txt").read_bytes()==b"private example"
    assert (completed/"08-29.pdf").read_bytes()==b"%PDF-1.4\nsaved PDF\n"
    # Correct version and checksum help distinguish a real update from a
    # stale title label or a silent failed installation.
    assert b'self.app_version = "5.2.0"' in actual_app
    assert b'V5.2.0' in actual_app
    print("PASS real update installed exact 5.2.0 payload and preserved private data")
    # Unexpected archive member must fail before replacing an existing app.
    bad=folder/"bad.zip"
    with zipfile.ZipFile(bad,"w") as z:
        z.writestr("case_number_ocr.py",actual_app)
        z.writestr("updater_apply.py",actual_updater)
        z.writestr("bad/path.py",b"unsafely nested")
    before=(app_dir/"case_number_ocr.py").read_bytes()
    try:module.apply_update(app_dir,bad)
    except Exception:pass
    else:raise AssertionError("Unexpected entry accepted")
    assert (app_dir/"case_number_ocr.py").read_bytes()==before
    print("PASS unexpected nested ZIP entry rejected with app preserved")
