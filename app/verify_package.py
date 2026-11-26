from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '1e284f05351516aaa5aabd8e516267c7f2046a8bba68ad9b6e7a8ef774979aa1', 'updater_apply.py': '293701a5f4566162e705ecc93eb593144199b8ccd045f626fb5b25f9e33af211'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.6.16 application and updater integrity")
