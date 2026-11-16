"""Unit tests of exact published MFCase V5.2.5 code: 08-44 conflicts + live progress."""
import ast,base64,hashlib,io,re,zipfile
from pathlib import Path
from types import SimpleNamespace
root=Path("release-assets")
data=base64.b64decode(root.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(data).hexdigest()==root.joinpath("SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(data)) as z:
    app=z.read("case_number_ocr.py").decode()
nodes={n.name:n for n in ast.walk(ast.parse(app)) if isinstance(n,ast.FunctionDef)}
names={"_resolve_ocr_alternates","_diag_field_filter","_scan_progress_update",
       "_sequence_case_number","_get_auto_threshold","_is_corner_case_rect"}
assert names<=nodes.keys(),names-nodes.keys()
klass=ast.ClassDef(name="Fixture",bases=[],keywords=[],
    body=[nodes[n] for n in sorted(names)],decorator_list=[])
ast.fix_missing_locations(klass)
env={"re":re,"Path":Path,"tk":SimpleNamespace(TclError=Exception),
     "normalize_case_number":lambda x: str(x or "").strip(),
     "CASE_RE":re.compile(r"\d{1,2}-\d{1,7}")}
exec(compile(ast.Module(body=[klass],type_ignores=[]),"<shipped methods>","exec"),env)

class C:
    def __init__(self):self.val={}
    def configure(self,**kw):self.val.update(kw)
    def config(self,**kw):self.val.update(kw)
    def __setitem__(self,name,value):self.val[name]=value
    def __getitem__(self,name):return self.val.get(name,0)

class App(env["Fixture"]):
    def __init__(self):
        self.scan_year_prefix="08"
        self.events=[]
        self.progress=C()
        self.scan_progress_label=C()
        self.scan_progress_pct=C()
        self.scan_items=[(f"f{i}",{}) for i in range(4)]
    def _diag_hits(self,hits):
        return [{"number":h.get("number"),"score":h.get("score"),
                 "rect":h.get("rect"),"engines":h.get("engines")}
                for h in hits]
    def _diag_event(self,event,**kw):self.events.append((event,kw))
    def _year_prefix(self):return "08"

def hit(no,score,rect,engines,label=False):
    return {"number":no,"score":score,"rect":rect,
        "engines":engines,"label_context":label,"label_type":None}

app=App()
best=hit("08-44",99.8,[920,22,1010,58],["EasyOCR","TrOCR"])
alt_enhanced=hit("08-15",99.9,[922,22,1008,58],["EasyOCR-enhanced"])
alt_trocr=hit("08-81",98.9,[919,21,1014,61],["TrOCR"])
alt_other_field=hit("08-25",99.9,[620,156,700,192],["EasyOCR"])
wrong_year=hit("29-44",100,[921,22,1010,58],["EasyOCR"])
filtered=app._diag_field_filter([best,alt_enhanced,alt_trocr,
                                  alt_other_field,wrong_year],"fallback")
assert "29-44" not in [x["number"] for x in filtered]
retained=app._resolve_ocr_alternates(filtered,"fallback",best)
assert {h["number"] for h in retained}=={"08-44"},retained
res=[x for x in app.events if x[0]=="case_conflict_resolution"][-1][1]
assert {x["reason"] for x in res["ignored"]}=={
    "enhanced_or_trocr_variant_of_primary_box",
    "different_unlabeled_location_not_case_field"},res
print("PASS screenshot-like 08-44 99.8 vs 08-15, 08-81 and unrelated 08-25 resolves to case number field")

# A genuine independent and close competing case read remains manual review.
real_conflict=hit("08-45",99.8,[932,23,1020,59],["EasyOCR"],True)
retained=app._resolve_ocr_alternates([best,real_conflict],"incident",best)
assert {h["number"] for h in retained}=={"08-44","08-45"},retained
print("PASS two equally reliable physically plausible case numbers remain conflicting")

# The 95% threshold is a confidence floor, not forced equality to 100%.
partial=hit("08-44",95.0,[920,22,1010,58],["EasyOCR"])
retained=app._resolve_ocr_alternates([partial,alt_trocr],"fallback",partial)
assert [h["number"] for h in retained]==["08-44"],retained
low=hit("08-44",94.9,[920,22,1010,58],["EasyOCR"])
assert app._resolve_ocr_alternates([low,alt_trocr],"fallback",low)==[low,alt_trocr]
print("PASS 95.0 qualifies for disambiguation; 94.9 remains manual")

# Fallback sequence anchor supports the screenshot's 99.8% correct read.
rec={"candidate":"08-44","score":99.8,"scan_source":"fallback",
     "snippet":(0,SimpleNamespace(shape=(2000,1400,3)),"08-44",[1220,22,1340,58],[]),
     "scan_ambiguous":False,"sequence_conflict":False,
     "sequence_excluded":False,"sequence_inferred":False}
assert app._sequence_case_number(rec)=="08-44"
rec["score"]=94.9
assert app._sequence_case_number(rec) is None
print("PASS correct 08-44 at 99.8 becomes an independent range anchor")

app._scan_progress_update(0,"SCANNING")
assert app.scan_progress_pct.val["text"]=="0%"
app._scan_progress_update(2,"SCANNING")
assert app.progress.val["value"]==2
assert app.scan_progress_pct.val["text"]=="50%"
assert "2 of 4" in app.scan_progress_label.val["text"]
app._scan_progress_update(4,"OCR COMPLETE — SENDING")
assert app.scan_progress_pct.val["text"]=="100%"
assert "SENDING" in app.scan_progress_label.val["text"]
print("PASS determinate progress 0/4 => 2/4 (50%) => 4/4 (100%), transfer stage")
print("PASS ALL V5.2.5 OCR AND PROGRESS TESTS")
