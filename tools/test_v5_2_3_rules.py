"""Exercise actual V5.2.3 OCR decisions (not replica implementations)."""
import ast,base64,hashlib,io,queue,re,tempfile,zipfile
from pathlib import Path
from types import SimpleNamespace

r=Path("release-assets")
zipbytes=base64.b64decode((r/"update.base64").read_text(),validate=True)
assert hashlib.sha256(zipbytes).hexdigest()==(r/"SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(zipbytes)) as zipf:
    source=zipf.read("case_number_ocr.py").decode()
tree=ast.parse(source)
functions={n.name:n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
names={"_poll","_get_auto_threshold","_auto_label_block_reason",
       "_sequence_case_number","_sequence_index","_sequence_neighbors",
       "_apply_sequence_consensus","_eligible_for_kurt",
       "_schedule_auto_export","_run_scheduled_auto_export"}
assert names<=set(functions)
base_class=ast.ClassDef(name="Base",bases=[],keywords=[],
                        body=[functions[name] for name in sorted(names)],decorator_list=[])
ast.fix_missing_locations(base_class)
env={"queue":queue,"Path":Path,"re":re,
     "normalize_case_number":lambda value:str(value or "").strip(),
     "CASE_RE":re.compile(r"\d{1,2}-\d{1,7}")}
exec(compile(ast.Module(body=[base_class],type_ignores=[]),
             "<actual-ocr-functions>","exec"),env)
Base=env["Base"]

class MockVar:
    def __init__(self,state=True):self.state=state
    def get(self):return self.state
class MockTree:
    def exists(self,*args):return True
    def delete(self,*args):pass
class MockStatus:
    def config(self,**kw):pass

class OCR(Base):
    def __init__(self,root,path,score,read,source_kind,ambiguous=False,enabled=True,localized=True):
        self.scanned=[]
        self.scan_year_prefix="08"
        self.scan_items=[("i",{})]
        self.records={"i":{"path":path,"status":"Not scanned","candidate":"",
                           "scan_source":"unknown","score":0.0,"exported":False}}
        self.queue=queue.Queue()
        self.root=SimpleNamespace(after=lambda *args:None)
        self.progress={}
        self.status=MockStatus()
        self.tree=MockTree()
        self.auto_label_var=MockVar(enabled)
        self._run_outcomes={}
        self.diags=[]
        self._move_to_completed=lambda iid,*a,**k:self.scanned.append(iid)
        self._refresh_review_controls=lambda:None
        self._update_completed_count=lambda:None
        self._update_scan_file_count=lambda:None
        self._refresh_scan_button=lambda:None
        self._refresh_rescan_button=lambda:None
        self._refresh_scan_checkbox_heading=lambda:None
        self._save_state=lambda:None
        self._insert_scan_row=lambda *a,**k:None
        self._diag_event=lambda *args,**kw:self.diags.append((args,kw))
        self._year_prefix=lambda:"08"
        if source_kind=="incident":
            note="Case field right-of-label full number — 3x3 labeled printed OCR"
        elif source_kind=="case":
            note="CASE NUMBER field full number — 3x3 labeled printed OCR"
        elif source_kind=="header":
            note="Upper-right header 3x3 handwritten candidate — verify 95%"
        elif source_kind=="fallback":
            note="Upper-right fallback within 3x3 — MANUAL REVIEW"
        else:
            note="No case number found in top-right 3x3 inches — MANUAL REVIEW"
        if ambiguous:
            note="3x3 case field has conflicting OCR reads: 08-33, 08-34 — MANUAL REVIEW"
        # A fallback must have a physical OCR bounding box in the 3x3 crop.
        evidence=(0,None,read,(500,20,575,55),[]) if localized else None
        self.queue.put(("row","i",read,evidence,note,source_kind=="header",
                        score,["EasyOCR"],False,1))
    def run(self):self._poll();return self.records["i"]

with tempfile.TemporaryDirectory() as d:
    path=Path(d)/"Image4877.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    tests=[
        ("incident",95.0,"08-33",False,True,True),
        ("case",95.0,"08-0035",False,True,True),
        ("incident",94.9,"08-33",False,True,False),
        ("header",95.0,"08-32",False,True,True),
        ("header",94.9,"08-32",False,True,False),
        ("fallback",100.0,"08-32",False,True,True),
        ("fallback",99.9,"08-32",False,True,False),
        ("fallback",100.0,"29-32",False,True,False),
        ("incident",100.0,"08-33",True,True,False),
        ("incident",100.0,"29-33",False,True,False),
        ("incident",100.0,"08-33",False,False,False),
    ]
    for kind,score,value,ambiguous,enabled,should_send in tests:
        model=OCR(None,path,score,value,kind,ambiguous,enabled)
        record=model.run()
        assert bool(model.scanned)==should_send,(
            kind,score,value,ambiguous,enabled,record)
        if should_send:assert record["confirmed"]==value
    print("PASS: 95% field/header primary rule, 94.9 rejection, wrong-year, genuine conflict and auto-off")
    unlocated=OCR(None,path,100.0,"08-44","fallback",False,True,localized=False)
    result=unlocated.run()
    assert not unlocated.scanned and not result.get("confirmed"),result
    print("PASS: unique localized 100% fallback auto-approved; unlocalized and wrong-year held")

class Sequence(Base):
    def __init__(self,folder,middle_read="",middle_ambiguous=False,
                 right_case="08-32",left_score=95.0,auto=True):
        self.folder=folder
        self.records={}
        self.scan_items=[]
        self._run_outcomes={}
        self.auto_label_var=MockVar(auto)
        self.scan_year_prefix="08"
        self.moved=[]
        self.notes=[]
        self._year_prefix=lambda:"08"
        self._move_to_completed=lambda iid,**kw:self.moved.append((iid,kw))
        self._diag_event=lambda *args,**kw:self.notes.append((args,kw))
        self._refresh_review_controls=lambda:None
        self._update_completed_count=lambda:None
        self._save_state=lambda:None
        self._schedule_auto_export=lambda:None
        self._insert_scan_row=lambda *a,**k:None
        self.completed_tree=MockTree()
        for idx,(name,value) in enumerate([
            ("Image4869.pdf","08-32"),("Image4870.pdf",middle_read),
            ("Image4872.pdf",right_case)]):
            filepath=folder/name
            filepath.write_bytes(b"%PDF-1.4\n%%EOF\n")
            record={
                "path":filepath,"candidate":value,
                "score":left_score if idx==0 else (95.0 if idx==2 else 0.0),
                "scan_source":"incident" if idx!=1 else "unknown",
                "scan_ambiguous":middle_ambiguous if idx==1 else False,
                "status":"No case number" if idx==1 else "Moved to COMPLETED",
                "exported":idx!=1,
                "manual_approved":idx!=1,"auto_labeled":idx!=1,
                "sequence_excluded":False,"sequence_conflict":False}
            iid="id"+str(idx)
            self.records[iid]=record
            self.scan_items.append((iid,record))

with tempfile.TemporaryDirectory() as tmp:
    folder=Path(tmp)
    scenarios=[
        ("",False,"08-32",95.0,True,True),
        ("08-33",False,"08-32",95.0,True,True),
        ("",True,"08-32",95.0,True,False),
        ("",False,"08-34",95.0,True,False),
        ("",False,"08-32",94.9,True,False),
        ("",False,"08-32",95.0,False,False),
    ]
    for middle,amb,right,score,enabled,expected in scenarios:
        model=Sequence(folder,middle,amb,right,score,enabled)
        result=model._apply_sequence_consensus()
        assert bool(model.moved)==expected,(middle,amb,right,score,enabled,result,model.records)
        if expected:
            rec=model.records["id1"]
            assert rec["confirmed"]=="08-32" and rec["sequence_inferred"] is True
            assert model.moved==[("id1",{"batch":True})]
    print("PASS: bounded range resolves weak mistaken OCR and blanks; preserves ambiguity, anchor disagreement and auto-off")

print("ALL PRIMARY AND FALLBACK REGRESSION CASES PASSED")


# Use the shipped eligibility and scheduling methods (not approximations)
# to verify that ALL COMPLETED rows, including manual approvals from prior
# runs, are dispatched only after OCR scans finish.
with tempfile.TemporaryDirectory() as d:
    folder=Path(d)
    a=folder/"Image4892.pdf"
    b=folder/"Image4893.pdf"
    c=folder/"Image4894.pdf"
    for path in (a,b,c):path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    tested=Base()
    tested.folder=folder
    tested.scan_year_prefix="08"
    tested.busy=True
    tested.trocr_training=False
    tested.export_busy=False
    tested._export_reschedule=False
    tested._auto_export_after=None
    tested.auto_label_var=MockVar(True)
    tested.records={
        "manual":{"path":a,"confirmed":"08-44","manual_approved":True,
                  "auto_labeled":False,"scan_ambiguous":True,"sequence_conflict":False,
                  "exported":False,"export_error":""},
        "machine":{"path":b,"confirmed":"08-44","manual_approved":True,
                   "auto_labeled":True,"scan_ambiguous":False,"sequence_conflict":False,
                   "exported":False,"export_error":""},
        "unverified":{"path":c,"confirmed":"","manual_approved":False,
                     "auto_labeled":False,"scan_ambiguous":False,"exported":False}}
    invoked=[]
    tested.root=SimpleNamespace(after=lambda delay,fn:invoked.append((delay,fn)) or "scheduled")
    sent=[]
    tested.export=lambda **kwargs:sent.append(kwargs)
    assert tested._eligible_for_kurt("manual",tested.records["manual"],automatic=True)
    assert tested._eligible_for_kurt("machine",tested.records["machine"],automatic=True)
    assert not tested._eligible_for_kurt("unverified",tested.records["unverified"],automatic=True)
    tested._schedule_auto_export()
    assert not invoked,"Nothing should transfer during an active scan"
    tested.busy=False
    tested._schedule_auto_export()
    assert len(invoked)==1 and invoked[0][0]>=0
    invoked[0][1]()
    assert sent==[{"automatic":True}]
    tested.records["manual"]["export_error"]="Access denied"
    assert not tested._eligible_for_kurt("manual",tested.records["manual"],automatic=True)
    print("PASS: all approved records auto-export after scan; no premature send and no endless failed-transfer retry")

# Check the shipped scan-done branch and training-completion branches
# actually call the approved-export scheduler after releasing busy locks.
poll=ast.get_source_segment(source,functions["_poll"])
assert poll.count("self._schedule_auto_export()")>=3,poll.count("self._schedule_auto_export()")
assert 'self.busy = False\n                    self._apply_sequence_consensus()' in poll
assert 'elif kind == "done":' in poll
print("PASS: scan-done and training-done scheduler hooks are present")
