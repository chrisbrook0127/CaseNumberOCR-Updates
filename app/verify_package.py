from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': 'bd6062a6edc51d9f76dec8ec9a0b60d459fe8d92263f07eeb6e3c2555f30d623', 'updater_apply.py': '293701a5f4566162e705ecc93eb593144199b8ccd045f626fb5b25f9e33af211'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.6.20 application and updater integrity")
