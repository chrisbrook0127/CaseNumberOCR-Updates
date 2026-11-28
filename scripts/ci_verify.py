"""Fail-closed regression discovery and release ZIP validation; synthetic tests only."""
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

STABLE = {
    "CaseNumberOCR_Update.zip": "2f2fdec1d19d26720a4e622f632d3678f1c914272403961c2e21fef410d22a76",
    "MFCase_Number_OCR_V6.0.3_Full_Online_Install.zip": "25c59ded15c502a9fdf087a9c933e822c5531bd849b090bfe0b19a53a762e3ca",
}
CANDIDATE = {"CaseNumberOCR_Update.zip", "MFCase_Number_OCR_V6.0.4_TEST_Full_Online_Install.zip"}

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run_tests(test_dir, pattern, app):
    test_dir, app = Path(test_dir).resolve(), Path(app).resolve()
    require(test_dir.is_dir() and app.is_dir(), "Missing real test or app directory")
    sys.path[:0] = [str(test_dir), str(app)]
    suite = unittest.defaultTestLoader.discover(str(test_dir), pattern=pattern)
    require(suite.countTestCases() > 0, "No tests discovered")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("Actual tests run:", result.testsRun, "skipped:", len(result.skipped))
    require(result.testsRun > 0 and result.wasSuccessful() and result.testsRun > len(result.skipped), "Regression failure or no executed tests")

def read_programs(path):
    with ZipFile(path) as z:
        names = z.namelist()
        require(z.testzip() is None and len(names) == len(set(n.casefold() for n in names)), "Corrupt or colliding ZIP entries")
        for n in names:
            p = PurePosixPath(n)
            require(n and not p.is_absolute() and ".." not in p.parts and "\\" not in n and ":" not in n, "Unsafe ZIP path")
            require(not any(part.lower() in {".case_ocr_learning", "logs", ".git", "__pycache__"} for part in p.parts), "Unexpected private/runtime directory")
            require(p.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".mp4", ".safetensors", ".pt", ".pth", ".pyc"}, "Unexpected private/runtime file")
        programs = {}
        for name in ("case_number_ocr.py", "updater_apply.py", "verify_package.py"):
            hits = [n for n in names if PurePosixPath(n).name == name]
            require(len(hits) == 1, "Missing/ambiguous " + name)
            data = z.read(hits[0])
            compile(data, name, "exec")
            programs[name] = data
        return programs

def verify_packages(target, root):
    dist = Path(root) / "dist"
    if target == "Stable":
        expected, version = STABLE, "6.0.3"
    elif target == "Candidate":
        expected = json.loads((dist / "SHA256SUMS.json").read_text(encoding="utf-8"))
        require(set(expected) == CANDIDATE, "Unexpected candidate artifact set")
        version = "6.0.4"
    else:
        raise RuntimeError("Unknown release target")
    for name, value in expected.items():
        require(re.fullmatch(r"[0-9a-f]{64}", value) is not None, "Invalid digest")
        require(digest(dist / name) == value, "Digest mismatch: " + name)
        print("Verified", name, value)
    updater = read_programs(dist / "CaseNumberOCR_Update.zip")
    full = read_programs(dist / next(n for n in expected if n != "CaseNumberOCR_Update.zip"))
    require(updater == full, "Installer and updater program source mismatch")
    require(('self.app_version = "' + version + '"').encode() in full["case_number_ocr.py"], "Version mismatch")

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "tests" and len(sys.argv) == 5:
        run_tests(*sys.argv[2:])
    elif mode == "packages" and len(sys.argv) == 4:
        verify_packages(*sys.argv[2:])
    else:
        raise SystemExit("Expected tests <directory> <pattern> <app> or packages <target> <root>")
