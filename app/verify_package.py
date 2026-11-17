from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '8a32f30abcc17de6d5de5a225b5ee227b1e4b695607f3ffddd74a01fd71b25ea', 'updater_apply.py': '73ce683d003c5346a3e2162504f487e0f28182808893c08e0768327d715addc3'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.3.2 application and updater integrity")
