"""Make V5.1.3 updater-compatible with *already installed* MFCase releases.

Source is exactly the publicly released V5.1.2 asset, SHA-256 checked.
The V5.1.3 update ZIP deliberately contains ONLY the two files accepted by
every existing application updater: case_number_ocr.py and updater_apply.py.
Extra release notes belong in this repo, not in the update ZIP.
"""
from __future__ import annotations
import ast, base64, hashlib, io, re, zipfile
from pathlib import Path

root=Path("release-assets")
old=base64.b64decode(root.joinpath("update.base64").read_text().strip(),validate=True)
pinned="f5aa1ee267a47cb47f45e08fb873fc407dc23d98802d635b0c9736acd473d30a"
assert hashlib.sha256(old).hexdigest()==pinned,"Not the official V5.1.2 source"
with zipfile.ZipFile(io.BytesIO(old)) as inp:
    assert inp.testzip() is None
    names=inp.namelist()
    assert len(names)==len(set(names))
    assert all(n and "/" not in n and "\\" not in n for n in names)
    extra=[n for n in names if n not in {"case_number_ocr.py","updater_apply.py"}]
    assert all(n in {"RUN_CASE_OCR.bat","README.txt"} or (n.startswith("CHANGELOG_") and n.endswith(".txt")) for n in extra),extra
    source=inp.read("case_number_ocr.py").decode("utf-8")
    apply=inp.read("updater_apply.py").decode("utf-8")
assert source.count('self.app_version = "5.1.2"')==1
source=source.replace('self.app_version = "5.1.2"','self.app_version = "5.1.3"',1)

# Source app preflights download ZIP *before* launching the external updater.
# Accept legacy explicitly listed file types, plus strictly named changelog
# text files. Keep rejection of everything else and all directories.
old_app='if len(path.parts) != 1 or path.name not in allowed:'
new_app='''if (len(path.parts) != 1 or path.name != name or
                            (path.name not in allowed and not re.fullmatch(
                                r"CHANGELOG_V[0-9]+(?:_[0-9]+){1,3}\\.txt",path.name))):'''
# We intentionally ship NO changelog ZIP entry; this change only prevents
# future versions falling foul of the new app's validation.
assert source.count(old_app)==1, "Unexpected primary updater validator"
source=source.replace(old_app,new_app,1)

# The helper was originally hard-coded to allow only a small number of
# historical changelogs; mirror the same constrained future rule.
# The packaged helper can differ from the full install bootstrap copy.
# Import re only if the production helper has not already imported it.
if not re.search(r"(?m)^import [^\n]*\bre\b",apply):
    marker="from __future__ import annotations\n"
    assert marker in apply
    apply=apply.replace(marker,marker+"import re\n",1)
# Adapt the released helper's existing allowlist while preserving all of its
# path-safety checks. The installed helper (pre-update) is unchanged, so this
# only affects later releases after V5.1.3 has been installed.
old_apply='if len(path.parts) != 1 or path.name not in ALLOWED or info.file_size > 20_000_000:'
new_apply='''if (len(path.parts) != 1 or path.name != info.filename or
                    (path.name not in ALLOWED and not re.fullmatch(
                        r"CHANGELOG_V[0-9]+(?:_[0-9]+){1,3}\\.txt",path.name))
                    or info.file_size > 20_000_000):'''
assert apply.count(old_apply)==1,"Unexpected production helper path/size guard"
apply=apply.replace(old_apply,new_apply,1)

# Catch syntax regressions and validate that the upstream OCR is preserved.
compile(source,"case_number_ocr.py","exec")
compile(apply,"updater_apply.py","exec")

# Run both new updaters' actual ZIP-entry regexes, not just a copied test pattern.
for update_source in (source, apply):
    regexes=[n.value for n in ast.walk(ast.parse(update_source))
             if isinstance(n,ast.Constant) and isinstance(n.value,str)
             and n.value.startswith("CHANGELOG_V[0-9]+")]
    assert len(regexes)==1, ("Bad update allowlist regex",regexes)
    regex=re.compile(regexes[0])
    assert regex.fullmatch("CHANGELOG_V5_1_3.txt"), regexes[0]
    assert not regex.fullmatch("bad.py") and not regex.fullmatch("folder/file.txt")

assert source.count("def _ocr_pdf(")==1
assert 'dimension_px=int(round(3*72*2.2))' in source
assert 'observed_score < 95.0' in source
assert 'os.utime(destination, None)' in source
assert 'self.app_version = "5.1.3"' in source
# Pattern must match normal versions but not directories or non-text.
test_pattern=re.compile(r"CHANGELOG_V[0-9]+(?:_[0-9]+){1,3}\.txt")
for name in ["CHANGELOG_V5_1_3.txt","CHANGELOG_V3_4_9.txt"]:
    assert test_pattern.fullmatch(name)
for name in ["../CHANGELOG_V5_1_3.txt","CHANGELOG_V5_1_3.py",
             "CHANGELOG_NOT_A_VERSION.txt","evil.py","folder/x.txt"]:
    assert not test_pattern.fullmatch(name)

# Validate compatibility against the unchanged V5.1.2 application rules;
# this is the critical requirement: old installed updater must accept ZIP.
legacy_allowed={"case_number_ocr.py", "updater_apply.py",
    "RUN_CASE_OCR.bat", "MF.ico", "Launch_Case_OCR.vbs", "README.txt",
    "CHANGELOG_V3_2_0.txt", "CHANGELOG_V3_3_0.txt",
    "CHANGELOG_V3_3_1.txt", "CHANGELOG_V3_3_2.txt",
    "CHANGELOG_V3_3_3.txt", "CHANGELOG_V3_3_4.txt",
    "CHANGELOG_V3_4_3.txt"}
members={"case_number_ocr.py":source.encode(),"updater_apply.py":apply.encode()}
assert set(members)<=legacy_allowed
out=io.BytesIO()
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for name,payload in members.items():
        z.writestr(name,payload)
data=out.getvalue()
with zipfile.ZipFile(io.BytesIO(data)) as z:
    assert z.testzip() is None
    assert set(z.namelist())==set(members)
    assert all(len(Path(n).parts)==1 and n in legacy_allowed for n in z.namelist())
    for n in z.namelist():
        assert z.read(n)==members[n]
    assert 'self.app_version = "5.1.3"' in z.read("case_number_ocr.py").decode()
digest=hashlib.sha256(data).hexdigest()
root.joinpath("update.base64").write_text(base64.b64encode(data).decode("ascii"))
root.joinpath("CaseNumberOCR_Update.zip").write_bytes(data)
root.joinpath("SHA256.txt").write_text(digest+"\n")
root.joinpath("RELEASE_TAG.txt").write_text("v5.1.3\n")
root.joinpath("PUBLISH_REQUEST.txt").write_text("Tested backward-compatible V5.1.3 updater package\n")
print("V5.1.3 OK - backwards-compatible update ZIP")
print("SHA256",digest,"size",len(data),"files",list(members))
