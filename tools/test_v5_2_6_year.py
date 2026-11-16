"""Run the actual released MFCase V5.2.6 year restriction, not a replica."""
import ast,base64,hashlib,io,re,tempfile,zipfile
from pathlib import Path

base=Path("release-assets")
raw=base64.b64decode((base/"update.base64").read_text(),validate=True)
assert hashlib.sha256(raw).hexdigest()==(base/"SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    source=z.read("case_number_ocr.py").decode()
tree=ast.parse(source)
tops={"normalize_case_number","_mfcase_export_case_name","_mfcase_header_field_candidates"}
methods={"_year_prefix","_diag_field_filter","_auto_label_block_reason",
         "_get_auto_threshold","_sequence_case_number","_eligible_for_kurt",
         "_save_learning_example","_save_learning_label_for_file"}
top=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in tops]
variables=[n for n in tree.body if isinstance(n,ast.Assign) and any(
    isinstance(t,ast.Name) and t.id in
    {"CASE_RE","INCIDENT_NUMBER_RE","REQUIRED_CASE_YEAR"}
    for t in n.targets)]
actual=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in methods]
assert {n.name for n in top}==tops
assert {n.name for n in actual}==methods
clazz=ast.ClassDef(name="AppMethods",bases=[],keywords=[],
                   body=actual,decorator_list=[])
ast.fix_missing_locations(clazz)
namespace={"re":re,"Path":Path}
exec(compile(ast.Module(body=variables+top+[clazz],type_ignores=[]),
             "published-v5.2.6-selected-methods.py","exec"),namespace)
CASE_RE=namespace["CASE_RE"]
INCIDENT_NUMBER_RE=namespace["INCIDENT_NUMBER_RE"]
normal=namespace["normalize_case_number"]
export_name=namespace["_mfcase_export_case_name"]
assert namespace["REQUIRED_CASE_YEAR"]=="08"

for case in ("08-1","08-19","08-0019","08-0000001"):
    assert CASE_RE.fullmatch(case),case
for case in ("09-19","07-19","8-19","2008-19","08","19","08-",
             "08-12345678","008-19","08-19-1","08-1foo","08-12 3"):
    assert not CASE_RE.fullmatch(case),case
assert normal("  08 — 0019 ")=="08-0019"
assert CASE_RE.fullmatch(normal("08 — 0019"))
assert export_name("08-000019")=="08-19"
assert export_name("08-0000001")=="08-1"
for case in ("09-000019","8-19","2008-19","19","08","08-"):
    try:export_name(case)
    except ValueError:pass
    else:raise AssertionError(("export accepted wrong year",case))
assert INCIDENT_NUMBER_RE.search("INCIDENT NO. 08 - 0044")
assert not INCIDENT_NUMBER_RE.search("INCIDENT NO. 09-0044")
assert not INCIDENT_NUMBER_RE.search("INCIDENT NO. 8-0044")
print("PASS literal 08- required for case format, printed field and exported filenames")

class Var:
    def __init__(self,x):self.x=x
    def get(self):return self.x
    def set(self,x):self.x=x

class App(namespace["AppMethods"]):
    def __init__(self):
        self.year_var=Var("09")
        self.scan_year_prefix="09"  # deliberate stale value must NOT override 08
        self.auto_label_var=Var(True)
        self.learning_dir=None
        self.events=[]
        self.busy=False
    def _diag_hits(self,hits):return [{"number":x.get("number"),"score":x.get("score")} for x in hits]
    def _diag_event(self,event,**kwargs):self.events.append((event,kwargs))

app=App()
for saved_year in ("09","2009","8","2008","","08"):
    app.year_var.set(saved_year)
    assert app._year_prefix()=="08" and app.year_var.get()=="08"
for case in ("09-44","8-44","2008-44","44","08"):
    assert app._auto_label_block_reason(100,True,case),case
assert app._auto_label_block_reason(95,False,"08-44") is None
assert app._auto_label_block_reason(94.9,False,"08-44") is not None
print("PASS 95% auto-confirm works only with literal 08- even when saved year says 09")

hits=[
    {"number":"09-44","score":100.0,"rect":(0,0,20,20)},
    {"number":"8-44","score":100.0,"rect":(0,0,20,20)},
    {"number":"2008-44","score":100.0,"rect":(0,0,20,20)},
    {"number":"08-44","score":96.0,"rect":(0,0,20,20)},
    {"number":"08-0044","score":96.0,"rect":(0,0,20,20)},
]
selected=app._diag_field_filter(hits,"fallback")
assert {h["number"] for h in selected}=={"08-44","08-0044"}
rejected=[e[1] for e in app.events if e[0]=="candidate_filter"][-1]
assert sum(item["reason"]=="required_literal_08_prefix_missing"
           for item in rejected["ignored"])>=2,rejected
assert rejected["expected_year"]=="08"
print("PASS wrong-year OCR reads are rejected and recorded in diagnostics")

for case in ("09-44","8-44","2008-44","08","44"):
    record={"candidate":case,"score":100,"scan_source":"incident",
            "scan_ambiguous":False,"sequence_conflict":False,
            "sequence_inferred":False,"sequence_excluded":False}
    assert app._sequence_case_number(record) is None,case
record["candidate"]="08-44"
assert app._sequence_case_number(record)=="08-44"
print("PASS before/after sequence anchors require literal 08-")

with tempfile.TemporaryDirectory(prefix="MFCase 08 tests ") as temp:
    folder=Path(temp);path=folder/"Image4920.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    app.folder=folder
    rec={"path":path,"confirmed":"09-44","manual_approved":True,
         "auto_labeled":False,"exported":False,"scan_ambiguous":False,
         "sequence_conflict":False}
    assert not app._eligible_for_kurt("i",rec,automatic=True)
    rec["confirmed"]="08-0044"
    assert app._eligible_for_kurt("i",rec,automatic=True)
    print("PASS saved/manual approvals cannot transfer unless case starts 08-")

for noncase in ("09-44","8-44","2008-44","44"):
    try:app._save_learning_example({"path":Path("anything.pdf")},noncase)
    except ValueError:pass
    else:raise AssertionError(("learning accepted invalid year",noncase))
    try:app._save_learning_label_for_file("anything.pdf",noncase)
    except ValueError:pass
    else:raise AssertionError(("learning revision accepted invalid year",noncase))
try:app._save_learning_example({"path":Path("anything.pdf")},"08-44")
except OSError:pass  # correct prefix; blocked only because learning drive is absent
else:raise AssertionError("expected share-unavailable protection")
print("PASS invalid years blocked before learning samples or corrections are saved")

# Other established functionality is still present in shipped source.
assert 'self.year_var.set(REQUIRED_CASE_YEAR)' in source
assert 'saved_case_year_rejected' in source
assert 'def _apply_sequence_consensus(self):' in source
assert 'def _make_scan_preview(' in source
assert 'def _scan_progress_update(' in source
assert 'text="REVIEW SELECTED"' in source
assert 'os.utime(destination, None)' in source
assert 'dimension_px=int(round(3*72*2.2))' in source
for function in ("_pending_learning_examples","_maybe_train_trocr"):
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==function)
    assert "CASE_RE.fullmatch" in ast.get_source_segment(source,node)
print("PASS V5.2.6 strict-08 validation and established UI/learning/scan checks")
