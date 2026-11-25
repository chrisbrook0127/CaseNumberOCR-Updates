from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': 'b6700d73a982574927dec5cb2535ee77ed9b87404dcc0e4668311219eccdf261', 'updater_apply.py': '293701a5f4566162e705ecc93eb593144199b8ccd045f626fb5b25f9e33af211'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.6.4 application and updater integrity")
