import ast
import re
import sys
import zipfile
from pathlib import Path

BASE = Path(sys.argv[1])
OUT = Path(sys.argv[2])

with zipfile.ZipFile(BASE, "r") as z:
    files = {Path(n).name: z.read(n) for n in z.namelist() if not n.endswith("/")}

if "case_number_ocr.py" not in files:
    raise RuntimeError("The V3.3.6 application source is missing.")

src = files["case_number_ocr.py"].decode("utf-8")
if 'self.app_version = "3.3.6"' not in src:
    raise RuntimeError("Expected V3.3.6 source was not found.")

pattern = re.compile(
    r"(?m)^(?:    @staticmethod[ \t]*\n)?"
    r"    def _normalized_training_type[ \t]*\([^)]*\):"
)
matches = list(pattern.finditer(src))
if len(matches) != 1:
    raise RuntimeError(
        f"Expected one _normalized_training_type method, found {len(matches)}."
    )

fixed_signature = (
    "    @staticmethod\n"
    "    def _normalized_training_type(value):"
)
src = pattern.sub(fixed_signature, src, count=1)
src = src.replace('self.app_version = "3.3.6"', 'self.app_version = "3.3.7"', 1)

# Verify that the method is explicitly static and accepts exactly one value.
module = ast.parse(src, filename="case_number_ocr.py")
app = next(
    (node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "App"),
    None
)
if app is None:
    raise RuntimeError("App class not found.")
methods = [
    node for node in app.body
    if isinstance(node, ast.FunctionDef) and node.name == "_normalized_training_type"
]
if len(methods) != 1:
    raise RuntimeError("The corrected method is missing or duplicated.")
method = methods[0]
if not any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in method.decorator_list):
    raise RuntimeError("_normalized_training_type is not static.")
if [arg.arg for arg in method.args.args] != ["value"]:
    raise RuntimeError("_normalized_training_type must accept only value.")

# The crop-saving path must still call the shared static helper.
save_method = next(
    (node for node in app.body if isinstance(node, ast.FunctionDef) and node.name == "_save_learning_example"),
    None
)
if save_method is None or "self._normalized_training_type" not in ast.get_source_segment(src, save_method):
    raise RuntimeError("Learning example saving no longer uses the normalized-type helper.")

compile(src, "case_number_ocr.py", "exec")
files["case_number_ocr.py"] = src.encode("utf-8")

# Verify the exact failure mode is fixed by normal instance-style access.
namespace = {}
exec(
    "class _BindingCheck:\n"
    "    @staticmethod\n"
    "    def _normalized_training_type(value):\n"
    "        return str(value or 'unsure').strip().lower()\n",
    namespace
)
assert namespace["_BindingCheck"]()._normalized_training_type("Handwritten") == "handwritten"

with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for name, data in files.items():
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        z.writestr(info, data)

with zipfile.ZipFile(OUT, "r") as z:
    bad = z.testzip()
    if bad:
        raise RuntimeError(f"ZIP integrity failure: {bad}")
print(f"V3.3.7 package built: {OUT.stat().st_size} bytes")
