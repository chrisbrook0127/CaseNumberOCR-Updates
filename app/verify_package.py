from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parent
EXPECTED={'case_number_ocr.py': '6cd7f68d8a26342a884f49e549f25732bde24a14c684b2853adc0208ca4a6de7', 'updater_apply.py': '73ce683d003c5346a3e2162504f487e0f28182808893c08e0768327d715addc3'}
for name,expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name
print("PASS: V5.4.0 application and updater integrity")
