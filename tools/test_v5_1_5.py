"""Actual shipped code regression: sorted read order, gaps and 3-read evidence."""
from pathlib import Path
import ast,base64,hashlib,io,re,tempfile,zipfile

folder=Path("release-assets")
raw=base64.b64decode((folder/"update.base64").read_text(),validate=True)
assert hashlib.sha256(raw).hexdigest()==(folder/"SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    code=z.read("case_number_ocr.py").decode()
tree=ast.parse(code)
names={"_sequence_case_number","_sequence_index",
       "_sequence_neighbors","_apply_sequence_consensus","_natural_pdf_sort_key"}
methods=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
assert {n.name for n in methods}==names
# Exercise the full released implementations, not replicas.
c=ast.ClassDef(name="Harness",bases=[],keywords=[],body=methods,decorator_list=[])
ast.fix_missing_locations(c)
ns={"Path":Path,"re":re,
    "normalize_case_number":lambda s:str(s or "").strip(),
    "CASE_RE":re.compile(r"\d{1,2}-\d{1,7}")}
exec(compile(ast.Module(body=[c],type_ignores=[]),"<release-method-test>","exec"),ns)
Base=ns["Harness"]

class Tree:
    def exists(self,*a):return False
    def delete(self,*a):pass
class Auto:
    def get(self):return True

class Mock(Base):
    def __init__(self, directory, names, cases, scores=None, sources=None):
        scores=scores or [100.0]*len(names)
        sources=sources or ["header"]*len(names)
        self.records={}
        self.scan_items=[]
        self.completed_tree=Tree()
        self.tree=Tree()
        self.auto_label_var=Auto()
        self.moved=[]
        self.events=[]
        self.stages=[]
        for i,(name,case,score,source) in enumerate(zip(names,cases,scores,sources)):
            path=directory/name
            path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            iid="id"+str(i)
            rec={"path":path,"status":"Unlabeled handwritten",
                 "candidate":case,"score":score,
                 "scan_source":source,
                 "scan_ambiguous":False,
                 "sequence_inferred":False,
                 "sequence_excluded":False,
                 "auto_labeled":False,"manual_approved":False}
            self.records[iid]=rec
            self.scan_items.append((iid,rec))
        # The application sorts its scan items naturally before OCR.
        self.scan_items.sort(key=lambda pair: self._natural_pdf_sort_key(pair[1]["path"]))
    def _get_auto_threshold(self):return 90.0
    def _diag_event(self,event,**details):self.events.append((event,details))
    def _move_to_completed(self,iid,batch=False):self.moved.append((iid,batch))
    def _insert_scan_row(self,*args,**kw):pass
    def _refresh_review_controls(self):pass
    def _update_completed_count(self):pass
    def _save_state(self):pass
    def _schedule_auto_export(self):self.stages.append("auto_export_scheduled")

def scenario(names,cases,scores=None,sources=None):
    with tempfile.TemporaryDirectory() as temp:
        m=Mock(Path(temp),names,cases,scores,sources)
        m._apply_sequence_consensus()
        return { "approved":sorted(int(iid[2:]) for iid,_ in m.moved),
                 "status":[m.records["id"+str(i)]["status"] for i in range(len(names))],
                 "events":m.events,"stages":m.stages,
                 "scan_names":[r["path"].name for _,r in m.scan_items] }

names=["Image4869.pdf","Image4870.pdf","Image4872.pdf","Image4873.pdf"]
test=scenario(names,["08-32"]*4)
assert test["approved"]==[0,1,2,3],test
assert test["scan_names"]==names,test
assert any(x[0]=="approval_decision" for x in test["events"])
print("PASS: four 100% 08-32 reads, filename gap, all auto-approved")

test=scenario(["Image1.pdf","Image2.pdf","Image3.pdf"],["08-32"]*3)
assert test["approved"]==[0,1,2],test
test=scenario(["Image1.pdf","Image2.pdf"],["08-32"]*2)
assert test["approved"]==[],test
print("PASS: three independent reads required, two insufficient")

names=[f"Image{i}.pdf" for i in [1,2,3,4,5,6]]
cases=["08-32"]*3+["08-33"]*3
test=scenario(names,cases)
assert test["approved"]==list(range(6)),test
print("PASS: protect case boundaries, approve both three-file runs")

test=scenario(["Image1.pdf","Image2.pdf","Image3.pdf"],["08-32","08-33","08-32"])
assert test["approved"]==[],test
assert "MANUAL REVIEW" in test["status"][1],test
print("PASS: conflicting middle read blocked")

test=scenario(["Image1.pdf","Image2.pdf","Image3.pdf"],["08-32","","08-32"])
assert test["approved"]==[],test
test=scenario(["Image1.pdf","Image2.pdf","Image3.pdf"],["08-32"]*3,
              scores=[100,94.9,100])
assert test["approved"]==[],test
test=scenario(["Image1.pdf","Image2.pdf","Image3.pdf"],["08-32"]*3,
              sources=["header","fallback","header"])
assert test["approved"]==[],test
print("PASS: unreadable, below-95% and fallback candidates cannot anchor")

test=scenario(["Image1.pdf","Image2.pdf","Image3.pdf"],["08-0032","8-32","08-32"])
assert test["approved"]==[0,1,2],test
print("PASS: compare numerically equivalent case numbers without deleting leading zeros in OCR audit")

test=scenario(["Image10.pdf","Image2.pdf","Image1.pdf"],["08-32"]*3)
assert test["scan_names"]==["Image1.pdf","Image2.pdf","Image10.pdf"],test
assert test["approved"]==[0,1,2],test
print("PASS: natural filename sort determines scan read order")

# Extra safeguard: a record marked as a previous inferred result must NOT
# be used as independent evidence for a new approval.
with tempfile.TemporaryDirectory() as tmp:
    m=Mock(Path(tmp),["Image1.pdf","Image2.pdf","Image3.pdf"],["08-32"]*3)
    m.records["id1"]["sequence_inferred"]=True
    m._apply_sequence_consensus()
    assert len(m.moved)==0
print("PASS: inferred results are not independent OCR anchors")
print("TOTAL: 10 grouped regression scenarios")
