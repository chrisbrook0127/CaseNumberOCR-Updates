"""Package v5.5.5 from a patched v5.5.4 source tree; never include operational data."""
from pathlib import Path
import hashlib,zipfile,sys,subprocess,tempfile,shutil
app=Path(sys.argv[1]).resolve()
out=Path(sys.argv[2]).resolve()
out.mkdir(parents=True,exist_ok=True)
manifest_names=['case_number_ocr.py','updater_apply.py','SETUP.bat','RUN_CASE_OCR.bat','Launch_Case_OCR.vbs','CREATE_DESKTOP_SHORTCUT.bat','Create_Desktop_Shortcut.vbs']
manifest={name:hashlib.sha256((app/name).read_bytes()).hexdigest() for name in manifest_names}
(app/'verify_package.py').write_text(
    'from pathlib import Path\nimport hashlib\nROOT=Path(__file__).resolve().parent\nEXPECTED='+repr(manifest)+'\n'
    'for name,expected in EXPECTED.items():\n'
    '    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,name\n'
    'print("MFCase v5.5.5 package integrity: PASS")\n',encoding='utf-8')
subprocess.run([sys.executable,str(app/'verify_package.py')],check=True)
full=out/'MFCase_Number_OCR_V5.5.5_Full_Online_Install.zip'
update=out/'CaseNumberOCR_Update.zip'
base=['case_number_ocr.py','updater_apply.py','SETUP.bat','RUN_CASE_OCR.bat',
      'RUN_CASE_OCR_DEBUG.bat','Launch_Case_OCR.vbs','MF.ico','CREATE_DESKTOP_SHORTCUT.bat',
      'Create_Desktop_Shortcut.vbs','README_FIRST.txt','verify_package.py','update_config.json','CHANGELOG_V5_5_5.txt']
updater=['case_number_ocr.py','updater_apply.py','SETUP.bat','RUN_CASE_OCR.bat',
      'RUN_CASE_OCR_DEBUG.bat','Launch_Case_OCR.vbs','MF.ico','CREATE_DESKTOP_SHORTCUT.bat',
      'Create_Desktop_Shortcut.vbs','README_FIRST.txt','verify_package.py','update_config.json','CHANGELOG_V5_5_5.txt']
full_files=base+sorted(x.name for x in app.glob('CHANGELOG_V5_*.txt') if x.name not in base)+[
  'tests/test_rules_553.py','tests/test_release_553_gui.py','tests/test_release_554.py','tests/test_release_555.py']
for filename,files,prefix in [(full,full_files,'MFCase_Number_OCR_V5.5.5_Full_Online_Install/'),
                              (update,updater,'')]:
    assert all((app/name).is_file() for name in files),[x for x in files if not (app/x).is_file()]
    with zipfile.ZipFile(filename,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in files:z.write(app/name,prefix+name)
    with zipfile.ZipFile(filename) as z:
        assert z.testzip() is None
        for item in z.namelist():
            assert '..' not in Path(item).parts
            assert not item.lower().endswith(('.pdf','.png','.log','.jsonl','.pt','.pth'))
    print(filename.name,'bytes',filename.stat().st_size,'sha256',hashlib.sha256(filename.read_bytes()).hexdigest(),flush=True)
# Reproduce field-office updater with private data, but never put it in ZIP.
import importlib.util
spec=importlib.util.spec_from_file_location('updater_sim',str(app/'updater_apply.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix='mfcase_update_555_') as tmp:
    folder=Path(tmp)
    for name in ('case_number_ocr.py','updater_apply.py'):
        (folder/name).write_text('previous application source',encoding='utf-8')
    (folder/'.case_ocr_state.json').write_text('{"private":"case records"}')
    (folder/'learning_examples_v3.json').write_text('{"private":"training"}')
    (folder/'confidential.pdf').write_bytes(b'%PDF-1.4 preserved')
    (folder/'model').mkdir();(folder/'model'/'checkpoint.pt').write_bytes(b'private model')
    archive=folder/'update.zip';shutil.copy2(update,archive)
    module.apply_update(folder,archive)
    assert (folder/'.case_ocr_state.json').read_text()=='{"private":"case records"}'
    assert (folder/'learning_examples_v3.json').read_text()=='{"private":"training"}'
    assert (folder/'confidential.pdf').read_bytes()==b'%PDF-1.4 preserved'
    assert (folder/'model'/'checkpoint.pt').read_bytes()==b'private model'
    assert 'self.app_version = "5.5.5"' in (folder/'case_number_ocr.py').read_text()
    subprocess.run([sys.executable,str(folder/'verify_package.py')],check=True)
print('PASS safe updater preserves PDF, state, learning, models, installer manifest')