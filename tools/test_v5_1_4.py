"""Small dependency-free regression suite for MFCase 5.1.4 release logic.

Checks actual methods extracted from the released application, using synthetic
page buffers and file fixtures rather than any case records or learning data.
"""
from pathlib import Path
import ast
import base64
import datetime
import hashlib
import io
import os
import re
import tempfile
import time
import types
import zipfile

root=Path("release-assets")
data=base64.b64decode(root.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(data).hexdigest()==root.joinpath("SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(data)) as z:
    code=z.read("case_number_ocr.py").decode()
tree=ast.parse(code)

def extract(name):
    matches=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(matches)==1,(name,len(matches))
    return matches[0]

# Original method extracted verbatim for testing in an isolated harness class.
def method_class(name,env):
    node=extract(name)
    section=ast.get_source_segment(code,node)
    assert section and section.lstrip().startswith("def ")
    obj={}
    exec("class Harness:\n    "+section+"\n",env,obj)
    return obj["Harness"]

normalize=lambda x:str(x or "").strip()

# Test match, selection, and crop behavior with real numpy arrays but a fake
# OCR reader. This avoids downloading a multi-GB model for unit tests.
import numpy as np

class Pix:
    def __init__(self,arr):
        self.height,self.width,self.n=arr.shape
        self.samples=arr.tobytes()
class Page:
    def __init__(self,p): self.p=p
    def get_pixmap(self,**kwargs):return self.p
class Doc:
    def __init__(self,p):self.pages=[Page(p)]
    def __len__(self):return len(self.pages)
    def __iter__(self):return iter(self.pages)
    def close(self):pass
arr=np.full((1700,1320,3),255,np.uint8)
fake_doc=Doc(Pix(arr))
pm=types.SimpleNamespace(open=lambda path:fake_doc,Matrix=lambda x,y:(x,y))

def select_primary(hits,kind,shape):
    return sorted(hits,key=lambda h:float(h.get("score") or 0),reverse=True)[0]

helper_env={"np":np,"re":re,"pymupdf":pm,"_select_primary_case_hit":select_primary,
            "INCIDENT_NUMBER_RE":re.compile(r"(\d{1,2}-\d{1,6})")}
helper={}
exec(compile(ast.Module(body=[extract("_mfcase_header_field_candidates")],type_ignores=[]),
             "header","exec"),helper_env,helper)
helper_env.update(helper)
OCRClass=method_class("_ocr_pdf",helper_env)
FilterClass=method_class("_diag_field_filter",{"re":re})
HitsClass=method_class("_diag_hits",{})
HitsClass._diag_hits=staticmethod(HitsClass._diag_hits)

class OCRHarness(OCRClass,FilterClass,HitsClass):
    def __init__(self,kind,score=100.0):
        self.kind=kind
        self.score=score
        self.labels_seen=[]
        self.candidates_seen=[]
        self.scan_year_prefix="08"
        self.events=[]
    def _year_prefix(self):return "08"
    def _diag_event(self,event,**kw):self.events.append((event,kw))
    def _log(self,*args):raise AssertionError(("unexpected OCR exception",args))
    def _find_case_field_label_regions(self,region):
        self.labels_seen.append(region.shape)
        if self.kind in ("labeled","mixed","true_conflict","leading_zeros"):
            return [(42,30,140,62,"incident")]
        return []
    def _read_incident_to_right(self,region,label):
        assert region.shape[0]<=476 and region.shape[1]<=476
        a={"number":"08-19","score":self.score,"rect":(150,30,250,65),
           "engines":["EasyOCR-incident"],"label_type":"incident"}
        if self.kind=="mixed":
            return [a,
                {"number":"2014","score":100,"rect":(140,50,180,70),"engines":["EasyOCR"]},
                {"number":"29-29","score":100,"rect":(150,30,250,65),"engines":["EasyOCR"]},
                {"number":"08-20","score":74,"rect":(150,30,250,65),"engines":["EasyOCR"]}]
        if self.kind=="true_conflict":
            return [a,{"number":"08-20","score":100,"rect":(150,30,250,65),"engines":["EasyOCR"]}]
        if self.kind=="leading_zeros":
            return [a,{"number":"08-0019","score":100,"rect":(150,30,250,65),"engines":["EasyOCR"]}]
        return [a]
    def _find_case_candidates(self,region,**kw):
        self.candidates_seen.append((region.shape,kw))
        assert region.shape[0]<=476 and region.shape[1]<=476
        # Full page region starts at x=1320-475=845.
        if self.kind=="header":
            return [{"number":"08-19","score":self.score,
                     "rect":(1140,15,1220,57),"engines":["TrOCR-header"]}]
        if self.kind=="body":
            return [{"number":"08-19","score":100.0,
                     "rect":(500,700,580,739),"engines":["EasyOCR"]}]
        return []

assert OCRHarness("labeled")._ocr_pdf("synthetic.pdf")[0]=="08-19"
assert OCRHarness("labeled")._ocr_pdf("synthetic.pdf")[4]==100.0
result=OCRHarness("empty")._ocr_pdf("synthetic.pdf")
assert result[0]=="" and "MANUAL REVIEW" in result[2]
result=OCRHarness("body")._ocr_pdf("synthetic.pdf")
assert result[0]=="" and "MANUAL REVIEW" in result[2]
result=OCRHarness("header",95.01)._ocr_pdf("synthetic.pdf")
assert result[0]=="08-19" and result[4]>=95 and "header" in result[2]
result=OCRHarness("header",94.99)._ocr_pdf("synthetic.pdf")
assert "MANUAL REVIEW" in result[2]
mixed=OCRHarness("mixed")._ocr_pdf("synthetic.pdf")
assert mixed[0]=="08-19" and "conflicting" not in mixed[2],mixed
conflicting=OCRHarness("true_conflict")._ocr_pdf("synthetic.pdf")
assert "conflicting" in conflicting[2] and "08-20" in conflicting[2],conflicting
equivalent=OCRHarness("leading_zeros")._ocr_pdf("synthetic.pdf")
assert "conflicting" not in equivalent[2],equivalent
print("PASS synthetic PDF regions: labeled priority, invalid 2014, wrong year, true conflict, zero equivalence, handwriting threshold")

# Sequence contract: the full function must approve a 95%+ unlabeled read
# ONLY if both independent neighbors are 08-19. Blank reads are never inferred.
SeqClass=method_class("_apply_sequence_consensus",{
    "normalize_case_number":normalize,
    "CASE_RE":re.compile(r"\d{1,2}-\d{1,7}")
})
class FakePath:
    def is_file(self):return True
    def __fspath__(self):return "/tmp/Image4845.pdf"
class FakeTree:
    def exists(self,x):return False
    def delete(self,x):pass
class FakeVar:
    def get(self):return True
class SequenceHarness(SeqClass):
    def __init__(self,number="08-19",score=95.0,kind="header",neighbors=("08-19","08-19"),ambiguous=False,auto=False):
        self.records={"middle":{
            "path":FakePath(),"status":"Ready for review","candidate":number,
            "score":score,"scan_source":kind,"scan_ambiguous":ambiguous,
            "confirmed":number if auto else "", "auto_labeled":auto,
            "manual_approved":auto}}
        self.neighbors=neighbors
        self.auto_label_var=FakeVar()
        self.completed_tree=FakeTree()
        self.moved=[]
        self.scheduled=False
    def _sequence_index(self):return {}
    def _sequence_neighbors(self,iid,index):
        if not self.neighbors:return None
        return (("left",self.neighbors[0]),("right",self.neighbors[1]))
    def _get_auto_threshold(self):return 90.0
    def _diag_event(self,*a,**kw):pass
    def _move_to_completed(self,iid,batch=False):self.moved.append((iid,batch))
    def _insert_scan_row(self,*a,**kw):pass
    def _refresh_review_controls(self):pass
    def _update_completed_count(self):pass
    def _save_state(self):pass
    def _schedule_auto_export(self):self.scheduled=True

def seq(number="08-19",score=95.,kind="header",neighbors=("08-19","08-19"),ambiguous=False,auto=False):
    h=SequenceHarness(number,score,kind,neighbors,ambiguous,auto)
    h._apply_sequence_consensus()
    return h
s=seq(score=95.0)
assert s.records["middle"]["confirmed"]=="08-19" and s.moved==[("middle",True)] and s.scheduled
assert not seq(score=94.99).moved
assert not seq(number="",score=100).moved
assert not seq(neighbors=None).moved
assert not seq(neighbors=("08-19","08-20")).moved
assert not seq(number="08-14").moved
assert seq(number="08-14").records["middle"]["sequence_conflict"] is True
assert not seq(ambiguous=True).moved
assert not seq(kind="fallback").moved
assert seq(kind="incident",score=83.0).moved
assert not seq(kind="incident",score=100,auto=True).moved
print("PASS sequencing: 95 accepted; 94.99/blank/one-sided/conflict/ambiguous/fallback rejected")

# Test actual PDF move method and timestamp, no PDF byte edits.
env={"os":os, "Path":Path}
exec(compile(ast.Module(body=[extract("_mfcase_export_case_name")],type_ignores=[]),
             "rename","exec"),{"re":re,"normalize_case_number":normalize},env)
ExportClass=method_class("_export_worker",env)
class Queue:
    def __init__(self):self.items=[]
    def put(self,x):self.items.append(x)
class ExportHarness(ExportClass):
    def __init__(self):self.queue=Queue();self.messages=[]
    def _log(self,*args):self.messages.append(args)
    def _diag_event(self,*a,**kw):pass

with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)
    source=p/"Image4815.pdf"
    content=b"%PDF-1.4\nMFCase file unchanged\n%%EOF\n"
    source.write_bytes(content)
    old_time=time.time()-86400*250
    os.utime(source,(old_time,old_time))
    h=ExportHarness()
    h._export_worker(p/"COMPLETED",[("test",source,"08-0019")])
    dest=p/"COMPLETED"/"08-19.pdf"
    assert dest.is_file() and dest.read_bytes()==content
    assert abs(dest.stat().st_mtime-time.time())<30
    assert not source.exists()
    assert h.queue.items[0][0]=="export_result" and h.queue.items[0][-1]==""
    assert h.queue.items[-1]==("export_done",)
    again=p/"Image4816.pdf";again.write_bytes(content)
    h._export_worker(p/"COMPLETED",[("test2",again,"08-0019")])
    assert (p/"COMPLETED"/"08-19 (1).pdf").is_file()
print("PASS transfer: original bytes preserved, Date modified refreshed, copy suffix retained")
print("TOTAL REGRESSION SCENARIOS: 26")
