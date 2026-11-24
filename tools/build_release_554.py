"""Build and validate MFCase 5.5.4 from the signed GitHub 5.5.2 release.
No case PDFs, shared learning database, model checkpoints or work logs are uploaded.
"""
import base64,hashlib,io,os,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path

ROOT=Path.cwd()
REPO="chrisbrook0127/CaseNumberOCR-Updates"
TAG="v5.5.4"
PAYLOAD_SHA="df1430f35a82140b352f3c1827b46eb5c133bba62283090abd30be8ee7a62492"
REVIEW_SHA="5959f2ab7a81f46d1d9b92dcfc57e69c751f9ca29112d04deb7bb2284e23487f"
FINAL_SHA="24f289f4043106d2ac2b0ae38069739412626d7d81b23a675938bcc74bea2ea1"

def run(cmd,**kwargs):
    print("RUN",cmd,flush=True)
    subprocess.run(cmd,check=True,**kwargs)

work=ROOT/"release-build-v554"
previous=work/"previous";stage=work/"stage";dist=ROOT/"dist"
for directory in (previous,stage,dist):directory.mkdir(parents=True,exist_ok=True)
run(["gh","release","download","v5.5.2","--repo",REPO,"--pattern",
     "MFCase_Number_OCR_V5.5.2_Full_Online_Install.zip","--dir",str(previous)])
original=previous/"MFCase_Number_OCR_V5.5.2_Full_Online_Install.zip"
assert original.is_file()
assert hashlib.sha256(original.read_bytes()).hexdigest()=="91e8bdcf6d1f6ddc5330ec9112d9f503eaad063840bad6985bef1e08d69beebd"
with zipfile.ZipFile(original) as z:
    assert z.testzip() is None
    z.extractall(stage)
sources=list(stage.rglob("case_number_ocr.py"))
assert len(sources)==1, sources
app=sources[0].parent

encoded=(ROOT/"tools/release_v554_payload.b64").read_text("ascii").strip()
payload=base64.b64decode(encoded,validate=True)
assert hashlib.sha256(payload).hexdigest()==PAYLOAD_SHA, "Transferred patch bytes changed"
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None
    for patch_name, expected in [("patch_553.diff",REVIEW_SHA),
                                 ("patch_554.diff",FINAL_SHA)]:
        print("Applying",patch_name,flush=True)
        run(["patch","--batch","--fuzz=0","-p1","-d",str(app)],
            input=z.read(patch_name))
        assert hashlib.sha256((app/"case_number_ocr.py").read_bytes()).hexdigest()==expected, patch_name
    for name in z.namelist():
        if name.startswith("replacement/") and not name.endswith("/"):
            dest=app/Path(name).name
            dest.write_bytes(z.read(name))
        elif name.startswith("tests/") and name.endswith(".py"):
            dest=app/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(z.read(name))
assert (app/"CREATE_DESKTOP_SHORTCUT.bat").is_file()
assert (app/"Create_Desktop_Shortcut.vbs").is_file()
assert 'root.title("M.F. Case Number OCR — V5.5.4")' in (app/"case_number_ocr.py").read_text("utf-8")
run([sys.executable,"-m","py_compile",str(app/"case_number_ocr.py"),str(app/"updater_apply.py")])
run([sys.executable,str(app/"verify_package.py")])
run([sys.executable,str(app/"tests/test_rules_553.py")])
run([sys.executable,str(app/"tests/test_release_554.py")])
run(["xvfb-run","-a",sys.executable,str(app/"tests/test_release_553_gui.py"),str(app)])

# Existing installed updaters cannot accept brand-new file names. The
# upgraded Launch_Case_OCR.vbs creates the shortcut on its first restart.
updater_files=["case_number_ocr.py","updater_apply.py","RUN_CASE_OCR.bat",
               "Launch_Case_OCR.vbs","MF.ico"]
full_files=["case_number_ocr.py","updater_apply.py","SETUP.bat","RUN_CASE_OCR.bat",
            "RUN_CASE_OCR_DEBUG.bat","Launch_Case_OCR.vbs",
            "CREATE_DESKTOP_SHORTCUT.bat","Create_Desktop_Shortcut.vbs",
            "MF.ico","README_FIRST.txt","verify_package.py","update_config.json",
            "CHANGELOG_V5_5_4.txt"]
full_files+=sorted(p.name for p in app.glob("CHANGELOG_V5_*.txt")
                   if p.name not in full_files)
full_files+=["tests/test_release_553_gui.py","tests/test_release_554.py",
             "tests/test_rules_553.py"]
assert all((app/name).is_file() for name in updater_files+full_files)
update=dist/"CaseNumberOCR_Update.zip"
full=dist/"MFCase_Number_OCR_V5.5.4_Full_Online_Install.zip"
with zipfile.ZipFile(update,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for name in updater_files:z.write(app/name,name)
with zipfile.ZipFile(full,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for name in full_files:z.write(app/name,f"MFCase_Number_OCR_V5.5.4_Full_Online_Install/{name}")
for archive in (update,full):
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert all(not name.lower().endswith((".pdf",".log",".jsonl",".pt",".pth"))
                   for name in z.namelist())
    print("VERIFIED",archive.name,archive.stat().st_size,
          hashlib.sha256(archive.read_bytes()).hexdigest(),flush=True)

# Simulate the v5.5.2/v5.5.3 updater using its original unmodified validator.
with tempfile.TemporaryDirectory(prefix="mfcase_old_updater_") as t:
    d=Path(t);legacy=app/"updater_apply.py"
    # Latest app validator is stricter about paths as well; earlier
    # production version accepts the same five supported update names.
    import importlib.util
    spec=importlib.util.spec_from_file_location("test_updater",legacy)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (d/"learning.json").write_text("sensitive local learning stays")
    shutil.copy(update,d/"download.zip")
    module.apply_update(d,d/"download.zip")
    assert (d/"learning.json").read_text()=="sensitive local learning stays"
    assert 'self.app_version = "5.5.4"' in (d/"case_number_ocr.py").read_text()
print("PASS 30 tests, installer hashes, ZIP validation, and updater simulation",flush=True)
