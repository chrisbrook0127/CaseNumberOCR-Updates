"""Reproducible MFCase v6.0.4 test build from verified public v6.0.3 release.

Only program files are packaged. Existing machine-local PDF and learning data
are never read or sent to GitHub by this release pipeline.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
BASELINE=ROOT/'baseline'/'MFCase_Number_OCR_V6.0.3_Full_Online_Install.zip'
PATCH=ROOT/'tools'/'patch_v604_test.diff'
BASE_SHA256='25c59ded15c502a9fdf087a9c933e822c5531bd849b090bfe0b19a53a762e3ca'
BASE_FOLDER='MFCase_Number_OCR_V6.0.3_Full_Online_Install'
OUTPUT_FOLDER='MFCase_Number_OCR_V6.0.4_TEST_Full_Online_Install'
CHANGELOG='CHANGELOG_V6_0_4.txt'
REQUIRED_OLD=['case_number_ocr.py','updater_apply.py','SETUP.bat','tests/test_release_603.py']
EXPECTED_PATCHED=['case_number_ocr.py','SETUP.bat','tests/test_release_5617.py',
                  'tests/test_release_603.py','tests/test_release_604.py']
UPDATER_FILES=['case_number_ocr.py','updater_apply.py','verify_package.py',
   'SETUP.bat','RUN_CASE_OCR.bat','RUN_CASE_OCR_DEBUG.bat',
   'Launch_Case_OCR.vbs','MF.ico','CREATE_DESKTOP_SHORTCUT.bat',
   'Create_Desktop_Shortcut.vbs','README_FIRST.txt','update_config.json',CHANGELOG]

def sha256(blob):return hashlib.sha256(blob).hexdigest()

def check_zip(z):
    assert z.testzip() is None,'Corrupted file in ZIP'
    names=z.namelist()
    assert len(names)==len(set(names)),'Duplicate ZIP entries'
    assert all(name and not name.startswith('/') and '\\' not in name and
               '..' not in Path(name).parts for name in names),'Unsafe ZIP path'

def app_dir():
    p=(ROOT/'stage_path.txt')
    if not p.is_file():raise RuntimeError('Build/assemble must run first')
    result=Path(p.read_text(encoding='utf8').strip())
    assert (result/'case_number_ocr.py').is_file(),result
    return result

def functions(source):
    lines=source.splitlines(keepends=True)
    out={}
    for f in ast.walk(ast.parse(source)):
        if isinstance(f,(ast.FunctionDef,ast.AsyncFunctionDef)):
            out[f.name]=''.join(lines[f.lineno-1:f.end_lineno])
    return out

def assemble():
    assert BASELINE.is_file(),'Missing verified public v6.0.3 baseline release'
    assert sha256(BASELINE.read_bytes())==BASE_SHA256,'Unexpected baseline ZIP SHA256'
    work=Path(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir())/'mfcase_v604_test_stage'
    if work.exists():shutil.rmtree(work)
    work.mkdir(parents=True)
    with ZipFile(BASELINE) as z:
        check_zip(z)
        z.extractall(work)
    app=work/BASE_FOLDER
    assert all((app/n).is_file() for n in REQUIRED_OLD)
    before=(app/'case_number_ocr.py').read_text('utf8')
    assert 'self.app_version = "6.0.3"' in before
    assert PATCH.is_file()
    subprocess.run(['git','apply','--check',str(PATCH)],cwd=str(app),check=True)
    subprocess.run(['git','apply',str(PATCH)],cwd=str(app),check=True)
    after=(app/'case_number_ocr.py').read_text('utf8')
    assert 'self.app_version = "6.0.4"' in after
    assert all((app/n).is_file() for n in EXPECTED_PATCHED)
    assert 'MAX_PAGES' not in after or 'MAX_PAGES' in before  # No changed page limits
    protected=(
        '_automatic_handwritten_4x4',
        '_scan_one_learned_zone',
        '_ranked_learned_scan_zones',
        '_get_learned_field_regions',
        '_learned_crop_rectangles',
        '_sequence_consensus_plan',
        '_sequence_order_anomalies',
        '_automatic_case_crop_rects_4x4',
        '_auto_label_block_reason',
        '_save_learning_example',
        '_train_trocr_worker',  # intentionally adjusted only thread restore
    )
    old=functions(before);new=functions(after)
    for name in protected:
        if name=='_train_trocr_worker':
            assert name in old and name in new
            assert 'prior_torch_threads' in new[name]
            assert 'torch.set_num_threads(prior_torch_threads)' in new[name]
            assert 'self._log("Restored OCR CPU threads' in new[name]
            continue
        assert name in old and old[name]==new.get(name),f'Unexpected change in {name}'
    assert 'num_beams=2' in after and 'max_new_tokens=24' in after
    assert 'torch.cuda.is_available()' in after
    assert 'if /I "%~1"=="/GPU" goto gpu_setup' in (app/'SETUP.bat').read_text('utf8')
    assert 'self._easyocr_gpu_batch_test_requested' in after
    assert 'OUT-OF-ORDER CASE NUMBER — SCANNING CONTINUES' in after
    assert 'grab_set' not in new['_confirm_out_of_order_change']
    assert 'wait_window' not in new['_confirm_out_of_order_change']
    (ROOT/'stage_path.txt').write_text(str(app),encoding='utf8')
    subprocess.run([sys.executable,'-m','compileall','-q',str(app)],check=True)
    print('PASS v6.0.3 hash, approved patch, OCR/learning/location invariants')

def package():
    app=app_dir()
    expected={n:sha256((app/n).read_bytes()) for n in
              ('case_number_ocr.py','updater_apply.py','SETUP.bat')}
    (app/'verify_package.py').write_text(
        "from pathlib import Path\nimport hashlib\nROOT=Path(__file__).resolve().parent\nEXPECTED="+repr(expected)+
        "\nfor n,expected in EXPECTED.items():\n    assert hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==expected,n\nprint('PASS MFCase v6.0.4 TEST package integrity')\n",
        encoding='utf8')
    (app/CHANGELOG).write_text(
        """MFCASE v6.0.4 TEST / RELEASE CANDIDATE — NOT THE STABLE RELEASE
        Based on published v6.0.3. Stable v6.0.3 remains unchanged.
        - Restore PyTorch CPU thread limit after automatic training even on STOP or failure.
        - Log the installed PyTorch CUDA capabilities and actual EasyOCR device.
        - Separate EasyOCR detector and recognizer, and TrOCR preprocessing/generation/decoding timings.
        - Optional Settings > GPU SETUP: only after explicit confirmation, uses official PyTorch CUDA 12.6 wheels.
        - Optional Settings > TEST GPU OCR BATCHING: compares 1,2,4,8 on the first two OCR images, always retaining batch-1 outputs; this test will temporarily slow those scans.
        - Active-scan out-of-order YES/NO confirmations are modeless. Scanning and the bounded worker queue continue; send and final before/after review remain deferred until safe.
        - Existing five-row review popup remains modeless; scan is never stopped by it.
        - No change to TrOCR beams, tokens, OCR engine weights, confidence threshold, 4x4 placement, scan retry policy, learning rules, or before/after sequence logic.
        - No automatic third-party uploads or GPU downloads. New software downloads require user consent.
        - For CUDA setup, close MFCase when prompted, then confirm in the setup console. CPU fallback remains available.
        - TEST ONLY: do not replace your rollback v6.0.3 ZIP until actual PDFs verify accuracy.
        """.replace('        ','').lstrip(),encoding='utf8')
    subprocess.run([sys.executable,str(app/'verify_package.py')],check=True)
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    for name in UPDATER_FILES:assert (app/name).is_file(),name
    updater=out/'CaseNumberOCR_Update.zip'
    full=out/'MFCase_Number_OCR_V6.0.4_TEST_Full_Online_Install.zip'
    with ZipFile(updater,'w',ZIP_DEFLATED,compresslevel=9) as z:
        for name in UPDATER_FILES:z.write(app/name,name)
    with ZipFile(full,'w',ZIP_DEFLATED,compresslevel=9) as z:
        for f in sorted(app.rglob('*')):
            if not f.is_file() or '__pycache__' in f.parts or f.suffix in ('.pyc',):continue
            z.write(f,OUTPUT_FOLDER+'/'+f.relative_to(app).as_posix())
    for archive in (updater,full):
        with ZipFile(archive) as z:check_zip(z)
        print('PASS',archive.name,archive.stat().st_size,sha256(archive.read_bytes()))
    # Real updater smoke test, including critical private files.
    spec=importlib.util.spec_from_file_location('mfcase_updater',app/'updater_apply.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory(prefix='mfcase_v604_update_test_') as temp:
        install=Path(temp)
        for name in UPDATER_FILES:
            if name==CHANGELOG:continue
            (install/name).write_bytes((app/name).read_bytes())
        # Pretend the program was v6.0.3 when updater launches.
        (install/'case_number_ocr.py').write_text('self.app_version = "6.0.3"\n',encoding='utf8')
        private={
            'Image0001.pdf':b'%PDF-1.4 private document',
            '.case_ocr_state.json':b'private user approvals',
            '.case_ocr_state.journal.jsonl':b'private recovery journal',
            '.case_ocr_sent_history.json':b'private sent history',
            'logs/MFCase_Work_Log.txt':b'private activity log',
            'learning_examples_v3.json':b'private training examples',
            '.case_ocr_learning/trocr_v2_finetuned/config.json':b'private model',
        }
        for name,data in private.items():
            f=install/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(data)
        test_zip=install/'update.zip'
        shutil.copy2(updater,test_zip)
        mod.apply_update(install,test_zip)
        assert all((install/name).read_bytes()==data for name,data in private.items())
        assert 'self.app_version = "6.0.4"' in (install/'case_number_ocr.py').read_text(encoding='utf8')
        subprocess.run([sys.executable,str(install/'verify_package.py')],check=True)
        forbidden=install/'forbidden.zip'
        with ZipFile(forbidden,'w') as z:
            z.writestr('case_number_ocr.py','self.app_version="6.0.4"')
            z.writestr('Image0001.pdf','not allowed')
        try:mod.apply_update(install,forbidden)
        except RuntimeError:pass
        else:raise AssertionError('Updater accepted unauthorized file')
        assert all((install/name).read_bytes()==data for name,data in private.items())
    print('PASS updater and all private-file/learning-state preservation checks')
    (out/'SHA256SUMS.json').write_text(json.dumps({f.name:sha256(f.read_bytes())
                                  for f in (updater,full)},indent=2)+'\n',encoding='utf8')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('step',choices=('assemble','package','all'))
    args=parser.parse_args()
    if args.step in ('assemble','all'):assemble()
    if args.step in ('package','all'):package()