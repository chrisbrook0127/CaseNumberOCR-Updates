"""Build MFCase 5.0.11 strictly from the verified 5.0.10 release."""
from __future__ import annotations
import ast, base64, hashlib, io, re, zipfile
from pathlib import Path

root=Path("release-assets")
old_archive=base64.b64decode((root/"update.base64").read_text().strip(),validate=True)
assert hashlib.sha256(old_archive).hexdigest()=="7f205b36dca0965f4f47cdd6aeae3f7c527faef84a06178f3b981ce63ce85804", "Wrong v5.0.10 source"
with zipfile.ZipFile(io.BytesIO(old_archive)) as old:
    assert old.testzip() is None
    names=old.namelist()
    assert len(names)==len(set(names))
    assert {"case_number_ocr.py","updater_apply.py"}<=set(names)
    assert all("/" not in n and "\\" not in n and n for n in names)
    content={name:old.read(name) for name in names}

source=content["case_number_ocr.py"].decode("utf-8")
assert source.count('self.app_version = "5.0.10"')==1, "Unexpected app version"
assert source.count('filename = f"{case}.pdf"')==1, "Unexpected exporter implementation"
assert "def _case_number_for_export(" not in source

helper = '''
def _case_number_for_export(value):
    """Drop leading zeros only from case digits during the exported PDF rename.

    Preserve year digits and let existing collision handling append its own
    (1), (2), etc. Never change the original PDF filenames during scan.
    """
    original = normalize_case_number(value)
    match = re.fullmatch(r"(\\d{1,2})-(\\d{1,7})", original)
    if not match:
        raise ValueError(f"Case number cannot be exported: {value!r}")
    year, case_digits = match.groups()
    return f"{year}-{int(case_digits)}"

'''
assert len(re.findall(r"(?m)^CASE_RE\s*=",source))==1
source=re.sub(r"(?m)^(CASE_RE\s*=)",lambda m: helper+m.group(1),source,count=1)
source,count=re.subn(
    r'(?m)^([ \t]*)filename = f"\{case\}\.pdf"[ \t]*$',
    lambda m:m.group(1)+'case = _case_number_for_export(case)\n'+m.group(1)+'filename = f"{case}.pdf"',
    source,count=1
)
assert count==1,"Could not patch export filename"
source=source.replace('self.app_version = "5.0.10"','self.app_version = "5.0.11"')
source=source.replace("V5.0.10","V5.0.11")
compile(source,"case_number_ocr.py","exec")
parsed=ast.parse(source)
fn=[n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name=="_case_number_for_export"]
assert len(fn)==1
ns={"normalize_case_number":lambda x:str(x).strip(),"re":re}
exec(compile(ast.Module(body=fn,type_ignores=[]),"test_case_export","exec"),ns)
canon=ns["_case_number_for_export"]
for original,expected in [
    ("08-19","08-19"),("08-0019","08-19"),("08-000019","08-19"),
    ("8-000019","8-19"),("08-0000","08-0"),("08-1234567","08-1234567"),
    ("08-0012345","08-12345")
]:
    assert canon(original)==expected,(original,canon(original))
for bad in ["08-12345678","foo","08-19 (1)","08-1-9"]:
    try:canon(bad)
    except ValueError:pass
    else:raise AssertionError("Unexpected acceptance "+bad)

content["case_number_ocr.py"]=source.encode("utf-8")
content["CHANGELOG_V5_0_11.txt"]=(
    "V5.0.11 - Leading-zero normalization on first export rename\n"
    "- Export case 08-0019 and 08-19 as 08-19, preserving numbered copy suffixes.\n"
    "- Preserve incident year (08), original scan filenames and all learning data.\n"
    "- Leave V5.0.10 field-first OCR, scan selection and sequence checks unchanged.\n"
).encode("utf-8")
out=io.BytesIO()
with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as new:
    for filename,data in content.items():new.writestr(filename,data)
archive=out.getvalue()
with zipfile.ZipFile(io.BytesIO(archive)) as z:
    assert z.testzip() is None
    assert set(z.namelist())==set(content)
    assert all(z.read(n)==content[n] for n in content)
    for n in ["case_number_ocr.py","updater_apply.py"]:compile(z.read(n),n,"exec")
digest=hashlib.sha256(archive).hexdigest()
(root/"update.base64").write_text(base64.b64encode(archive).decode("ascii")+"\n")
(root/"SHA256.txt").write_text(digest+"\n")
(root/"RELEASE_TAG.txt").write_text("v5.0.11\n")
(root/"PUBLISH_REQUEST.txt").write_text("Publish v5.0.11 leading-zero-only export normalization.\n")
(root/"CaseNumberOCR_Update.zip").write_bytes(archive)
print("MFCase v5.0.11 built and verified from V5.0.10, SHA256:",digest)
