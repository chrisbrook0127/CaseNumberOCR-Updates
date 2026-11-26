"""MFCase v6.0.2: assemble independently from official v6.0.1 + exact approved delta."""
import ast,base64,hashlib,io,json,os,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'stage'
BASE=ROOT/'baseline/MFCase_Number_OCR_V6.0.1_Full_Online_Install.zip'
TRANSFER=ROOT/'tools/transfer_602.b64'
BASE_SHA='7c2ff3983383da6fe335fd32f9eb93bc0b0220a3d070a0ea9d5ea975d6805af3'
TRANSFER_SHA='5188596a3597e3e1fdabed38fe63b532988b5a46f9a2cc8ea5fae7b5842cbe50'
FILES={'case_number_ocr.py','verify_package.py','CHANGELOG_V6_0_2.txt',
       'tests/test_release_5617.py','tests/test_release_568.py',
       'tests/test_release_600.py','tests/test_release_601.py',
       'tests/test_release_602.py'}
UPDATER=['case_number_ocr.py','updater_apply.py','verify_package.py','RUN_CASE_OCR.bat',
         'RUN_CASE_OCR_DEBUG.bat','Launch_Case_OCR.vbs','MF.ico','SETUP.bat','README_FIRST.txt',
         'CREATE_DESKTOP_SHORTCUT.bat','Create_Desktop_Shortcut.vbs','update_config.json',
         'CHANGELOG_V6_0_2.txt']

def sha(data):return hashlib.sha256(data).hexdigest()
def app_dir():
    found=list(STAGE.rglob('case_number_ocr.py'))
    assert len(found)==1,found
    return found[0].parent

def safe_zip(z):
    assert z.testzip() is None
    names=z.namelist()
    assert len(names)==len(set(names))
    assert all(not n.startswith('/') and '\\' not in n and '..' not in Path(n).parts for n in names)

def app_methods(source):
    tree=ast.parse(source)
    source_lines=source.splitlines(keepends=True)
    app=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='App')
    return {n.name:''.join(source_lines[n.lineno-1:n.end_lineno]) for n in app.body
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}

def verify_source(original,updated):
    a=app_methods(original);b=app_methods(updated)
    assert a.keys()==b.keys()
    allowed={'__init__','_sequence_order_anomalies','_manual_order_problem',
             '_sequence_consensus_plan','_flag_context_outliers'}
    changes={name for name in a if a[name]!=b[name]}
    assert changes==allowed,changes
    # In __init__ only the version/title may change.
    assert a['__init__'].replace('6.0.1','6.0.2')==b['__init__']
    orig=ast.parse(original);new=ast.parse(updated)
    orig_non_app=[ast.dump(n,include_attributes=False) for n in orig.body
                  if not (isinstance(n,ast.ClassDef) and n.name=='App')]
    new_non_app=[ast.dump(n,include_attributes=False) for n in new.body
                 if not (isinstance(n,ast.ClassDef) and n.name=='App')]
    assert orig_non_app==new_non_app,'Top-level OCR/learning code changed'
    assert 'self.app_version = "6.0.2"' in updated
    print('PASS: only five authorized class methods changed; all OCR/learning source identical')

def assemble():
    assert sha(BASE.read_bytes())==BASE_SHA,'Published base integrity mismatch'
    STAGE.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(BASE) as z:
        safe_zip(z);z.extractall(STAGE)
    app=app_dir()
    original=(app/'case_number_ocr.py').read_text('utf8')
    assert 'self.app_version = "6.0.1"' in original
    data=base64.b64decode(TRANSFER.read_text('ascii').strip(),validate=True)
    assert sha(data)==TRANSFER_SHA,'Patch delta checksum mismatch'
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        safe_zip(z)
        assert set(z.namelist())==FILES,set(z.namelist())
        updated=z.read('case_number_ocr.py').decode('utf8')
        verify_source(original,updated)
        for name in z.namelist():
            dest=app/name;dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(z.read(name))
    subprocess.run([sys.executable,str(app/'verify_package.py')],check=True)
    subprocess.run([sys.executable,'-m','compileall','-q',str(app)],check=True)
    print('PASS: official v6.0.1 plus SHA256-verified v6.0.2 patch')

def package():
    app=app_dir()
    subprocess.run([sys.executable,str(app/'verify_package.py')],check=True)
    output=ROOT/'dist';output.mkdir(parents=True,exist_ok=True)
    assert all((app/name).is_file() for name in UPDATER)
    installers=[('CaseNumberOCR_Update.zip',UPDATER,''),
       ('MFCase_Number_OCR_V6.0.2_Full_Online_Install.zip',
        [p.relative_to(app).as_posix() for p in app.rglob('*') if p.is_file()
          and '__pycache__' not in p.parts and p.suffix!='.pyc'],
        'MFCase_Number_OCR_V6.0.2_Full_Online_Install/')]
    checksums={}
    for filename,files,prefix in installers:
        path=output/filename
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for name in sorted(set(files)):z.write(app/name,prefix+name)
        with zipfile.ZipFile(path) as z:safe_zip(z)
        checksums[filename]=sha(path.read_bytes())
        print('PASS:',filename,path.stat().st_size,checksums[filename])
    (output/'SHA256SUMS.json').write_text(json.dumps(checksums,indent=2)+'\n',encoding='utf8')
    # Actually invoke v6.0.1 updater on an existing installation with private data.
    import importlib.util
    spec=importlib.util.spec_from_file_location('mfcase_updater',app/'updater_apply.py')
    updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
    with tempfile.TemporaryDirectory() as td:
        existing=Path(td)/'existing';existing.mkdir()
        for name in UPDATER:
            if (app/name).is_file() and name!='CHANGELOG_V6_0_2.txt':
                (existing/name).write_bytes(b'previous version')
        private={
           'Image0001.pdf':b'%PDF-1.4 restricted',
           '.case_ocr_state.json':b'{"files":{"a":{"sequence_locked":true}}}',
           '.case_ocr_state.journal.jsonl':b'local incremental data',
           '.case_ocr_sent_history.json':b'case history',
           '.case_ocr_learning/examples_v3/handwritten/one.png':b'case crop',
           '.case_ocr_learning/trocr_v2_finetuned/config.json':b'model',
           'logs/MFCase_Work_Log.txt':b'private log'}
        for name,data in private.items():
            target=existing/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
        archive=existing/'update.zip'
        shutil.copy2(output/'CaseNumberOCR_Update.zip',archive)
        updater.apply_update(existing,archive)
        assert all((existing/name).read_bytes()==data for name,data in private.items())
        subprocess.run([sys.executable,str(existing/'verify_package.py')],check=True)
        assert 'self.app_version = "6.0.2"' in (existing/'case_number_ocr.py').read_text('utf8')
        with zipfile.ZipFile(existing/'malicious.zip','w') as z:
            z.writestr('case_number_ocr.py','malicious')
            z.writestr('Image0001.pdf','replace private case')
        try:updater.apply_update(existing,existing/'malicious.zip')
        except RuntimeError:pass
        else:raise AssertionError('Update accepted an unexpected PDF')
        assert all((existing/name).read_bytes()==data for name,data in private.items())
    print('PASS: actual updater, rollback protection and sensitive file preservation')
    source=output/'tested_v602_source';source.mkdir(exist_ok=True)
    for name in FILES:
        dest=source/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(app/name,dest)

if __name__=='__main__':
    assert len(sys.argv)==2 and sys.argv[1] in ('assemble','package')
    {'assemble':assemble,'package':package}[sys.argv[1]]()
