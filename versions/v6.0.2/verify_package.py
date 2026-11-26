from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '382d0ffc4c53fa2c17ed323c163113a241f7e4b6bc30560c44f9397cd32b4c87', 'updater_apply.py': '293701a5f4566162e705ecc93eb593144199b8ccd045f626fb5b25f9e33af211'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,name
print("PASS: MFCase v6.0.2 application integrity")
