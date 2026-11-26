"""Reproducible release and safety check for MFCase v6.0.3 on Windows/Linux."""
from __future__ import annotations
import ast,base64,hashlib,importlib.util,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'dist'
STAGE=ROOT/'stage'
DIGESTS={
  'CaseNumberOCR_Update.zip':'2f2fdec1d19d26720a4e622f632d3678f1c914272403961c2e21fef410d22a76',
  'MFCase_Number_OCR_V6.0.3_Full_Online_Install.zip':'25c59ded15c502a9fdf087a9c933e822c5531bd849b090bfe0b19a53a762e3ca',
}
TRANSFER={
  'CaseNumberOCR_Update.zip':'tools/transfer_v603_updater.b64',
  'MFCase_Number_OCR_V6.0.3_Full_Online_Install.zip':'tools/transfer_v603_installer.b64',
}
BASELINE_SOURCE_SHA='382d0ffc4c53fa2c17ed323c163113a241f7e4b6bc30560c44f9397cd32b4c87'
EXPECTED_DIFF={'__init__','_update_scan_file_count','_scan_preview_cache_file',
               '_refresh_scan_previews','_insert_scan_row','_worker','_pdf_is_blank',
               '_ocr_pdf','_poll','_review_ready_ids','_refresh_review_controls',
               'on_double_click'}

def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def safe(z):
  assert z.testzip() is None
  names=z.namelist()
  assert len(names)==len(set(names))
  assert all(not n.startswith('/') and '..' not in Path(n).parts and '\\' not in n for n in names)

def sources(src):
  result={}
  for node in ast.walk(ast.parse(src)):
    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
      result[node.name]=ast.dump(node,include_attributes=False)
  return result

def prepare():
  ASSETS.mkdir(exist_ok=True)
  for asset,p in TRANSFER.items():
    b=base64.b64decode((ROOT/p).read_text(encoding='ascii').strip(),validate=True)
    assert sha(b)==DIGESTS[asset],asset
    (ASSETS/asset).write_bytes(b)
    with ZipFile(ASSETS/asset) as z:safe(z)
    print('PASS',asset,'bytes',len(b),'sha256',sha(b))
  assert (ROOT/'versions/v6.0.2/case_number_ocr.py').exists()
  baseline=(ROOT/'versions/v6.0.2/case_number_ocr.py').read_bytes()
  assert sha(baseline)==BASELINE_SOURCE_SHA,sha(baseline)
  with ZipFile(ASSETS/'MFCase_Number_OCR_V6.0.3_Full_Online_Install.zip') as z:
    z.extractall(STAGE)
  app=next(STAGE.rglob('case_number_ocr.py')).parent
  assert len(list(STAGE.rglob('case_number_ocr.py')))==1
  current=(app/'case_number_ocr.py').read_text(encoding='utf8')
  original=baseline.decode('utf8')
  assert 'self.app_version = "6.0.3"' in current
  b=sources(original);a=sources(current)
  changed={name for name in b if b[name]!=a.get(name)}
  assert changed==EXPECTED_DIFF,(changed-EXPECTED_DIFF,EXPECTED_DIFF-changed)
  assert set(a)-set(b)=={'_throttle_scan_result_queue'}
  for name in ('_trocr_read_scored_impl','_timed_easyocr_readtext',
               '_scan_one_learned_zone','_automatic_handwritten_4x4',
               '_train_trocr_worker','_ranked_learned_scan_zones',
               '_get_learned_field_regions','_sequence_consensus_plan',
               '_order_review_findings','_save_learning_example'):
    assert b[name]==a[name],name
  with ZipFile(ASSETS/'CaseNumberOCR_Update.zip') as z:
    assert z.read('case_number_ocr.py')==(app/'case_number_ocr.py').read_bytes()
    assert z.read('updater_apply.py')==(app/'updater_apply.py').read_bytes()
    assert z.read('verify_package.py')==(app/'verify_package.py').read_bytes()
  subprocess.run([sys.executable,str(app/'verify_package.py')],check=True)
  subprocess.run([sys.executable,'-m','compileall','-q',str(app)],check=True)
  print('PASS: only performance methods changed; original OCR, TrOCR, learning, crop locations, anchors, order unchanged')


def validate_updater():
  app=next(STAGE.rglob('case_number_ocr.py')).parent
  spec=importlib.util.spec_from_file_location('updated_app',app/'updater_apply.py')
  mod=importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    with ZipFile(ASSETS/'CaseNumberOCR_Update.zip') as z:
      names=[n for n in z.namelist() if not n.endswith('/')]
    # Application files to be replaced; not private data.
    for name in names:
      if name!='CHANGELOG_V6_0_3.txt':(root/name).write_bytes(b'original')
    sensitive={'Image0001.pdf':b'private PDF',
      '.case_ocr_state.json':b'approved state',
      '.case_ocr_state.journal.jsonl':b'recovery journal',
      '.case_ocr_sent_history.json':b'sent history',
      '.case_ocr_learning/learning_examples_v3.json':b'learning data',
      '.case_ocr_learning/trocr_v2_finetuned/config.json':b'handwriting model',
      'logs/MFCase_Work_Log.txt':b'private log'}
    for name,data in sensitive.items():
      f=root/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(data)
    archive=root/'update.zip'
    shutil.copy2(ASSETS/'CaseNumberOCR_Update.zip',archive)
    mod.apply_update(root,archive)
    assert all((root/name).read_bytes()==data for name,data in sensitive.items())
    subprocess.run([sys.executable,str(root/'verify_package.py')],check=True)
    with ZipFile(root/'evil.zip','w') as z:
      z.writestr('case_number_ocr.py',b'invalid replacement')
      z.writestr('private-case.pdf',b'unexpected file')
    try:mod.apply_update(root,root/'evil.zip')
    except RuntimeError:pass
    else:raise AssertionError('Updater accepted unexpected PDF')
    assert all((root/name).read_bytes()==data for name,data in sensitive.items())
    print('PASS: Windows-compatible updater allowlist and confidential-file preservation')


def archive_source():
  app=next(STAGE.rglob('case_number_ocr.py')).parent
  target=ROOT/'versions/v6.0.3'
  target.mkdir(parents=True,exist_ok=True)
  for name in ('case_number_ocr.py','verify_package.py','CHANGELOG_V6_0_3.txt'):
    shutil.copy2(app/name,target/name)
  shutil.copy2(app/'tests/test_release_603.py',target/'test_release_603.py')
  (target/'ASSET_SHA256.json').write_text(json.dumps(DIGESTS,indent=2)+'\n',encoding='utf8')
  print('PASS: source archived into versions/v6.0.3')

if __name__=='__main__':
  assert len(sys.argv)==2
  if sys.argv[1]=='prepare':prepare()
  elif sys.argv[1]=='updater':validate_updater()
  elif sys.argv[1]=='archive':archive_source()
  else:raise SystemExit('expected prepare/updater/archive')