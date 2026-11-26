from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '66230453304b29f11ee807a90fe6c8d9137b9e696d8f0d4ec039913d5d4c766f', 'updater_apply.py': '293701a5f4566162e705ecc93eb593144199b8ccd045f626fb5b25f9e33af211'}
for n,h in EXPECTED.items():
    assert hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h,n
print('PASS: MFCase v5.6.18 application integrity')
