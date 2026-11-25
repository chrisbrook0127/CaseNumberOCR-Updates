"""Sanitized MFCase v5.6.7 release builder. Only package approved source files."""
from __future__ import annotations
from pathlib import Path
import hashlib,sys,subprocess,tempfile,shutil,importlib.util
from zipfile import ZipFile, ZIP_DEFLATED

app=Path(sys.argv[1]).resolve()
out=Path(sys.argv[2]).resolve()
out.mkdir(parents=True,exist_ok=True)
assert 'self.app_version = "5.6.7"' in (app/'case_number_ocr.py').read_text(encoding='utf8')
files=['case_number_ocr.py','updater_apply.py','SETUP.bat','RUN_CASE_OCR.bat',
       'RUN_CASE_OCR_DEBUG.bat','Launch_Case_OCR.vbs',
       'CREATE_DESKTOP_SHORTCUT.bat','Create_Desktop_Shortcut.vbs',
       'README_FIRST.txt','verify_package.py','update_config.json','MF.ico',
       'MFCase_Combine_And_Trace.py','CHANGELOG_V5_6_7.txt']
for name in files:
    assert (app/name).is_file(),name
manifest={name:hashlib.sha256((app/name).read_bytes()).hexdigest()
          for name in ('case_number_ocr.py','updater_apply.py','SETUP.bat','README_FIRST.txt')}
(app/'verify_package.py').write_text(
    'from pathlib import Path\nimport hashlib\nROOT=Path(__file__).resolve().parent\nEXPECTED='
    +repr(manifest)+'\nfor name,digest in EXPECTED.items():\n'
    +'    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name\n'
    +"print('PASS MFCase v5.6.7 source integrity')\n",encoding='utf8')
subprocess.run([sys.executable,'-m','py_compile',str(app/'case_number_ocr.py'),str(app/'updater_apply.py')],check=True)
subprocess.run([sys.executable,str(app/'verify_package.py')],check=True)
files+=sorted(p.name for p in app.glob('CHANGELOG_V5_*.txt') if p.name not in files)
files+=sorted(str(p.relative_to(app)) for p in (app/'tests').glob('test_release_*.py'))
update_files=['case_number_ocr.py','updater_apply.py','SETUP.bat','RUN_CASE_OCR.bat',
              'RUN_CASE_OCR_DEBUG.bat','Launch_Case_OCR.vbs',
              'CREATE_DESKTOP_SHORTCUT.bat','Create_Desktop_Shortcut.vbs',
              'README_FIRST.txt','verify_package.py','update_config.json','MF.ico',
              'CHANGELOG_V5_6_7.txt']
files=list(dict.fromkeys(files))
files_full=out/'MFCase_Number_OCR_V5.6.7_Full_Online_Install.zip'
files_up=out/'CaseNumberOCR_Update.zip'
for path,names,prefix in ((files_full,files,'MFCase_Number_OCR_V5.6.7_Full_Online_Install/'),
                          (files_up,update_files,'')):
    with ZipFile(path,'w',ZIP_DEFLATED,compresslevel=9) as z:
        for name in names:z.write(app/name,prefix+name)
    with ZipFile(path) as z:
        assert z.testzip() is None
        assert all(not name.lower().endswith(('.pdf','.log','.jsonl','.pt','.pth','.sqlite'))
                   and '__pycache__' not in name and 'learning' not in name.lower().split('/')[-1]
                   for name in z.namelist())
    print('VALIDATED',path,path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest(),flush=True)
spec=importlib.util.spec_from_file_location('updater_567',app/'updater_apply.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix='mfcase567_') as d:
    root=Path(d)
    for file,datum in [('original_case.pdf','CONFIDENTIAL PDF'),
                       ('.case_ocr_state.json','PRIVATE STATE'),
                       ('learning_examples.json','PRIVATE LEARNING'),
                       ('MFCase_File_Trace.jsonl','PRIVATE TRACE')]:
        (root/file).write_text(datum)
    (root/'models').mkdir();(root/'models'/'checkpoint.pt').write_text('PRIVATE MODEL')
    (root/'case_number_ocr.py').write_text('previous installation')
    with tempfile.TemporaryDirectory() as d2:
        archive=Path(d2)/'update.zip';shutil.copyfile(files_up,archive)
        module.apply_update(root,archive)
    for file,datum in [('original_case.pdf','CONFIDENTIAL PDF'),
                       ('.case_ocr_state.json','PRIVATE STATE'),
                       ('learning_examples.json','PRIVATE LEARNING'),
                       ('MFCase_File_Trace.jsonl','PRIVATE TRACE'),
                       ('models/checkpoint.pt','PRIVATE MODEL')]:
        assert (root/file).read_text()==datum,file
    assert 'self.app_version = "5.6.7"' in (root/'case_number_ocr.py').read_text()
    subprocess.run([sys.executable,str(root/'verify_package.py')],check=True)
print('PASS updater installation and private data preservation',flush=True)