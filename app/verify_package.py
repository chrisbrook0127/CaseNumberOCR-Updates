from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '2355db5b6a1e41e4e3936dff1c715fc62827835520769dac4ab052e63350e43f', 'updater_apply.py': '73ce683d003c5346a3e2162504f487e0f28182808893c08e0768327d715addc3'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.4.1 application and updater integrity")
