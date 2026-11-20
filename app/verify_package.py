from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '10af2ce324717783595e7f4c1f2be399d6ff8d04a0923cc35f5ef45fa3a1e5be', 'updater_apply.py': '73ce683d003c5346a3e2162504f487e0f28182808893c08e0768327d715addc3'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.3.9 application and updater integrity")
