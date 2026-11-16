"""Test *shipped* MFCase V5.2.6 handwritten 08-44 case selection over fax headers.

These tests extract code directly from the exact built release ZIP and call
its own OCR methods. No externally guessed case is written into an original
PDF. False fax-header evidence must not become a verified case number.
"""
import ast,base64,hashlib,io,re,zipfile
from pathlib import Path
import numpy as np

folder=Path("release-assets")
blob=base64.b64decode(folder.joinpath("update.base64").read_text(),validate=True)
assert hashlib.sha256(blob).hexdigest()==folder.joinpath("SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    source=z.read("case_number_ocr.py").decode()
tree=ast.parse(source)
names={"_select_primary_case_hit","_mfcase_header_field_candidates"}
module_nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
assert {n.name for n in module_nodes}==names
globals_={"re":re,"np":np,"REQUIRED_CASE_YEAR":"08"}
exec(compile(ast.Module(body=module_nodes,type_ignores=[]),"<published OCR selectors>","exec"),globals_)
select=globals_["_select_primary_case_hit"]
header=globals_["_mfcase_header_field_candidates"]

def hit(number,score,bbox,engines=("EasyOCR",)):
    return {"number":number,"score":score,"rect":bbox,
            "engines":list(engines),"label_context":False,"label_type":None}
shape=(2000,1400,3)
fax=hit("08-15",91,(1010,34,1150,80))
fax2=hit("08-81",100,(1020,35,1152,82),("TrOCR",))
hand=hit("08-44",88,(1220,39,1330,104))
items=[fax,fax2,hand]
assert select(items,"fallback",shape) is hand
assert header(items,shape)==[hand],header(items,shape)
assert header([fax],shape)==[],header([fax],shape)
assert select([fax],"fallback",shape) is fax  # remains review, not auto-approved
print("PASS rightmost handwritten 08-44 (88%) selected ahead of fax header (91-100%)")
print("PASS fax header alone cannot satisfy true header field location")

method_names={"_is_corner_case_rect","_prefer_case_margin","_sequence_case_number",
              "_find_case_candidates"}
cls_nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in method_names]
assert {n.name for n in cls_nodes}==method_names
cls=ast.ClassDef(name="HarnessMethods",bases=[],keywords=[],body=cls_nodes,decorator_list=[])
ast.fix_missing_locations(cls)
globals_.update({
    "normalize_case_number":lambda val:str(val or "").strip(),
    "CASE_RE":re.compile(r"(?<!\d)(08-\d{1,7})(?!\d)")})
exec(compile(ast.Module(body=[cls],type_ignores=[]),"<published case methods>","exec"),globals_)

class Reader:
    def __init__(self,first):
        self.first=first
        self.calls=0
    def readtext(self,image,**kwargs):
        self.calls+=1
        return self.first if self.calls==1 else []

def box(x0,y0,x1,y1):
    return [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
class Harness(globals_["HarnessMethods"]):
    def __init__(self):
        self.events=[]
        self.scan_year_prefix="08"
        self.reader=None
        self.trocr_calls=0
    def _year_prefix(self):return "08"
    def _diag_hits(self,hits):
        return [{"number":h.get("number"),"score":h.get("score"),
                 "rect":h.get("rect"),"engines":h.get("engines")}
                for h in hits]
    def _diag_event(self,event,**data):self.events.append((event,data))
    def _log(self,*args):pass
    def _trocr_read_scored(self,img):
        self.trocr_calls+=1
        return "08-44",96.0

h=Harness()
filtered=h._prefer_case_margin(items,"fallback",np.full(shape,255,np.uint8))
assert [x["number"] for x in filtered]==["08-44"],filtered
assert h._is_corner_case_rect(hand["rect"],np.full(shape,255,np.uint8))
assert not h._is_corner_case_rect(fax["rect"],np.full(shape,255,np.uint8))
assert h._prefer_case_margin([fax],"fallback",np.full(shape,255,np.uint8))==[fax]
print("PASS handwritten margin filters unrelated 08-15 and 08-81 from OCR conflict calculation")

image=np.full((475,550,3),255,np.uint8)
# Simulates actual EasyOCR raw text, where fax pagination is printed
# '122 P.005/015', not a true case number with an 08- prefix.
h.reader=Reader([
    (box(180,35,340,70),"122 P.005/015",.91),
    (box(410,36,515,90),"08-44",.88)])
candidates=h._find_case_candidates(image,x_offset=850,run_trocr=True)
assert {x["number"] for x in candidates}=={"08-44"},candidates
assert h.trocr_calls==1,h.trocr_calls
assert h.reader.calls>1
print("PASS real candidate parser ignores fax slash and keeps handwritten YY-NN")

h.reader=Reader([(box(180,35,340,70),"122 P.005/015",.91)])
h.trocr_calls=0
assert h._find_case_candidates(image,x_offset=850,run_trocr=True)==[]
assert h.trocr_calls==0
print("PASS fax-only printed P.005/015 can NEVER synthesize 08-15 by TrOCR")

h.scan_year_prefix="08"
marginsnip=(0,np.zeros(shape,np.uint8),"",[1220,39,1330,104],[])
faxsnip=(0,np.zeros(shape,np.uint8),"",[1010,34,1150,80],[])
base={"score":88.0,"candidate":"08-64","scan_source":"fallback",
      "snippet":marginsnip,"scan_ambiguous":False,
      "sequence_conflict":False,"sequence_excluded":False,
      "sequence_inferred":False}
assert h._sequence_case_number(base) is None
base["candidate"]="08-44";base["score"]=95.
assert h._sequence_case_number(base)=="08-44"
base["snippet"]=faxsnip
assert h._sequence_case_number(base) is None
print("PASS <95 OCR awaits range fallback; >=95 near true margin can anchor; fax cannot")
print("ALL V5.2.6 UPPER-RIGHT CASE-PRIORITY TESTS PASSED")
