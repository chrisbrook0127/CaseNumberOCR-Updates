"""MFCase safe in-place updater: validate, backup, replace, rollback, restart."""
from __future__ import annotations
import argparse
import ctypes
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import time
import zipfile
from pathlib import Path

ALLOWED = {
    'case_number_ocr.py', 'updater_apply.py', 'RUN_CASE_OCR.bat',
    'Launch_Case_OCR.vbs', 'MF.ico', 'README.txt', 'update_config.json',
    'CHANGELOG_V5_0_2.txt',
    'CHANGELOG_V5_0_3.txt',
    'CHANGELOG_V5_0_4.txt',
}
REQUIRED = {'case_number_ocr.py', 'updater_apply.py'}


def wait_for_pid(pid: int):
    if os.name == 'nt':
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            return
    while True:
        try:
            os.kill(pid, 0)
            time.sleep(0.5)
        except OSError:
            return


def validate_update(archive_path, staging):
    with zipfile.ZipFile(archive_path) as z:
        corrupt = z.testzip()
        if corrupt:
            raise RuntimeError(f'Corrupted update member: {corrupt}')
        files = [i for i in z.infolist() if not i.is_dir()]
        names = [i.filename for i in files]
        if len(names) != len(set(names)) or not REQUIRED.issubset(names):
            raise RuntimeError('Update archive has duplicate names or missing required files')
        for info in files:
            path = Path(info.filename)
            if (len(path.parts) != 1 or path.name != info.filename or
                    (path.name not in ALLOWED and not re.fullmatch(
                        r"CHANGELOG_V[0-9]+(?:_[0-9]+){1,3}\.txt",path.name))
                    or info.file_size > 20_000_000):
                raise RuntimeError(f'Unsafe or unexpected update file: {info.filename}')
            (staging / info.filename).write_bytes(z.read(info))
    for name in ('case_number_ocr.py', 'updater_apply.py'):
        py_compile.compile(str(staging / name), doraise=True)
    source = (staging / 'case_number_ocr.py').read_text(encoding='utf-8')
    if not re.search(r'^\s*self\.app_version = "[0-9]+\.[0-9]+\.[0-9]+"\s*$', source, re.MULTILINE):
        raise RuntimeError('Unexpected application version in update archive')
    return names


def apply_update(app_dir, archive_path):
    with tempfile.TemporaryDirectory(prefix='mfcase_update_') as work:
        workdir = Path(work)
        stage = workdir / 'stage'
        backup = workdir / 'backup'
        stage.mkdir()
        backup.mkdir()
        names = validate_update(archive_path, stage)
        original = {}
        for name in names:
            target = app_dir / name
            original[name] = target.exists()
            if target.is_file():
                shutil.copy2(target, backup / name)
        changed = []
        try:
            for name in names:
                target = app_dir / name
                temp = app_dir / (name + '.update.tmp')
                shutil.copy2(stage / name, temp)
                os.replace(temp, target)
                changed.append(name)
        except Exception:
            for name in reversed(changed):
                target = app_dir / name
                if original[name]:
                    os.replace(backup / name, target)
                else:
                    target.unlink(missing_ok=True)
            raise
    try:
        archive_path.unlink(missing_ok=True)
    except OSError:
        pass


def restart(app_dir):
    launcher = app_dir / 'Launch_Case_OCR.vbs'
    bat = app_dir / 'RUN_CASE_OCR.bat'
    if os.name == 'nt' and launcher.is_file():
        subprocess.Popen([str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32' / 'wscript.exe'), str(launcher)], cwd=str(app_dir), creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
    elif os.name == 'nt' and bat.is_file():
        subprocess.Popen(['cmd.exe', '/c', str(bat)], cwd=str(app_dir), creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
    else:
        subprocess.Popen([sys.executable, str(app_dir / 'case_number_ocr.py')], cwd=str(app_dir))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--app-dir', required=True)
    parser.add_argument('--zip', required=True)
    args = parser.parse_args()
    app_dir = Path(args.app_dir).resolve()
    archive_path = Path(args.zip).resolve()
    wait_for_pid(args.pid)
    apply_update(app_dir, archive_path)
    restart(app_dir)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        log_path=Path(tempfile.gettempdir()) / 'MF_CaseNumberOCR_Update_Error.txt'
        try:
            log_path.write_text(traceback.format_exc(),encoding='utf-8')
        except OSError:
            pass
        if os.name == 'nt':
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    'MFCase GitHub update failed.\n\n' + str(exc) +
                    '\n\nDiagnostic: ' + str(log_path) +
                    '\n\nYour existing application and learning are preserved.',
                    'MFCase Update Error', 0x10)
            except Exception:
                pass
        raise
