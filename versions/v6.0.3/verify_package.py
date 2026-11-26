from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '31ad971c7bdd7fae03c6b7dbceb84da68f70c2630be4ac9162c93cbea2d52519', 'updater_apply.py': '293701a5f4566162e705ecc93eb593144199b8ccd045f626fb5b25f9e33af211'}
for n,h in EXPECTED.items():
    assert hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h,n
print("PASS: MFCase v6.0.3 integrity")
