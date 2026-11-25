from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': 'dc4d67fef3b00fc6706889cfc8ac7903042608c91ecb0504f1e800daa1423930', 'updater_apply.py': '293701a5f4566162e705ecc93eb593144199b8ccd045f626fb5b25f9e33af211'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.6.13 application and updater integrity")
