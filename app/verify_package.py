from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '59cdece68241e99171a4e4a4f76e9b79544dd81494f1de0b8e65830c92f3029e', 'updater_apply.py': '73ce683d003c5346a3e2162504f487e0f28182808893c08e0768327d715addc3'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.3.8 application and updater integrity")
