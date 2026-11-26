"""Reproduce MFCase v6.0.1 release from published v6.0.0 + tested delta."""
from __future__ import annotations
import ast, base64, hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'stage'
BASELINE=ROOT/'baseline'/'MFCase_Number_OCR_V6.0.0_Full_Online_Install.zip'
TRANSFER=ROOT/'tools'/'transfer_601.b64'
BASE_SHA='ad926c3668e55ba9c49f51c657cb3a074bf336548980eaf3e7d89ba3c443b6fc'
TRANSFER_SHA='f4034a15c72d3eead835be952f61d2363d79b2666ee6f552250dd74320efff7a'
EXPECTED_TRANSFER={
 'case_number_ocr.py','SETUP.bat','README_FIRST.txt','verify_package.py',
 'CHANGELOG_V6_0_1.txt','tests/test_release_5617.py',
 'tests/test_release_5619.py','tests/test_release_5620.py',
 'tests/test_release_600.py','tests/test_release_601.py'
}
APP_PREFIX='MFCase_Number_OCR_V6.0.0_Full_Online_Install/'
UPDATER_NAMES=[
 'case_number_ocr.py','updater_apply.py','verify_package.py','RUN_CASE_OCR.bat',
 'RUN_CASE_OCR_DEBUG.bat','Launch_Case_OCR.vbs','MF.ico','SETUP.bat',
 'README_FIRST.txt','CREATE_DESKTOP_SHORTCUT.bat','Create_Desktop_Shortcut.vbs',
 'update_config.json','CHANGELOG_V6_0_1.txt'
]

def digest(data:bytes)->str:return hashlib.sha256(data).hexdigest()

def safe_archive(z:ZipFile)->None:
 assert z.testzip() is None
 assert all('..' not in Path(n).parts and not n.startswith('/') and '\\' not in n
            for n in z.namelist()),'unsafe archive paths'
 assert len(z.namelist())==len(set(z.namelist())),'duplicate archive entries'

def app_folder()->Path:
 items=list(STAGE.rglob('case_number_ocr.py'))
 assert len(items)==1,items
 return items[0].parent

def functions(src:str)->dict:
 lines=src.splitlines(keepends=True)
 out={}
 for node in ast.walk(ast.parse(src)):
  if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
   out[node.name]=''.join(lines[node.lineno-1:node.end_lineno])
 return out

def assemble()->None:
 assert BASELINE.is_file(),'official v6.0.0 release missing'
 assert digest(BASELINE.read_bytes())==BASE_SHA,'official v6.0.0 SHA mismatched'
 STAGE.mkdir(exist_ok=True)
 with ZipFile(BASELINE) as z:
  safe_archive(z)
  z.extractall(STAGE)
 app=app_folder()
 before=(app/'case_number_ocr.py').read_text('utf-8')
 assert 'self.app_version = "6.0.0"' in before,'not v6.0.0 baseline'
 raw=base64.b64decode(TRANSFER.read_text('ascii').strip(),validate=True)
 assert digest(raw)==TRANSFER_SHA,'v6.0.1 delta SHA mismatched'
 with ZipFile(__import__('io').BytesIO(raw)) as z:
  safe_archive(z)
  assert set(z.namelist())==EXPECTED_TRANSFER,'not the audited delta manifest'
  for name in z.namelist():
   out=app/name
   out.parent.mkdir(parents=True,exist_ok=True)
   out.write_bytes(z.read(name))
 after=(app/'case_number_ocr.py').read_text('utf8')
 assert 'self.app_version = "6.0.1"' in after
 assert 'self.show_all_files_var.set(True)' in after
 assert 'class _MFTrackedRow' in after and 'class _MFTrackedRows' in after
 assert 'Potential missing final digit — MANUAL REVIEW' not in after
 # Critical invariants: existing learned physical crop locations unchanged.
 orig=functions(before)
 new=functions(after)
 for method in ('_learned_crop_clusters','_learned_crop_rectangles',
                '_ranked_learned_scan_zones','_get_learned_field_regions',
                '_automatic_case_crop_rects_4x4'):
  assert orig.get(method)==new.get(method),f'location change: {method}'
 # Original v6 before/after & send mechanisms must remain recognizable.
 for term in ('_sequence_consensus_plan','_sequence_order_with_sent_history',
              'MFCaseOrderPopup','_save_state','_ocr_pdf','_train_trocr_worker'):
  assert term in after,term
 subprocess.run([sys.executable,str(app/'verify_package.py')],check=True)
 subprocess.run([sys.executable,'-m','compileall','-q',str(app)],check=True)
 print('PASS official baseline, approved delta, unchanged crop-location functions, integrity')
 
def package()->None:
 app=app_folder()
 assert 'self.app_version = "6.0.1"' in (app/'case_number_ocr.py').read_text('utf8')
 subprocess.run([sys.executable,str(app/'verify_package.py')],check=True)
 assert all((app/n).is_file() for n in UPDATER_NAMES)
 all_files=sorted(p for p in app.rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and not p.name.endswith('.pyc'))
 out=ROOT/'dist'
 out.mkdir(exist_ok=True)
 updater=out/'CaseNumberOCR_Update.zip'
 full=out/'MFCase_Number_OCR_V6.0.1_Full_Online_Install.zip'
 with ZipFile(updater,'w',ZIP_DEFLATED,compresslevel=9) as z:
  for name in UPDATER_NAMES:z.write(app/name,name)
 with ZipFile(full,'w',ZIP_DEFLATED,compresslevel=9) as z:
  for file in all_files:z.write(file,'MFCase_Number_OCR_V6.0.1_Full_Online_Install/'+
                                 str(file.relative_to(app)).replace(os.sep,'/'))
 for zip_path in (updater,full):
  with ZipFile(zip_path) as z:safe_archive(z)
  print('PASS packaged',zip_path.name,zip_path.stat().st_size,digest(zip_path.read_bytes()))
 # Apply ACTUAL updater, not a simulation of its copy operation.
 spec=importlib.util.spec_from_file_location('mfcase_updater',app/'updater_apply.py')
 module=importlib.util.module_from_spec(spec)
 assert spec and spec.loader
 spec.loader.exec_module(module)
 with tempfile.TemporaryDirectory() as t:
  existing=Path(t)/'existing'
  existing.mkdir()
  for name in UPDATER_NAMES:
   if (app/name).is_file() and name!='CHANGELOG_V6_0_1.txt':
    (existing/name).write_bytes(b'old app')
  secret={
   'Image0001.pdf':b'%PDF-1.4 private pdf',
   '.case_ocr_state.json':b'private state',
   '.case_ocr_state.journal.jsonl':b'private journal',
   '.case_ocr_sent_history.json':b'private sent history',
   '.case_ocr_learning/examples_v3/handwritten/one.png':b'private handwriting',
   '.case_ocr_learning/trocr_v2_finetuned/config.json':b'private model',
   'logs/MFCase_Work_Log.txt':b'private log'
  }
  for name,data in secret.items():
   dest=existing/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
  arc=existing/'update.zip'
  shutil.copy2(updater,arc)
  module.apply_update(existing,arc)
  assert all((existing/n).read_bytes()==v for n,v in secret.items())
  subprocess.run([sys.executable,str(existing/'verify_package.py')],check=True)
  assert 'self.app_version = "6.0.1"' in (existing/'case_number_ocr.py').read_text()
  with ZipFile(existing/'evil.zip','w') as z:
   z.writestr('case_number_ocr.py',b'malicious')
   z.writestr('private.pdf',b'unsafe')
  try:module.apply_update(existing,existing/'evil.zip')
  except RuntimeError:pass
  else:raise AssertionError('updater accepted forbidden file')
  assert all((existing/n).read_bytes()==v for n,v in secret.items())
 print('PASS updater, confidential file preservation, safe allowlist, recovery fixtures')
 manifest={f.name:digest(f.read_bytes()) for f in (updater,full)}
 (out/'SHA256SUMS.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')

if __name__=='__main__':
 assert len(sys.argv)==2 and sys.argv[1] in {'assemble','package'}
 {'assemble':assemble,'package':package}[sys.argv[1]]()
