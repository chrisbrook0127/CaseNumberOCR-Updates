from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '479e0b4d349ee0e79ee59d47c8b3c220fb177f96d9e2680358e6465fe0355f4c', 'updater_apply.py': '293701a5f4566162e705ecc93eb593144199b8ccd045f626fb5b25f9e33af211'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V6.0.0 application and updater integrity")
