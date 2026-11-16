"""Regression tests for actual MFCase V5.2.5 sequence range implementation."""
import ast,base64,hashlib,io,re,tempfile,zipfile
from types import SimpleNamespace
from pathlib import Path

root=Path("release-assets")
blob=base64.b64decode(root.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(blob).hexdigest()==root.joinpath("SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    code=z.read("case_number_ocr.py").decode()
tree=ast.parse(code)
names={"_sequence_case_number","_apply_sequence_consensus","_auto_label_block_reason",
       "_get_auto_threshold","_is_corner_case_rect"}
nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
assert {n.name for n in nodes}==names
cls=ast.ClassDef(name="Base",bases=[],keywords=[],body=nodes,decorator_list=[])
ast.fix_missing_locations(cls)
env={"Path":Path,"re":re,
     "normalize_case_number":lambda s:str(s or "").strip(),
     "CASE_RE":re.compile(r"\d{1,2}-\d{1,7}")}
exec(compile(ast.Module(body=[cls],type_ignores=[]),"<real-methods>","exec"),env)
Base=env["Base"]

class Var:
    def __init__(self,on=True):self.on=on
    def get(self):return self.on

class Harness(Base):
    def __init__(self, folder, rows, enabled=True):
        self.folder=folder
        self.scan_year_prefix="08"
        self.auto_label_var=Var(enabled)
        self.records={}
        self.scan_items=[]
        self.moved=[]
        self.events=[]
        self.scheduled=0
        for i,(name,value,score,source,exported,ambiguous,manual) in enumerate(rows):
            iid="i"+str(i)
            path=folder/name
            path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            snippet=(0,SimpleNamespace(shape=(2000,1400,3)),"", (1220,22,1330,70), []) if value else None
            rec={
                "path":path,"candidate":value,"score":score,
                "scan_source":source,"snippet":snippet,
                "exported":exported,"manual_approved":manual,
                "auto_labeled":(score>=95 and source!="unknown"),
                "scan_ambiguous":ambiguous,"sequence_conflict":False,
                "sequence_excluded":False,"sequence_inferred":False,
                "status":"Auto-approved" if exported else "Manual review"}
            self.records[iid]=rec
            self.scan_items.append((iid,rec))
    def _year_prefix(self):return "08"
    def _diag_event(self,event,**kw):self.events.append((event,kw))
    def _move_to_completed(self,iid,**kw):self.moved.append((iid,kw))
    def _refresh_review_controls(self):pass
    def _update_completed_count(self):pass
    def _save_state(self):pass
    def _schedule_auto_export(self):self.scheduled+=1
    def _insert_scan_row(self,*a,**kw):pass

def row(i,value,score=0,source="unknown",exported=False,ambiguous=False,manual=False):
    return (f"Image{4892+i}.pdf",value,score,source,exported,ambiguous,manual)

with tempfile.TemporaryDirectory() as d:
    directory=Path(d)
    sample=[
        row(0,"08-44",100,"fallback",True,False,True),
        row(1,"08-64",75.3,"fallback"),
        row(2,"",0,"unknown"),
        row(9,"08-44",100,"fallback",True,False,True)]
    app=Harness(directory,sample)
    assert app._sequence_case_number(app.records["i0"])=="08-44"
    assert app._sequence_case_number(app.records["i3"])=="08-44"
    assert app._apply_sequence_consensus()==2
    assert [iid for iid,_ in app.moved]==["i1","i2"],app.moved
    assert app.records["i1"]["candidate"]=="08-44"
    assert app.records["i1"]["sequence_original_candidate"]=="08-64"
    assert app.records["i1"]["sequence_original_score"]==75.3
    assert app.records["i2"]["confirmed"]=="08-44"
    assert app.records["i2"]["sequence_original_candidate"]==""
    assert all(app.records[i]["sequence_inferred"] for i in ("i1","i2"))
    print("PASS uploaded example: 08-44 / wrong 08-64 at 75.3 / blank / 08-44 inferred all middle")

    five=[row(0,"08-99",100,"fallback",True,False,True)]
    five += [row(i,"08-64" if i%2 else "",74 if i%2 else 0,
                 "fallback" if i%2 else "unknown") for i in range(1,6)]
    five += [row(9,"08-99",100,"fallback",True,False,True)]
    app=Harness(directory,five)
    assert app._apply_sequence_consensus()==5
    assert [iid for iid,_ in app.moved]==["i1","i2","i3","i4","i5"]
    assert all(app.records["i"+str(n)]["candidate"]=="08-99" for n in range(1,6))
    print("PASS 5 sequential scanned PDFs between matching end anchors (filename gaps allowed)")

    # Do not overwrite independent 95%-plus disagreement. A different
    # high-confidence middle value creates a boundary between ranges.
    conflict=[row(0,"08-44",100,"fallback",True,False,True),
              row(1,"08-44",40,"fallback"),
              row(2,"08-45",99,"incident",True,False,True),
              row(3,"08-44",28,"fallback"),
              row(4,"08-44",100,"fallback",True,False,True)]
    app=Harness(directory,conflict)
    assert app._apply_sequence_consensus()==0
    print("PASS genuine high-confidence conflicting anchor prevents bad span")

    # A record flagged for OCR ambiguity is not silently overwritten.
    app=Harness(directory,[row(0,"08-44",100,"fallback",True,False,True),
           row(1,"08-64",43,"fallback",False,True),
           row(2,"08-44",100,"fallback",True,False,True)])
    assert app._apply_sequence_consensus()==0
    print("PASS ambiguous middle remains in review")

    # A reviewer's explicit previous confirmation is protected.
    app=Harness(directory,[row(0,"08-44",100,"fallback",True,False,True),
           row(1,"08-64",40,"fallback",False,False,True),
           row(2,"08-44",100,"fallback",True,False,True)])
    app.records["i1"]["auto_labeled"]=False
    assert app._apply_sequence_consensus()==0
    print("PASS manual approval never silently overwritten")

    # Inferred readings are not promoted into independent anchors later.
    app=Harness(directory,[row(0,"08-44",100,"fallback",True,False,True),
           row(1,"08-64",44,"fallback"),
           row(2,"08-44",100,"fallback",True,False,True)])
    assert app._apply_sequence_consensus()==1
    assert app._sequence_case_number(app.records["i1"]) is None
    print("PASS inferred values cannot cause cascading inference")

    # No second anchor / disabled auto mode -> no action.
    app=Harness(directory,[row(0,"08-44",100,"fallback",True,False,True),
           row(1,"08-64",30,"fallback")])
    assert app._apply_sequence_consensus()==0
    app=Harness(directory,sample,enabled=False)
    assert app._apply_sequence_consensus()==0
    print("PASS no send without two anchors or when auto-confirm is off")

    # Leading-zero variants equal for confidence boundary matching.
    app=Harness(directory,[row(0,"08-0044",100,"fallback",True,False,True),
         row(1,"",0),row(2,"08-44",100,"fallback",True,False,True)])
    assert app._apply_sequence_consensus()==1
    assert app.records["i1"]["confirmed"]=="08-0044"
    print("PASS normalized comparison with preserved original evidence")
print("PASS ALL scan-range regression scenarios")
