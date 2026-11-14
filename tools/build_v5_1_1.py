"""MFCase V5.1.1: verified patch from the actual V5.0.10 GitHub release."""
from __future__ import annotations
import ast, base64, hashlib, io, re, zipfile
from pathlib import Path

root=Path("release-assets")
source_package=base64.b64decode((root/"update.base64").read_text().strip(),validate=True)
assert hashlib.sha256(source_package).hexdigest()=="7f205b36dca0965f4f47cdd6aeae3f7c527faef84a06178f3b981ce63ce85804", "Release source is not V5.0.10"
with zipfile.ZipFile(io.BytesIO(source_package)) as original:
    assert original.testzip() is None
    names=original.namelist()
    assert len(names)==len(set(names))
    assert all(n and "/" not in n and "\\" not in n for n in names)
    assert {"case_number_ocr.py","updater_apply.py"}.issubset(names)
    files={name:original.read(name) for name in names}
src=files["case_number_ocr.py"].decode("utf-8")
assert src.count('self.app_version = "5.0.10"')==1

def replace_once(old,new):
    global src
    count=src.count(old)
    if count!=1:
        raise AssertionError(f"Expected one source pattern, found {count}: {old[:140]!r}")
    src=src.replace(old,new,1)

# Only the designated case-number area should be compared for ambiguity.
# Number-shaped strings in the narrative body must not compete with the top
# right handwritten number when there is no INCIDENT NO./CASE NUMBER caption.
helper=r'''
def _mfcase_header_field_candidates(hits, page_shape):
    """Isolate a UNIQUE case number at top right; keep conflicts for review.

    The detection's absolute document coordinates (not score alone) decide
    whether it is inside the likely handwritten incident-number header.
    This is intentionally not used when a labeled field exists.
    """
    try:
        height, width = page_shape[:2]
        header=[]
        for hit in hits:
            rect=hit.get("rect")
            if not rect or len(rect)!=4:
                continue
            x0,y0,x1,y1=[float(v) for v in rect]
            mid_x=(x0+x1)/2.0
            mid_y=(y0+y1)/2.0
            if (mid_x >= width*0.60 and mid_y <= height*0.15 and
                    0 <= y0 < y1 <= height*0.19 and
                    re.fullmatch(r"\d{1,2}-\d{1,6}",str(hit.get("number") or ""))):
                header.append(hit)
        if header and len({h["number"] for h in header})==1:
            return header
    except (ValueError,TypeError,IndexError):
        pass
    return hits


def _mfcase_export_case_name(value):
    """Remove zeros ONLY before case digits, retaining the original year."""
    original=normalize_case_number(value)
    match=re.fullmatch(r"(\d{1,2})-(\d{1,7})", original)
    if not match:
        raise ValueError(f"Invalid case number for export: {value!r}")
    year,case_digits=match.groups()
    return f"{year}-{int(case_digits)}"


'''
replace_once("class App:",helper+"class App:")
replace_once('self.app_version = "5.0.10"','self.app_version = "5.1.1"')
replace_once('filename = f"{case}.pdf"','case = _mfcase_export_case_name(case)\n                filename = f"{case}.pdf"')
# Isolate 08-19 in the TOP RIGHT header; ignore 08-14 in report narrative.
replace_once(
    '                    elif hits:\n                        results.append((page_index,image,hits,"fallback"))',
    '                    elif hits:\n'
    '                        header_hits=_mfcase_header_field_candidates(hits,image.shape)\n'
    '                        if (header_hits is not hits and\n'
    '                                max(float(h.get("score") or 0) for h in header_hits)>=99.5):\n'
    '                            results.append((page_index,image,header_hits,"header"))\n'
    '                        else:\n'
    '                            results.append((page_index,image,hits,"fallback"))'
)
replace_once(
    '{"incident": 2, "case": 1, "fallback": 0}[source]',
    '{"incident": 3, "case": 2, "header": 1, "fallback": 0}[source]')
replace_once(
    '            elif source=="case":\n                note="CASE NUMBER field full number — OCR confidence checked"',
    '            elif source=="case":\n                note="CASE NUMBER field full number — OCR confidence checked"\n'
    '            elif source=="header":\n'
    '                note="Upper-right header unique 100% OCR read — handwritten field position; sequence cross-check"'
)
# The header is a distinct, tightly scoped source; the whole 5x5" fallback
# remains review-only. There is no automatic approval from irrelevant text.
replace_once(
    '# Diagnostic counters belong to THIS run, not accumulated saved state.',
    'if note.startswith("Upper-right header"):\n'
    '                         r["scan_source"]="header"\n'
    '                     # Diagnostic counters belong to THIS run, not accumulated saved state.')
replace_once(
    'and r["scan_source"] in ("incident", "case")',
    'and (r["scan_source"] in ("incident", "case") or\n'
    '                                  (r["scan_source"]=="header" and float(score or 0)>=99.5))')
# A 100% score only qualifies for header approval when it came from that
# physical header, not from a random body match. This still respects auto ON.
replace_once(
    '        if record.get("scan_source") not in ("incident", "case"):\n            return None',
    '        if record.get("scan_source") not in ("incident", "case", "header"):\n'
    '            return None\n'
    '        if (record.get("scan_source")=="header" and\n'
    '                float(record.get("score") or 0)<99.5):\n'
    '            return None')
# Before/after: preserve a strong labeled field, but a weak/ambiguous or
# unrelated fallback cannot veto two matching, independently read neighbors.
replace_once(
    '            strong_read = (candidate and not r.get("scan_ambiguous")\n'
    '                           and float(r.get("score") or 0) >= self._get_auto_threshold())\n'
    '            if strong_read:\n                continue',
    '            strong_read = (candidate and not r.get("scan_ambiguous")\n'
    '                           and r.get("scan_source") in ("incident","case","header")\n'
    '                           and float(r.get("score") or 0) >=\n'
    '                           (99.5 if r.get("scan_source")=="header" else self._get_auto_threshold()))\n'
    '            if strong_read:\n'
    '                if candidate != consensus:\n'
    '                    r["sequence_conflict"]=True\n'
    '                    r["status"]=(f"Strong case-field read {candidate} conflicts with "\n'
    '                                 f"both neighbors {consensus} — MANUAL REVIEW")\n'
    '                    if r.get("auto_labeled"):\n'
    '                        r["confirmed"]=""\n'
    '                        r["auto_labeled"]=False\n'
    '                        r["manual_approved"]=False\n'
    '                        if self.completed_tree.exists(iid):\n'
    '                            self.completed_tree.delete(iid)\n'
    '                        r["completed_iid"]=None\n'
    '                    self._insert_scan_row(iid,r)\n'
    '                    updated+=1\n'
    '                continue')
replace_once(
    '            r["scan_ambiguous"] = False\n            r["sequence_conflict"] = False',
    '            r["sequence_original_candidate"]=candidate\n'
    '            r["sequence_original_ambiguous"]=bool(r.get("scan_ambiguous"))\n'
    '            r["scan_ambiguous"] = False\n'
    '            r["sequence_conflict"] = False')
# A valid one-digit year is equivalent to the same two-digit selected year.
replace_once(
    '        if year and not normalized.startswith(year + "-"):\n'
    '            return "Case number does not match the selected year."',
    '        observed_year=normalized.split("-",1)[0]\n'
    '        if year and not (year.isdigit() and observed_year.isdigit() and\n'
    '                         int(observed_year)==int(year)):\n'
    '            return "Case number does not match the selected year."')
# An OCR engine that actually used the handwriting recognizer automatically
# preselects handwritten for human correction. User may change classification.
replace_once(
    '                value=r.get("training_type", "unsure"))',
    '                value=("handwritten" if r.get("handwritten") and\n'
    '                       r.get("training_type","unsure")=="unsure" else\n'
    '                       r.get("training_type","unsure")))')
# Keep existing OCR model, learning data, training architecture and other UI.
src=src.replace("V5.0.10","V5.1.1")
compile(src,"case_number_ocr.py","exec")
tree=ast.parse(src)
fns={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
assert {"_mfcase_header_field_candidates","_mfcase_export_case_name"}<=set(fns)
ns={"re":re,"normalize_case_number":lambda x:str(x).strip()}
for name in ("_mfcase_header_field_candidates","_mfcase_export_case_name"):
    n=fns[name]
    exec(compile(ast.Module(body=[n],type_ignores=[]),"test_rules","exec"),ns)
choose=ns["_mfcase_header_field_candidates"]
rename=ns["_mfcase_export_case_name"]
for actual,expected in [
    ("08-19","08-19"),("08-0019","08-19"),("08-000019","08-19"),
    ("8-0019","8-19"),("08-0000","08-0"),("08-1234567","08-1234567")]:
    assert rename(actual)==expected,(actual,expected)
for bad in ["08-19 (1)","invalid","08-12345678"]:
    try:rename(bad)
    except ValueError:pass
    else:raise AssertionError("Invalid case name accepted: "+bad)
header={"number":"08-19","score":100.0,"rect":(690,6,780,37)}
irrelevant={"number":"08-14","score":100.0,"rect":(605,205,770,231)}
assert choose([irrelevant,header],(800,800,3))==[header]
assert choose([irrelevant],(800,800,3))==[irrelevant]
conflict={"number":"08-20","score":99.9,"rect":(690,10,779,38)}
two=[header,conflict,irrelevant]
assert choose(two,(800,800,3))==two, "Conflicting header readings require review"
assert "score" not in str(rename("08-000019"))
assert "sequence_original_candidate" in src
assert "self.app_version = \"5.1.1\"" in src
files["case_number_ocr.py"]=src.encode("utf-8")
files["CHANGELOG_V5_1_1.txt"]=(
    "V5.1.1 — case-field and handwriting review improvements\n"
    "Source: verified V5.0.10 production release.\n"
    "A unique 99.5%+ OCR reading in the top-right header can qualify for auto-confirm.\n"
    "Other case-shaped text deeper in the page cannot compete with that header.\n"
    "INCIDENT NO. / CASE NUMBER labeled fields still override header guesses.\n"
    "Two matching immediately adjacent strong reads may infer a weak/ambiguous middle scan.\n"
    "Contradictions between strong labeled reads and neighbors require manual review.\n"
    "Sequence inference retains an audit of the original candidate and uncertainty.\n"
    "Initial transfer renaming removes only leading zeros in case digits.\n"
    "TrOCR-based manual reviews default to the handwritten learning category.\n"
    "Existing shared learning model, originals, progress and updater are preserved.\n"
).encode()
out=io.BytesIO()
with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for path,data in files.items():archive.writestr(path,data)
data=out.getvalue()
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    assert archive.testzip() is None
    assert set(archive.namelist())==set(files)
    for path in ["case_number_ocr.py","updater_apply.py"]:
        compile(archive.read(path),path,"exec")
digest=hashlib.sha256(data).hexdigest()
(root/"update.base64").write_text(base64.b64encode(data).decode("ascii"))
(root/"CaseNumberOCR_Update.zip").write_bytes(data)
(root/"SHA256.txt").write_text(digest+"\n")
(root/"RELEASE_TAG.txt").write_text("v5.1.1\n")
(root/"PUBLISH_REQUEST.txt").write_text("Validated V5.1.1 case OCR and handwriting rules\n")
print("V5.1.1 OK: SHA256",digest,"size",len(data),"checks",14)
