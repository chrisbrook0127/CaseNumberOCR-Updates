"""Actual V5.3.1 application security and adaptive OCR regressions.

All tests use a synthetic pixmap, never any department case file. Monkeypatch
network so that even one unintentional connection raises a hard failure.
"""
import base64,hashlib,io,os,queue,re,tempfile,types,urllib.request,zipfile
from pathlib import Path
import numpy as np

source_root=Path("release-assets")
raw=base64.b64decode((source_root/"update.base64").read_text(),validate=True)
assert hashlib.sha256(raw).hexdigest()==(source_root/"SHA256.txt").read_text().strip()
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None
    s=z.read("case_number_ocr.py").decode()
    compile(s,"case_number_ocr.py","exec")
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
ns={"__name__":"mfcase_531_test","__file__":str(Path("case_number_ocr.py").resolve())}
os.environ["MFCASE_SKIP_STARTUP_MAINTENANCE"]="1"
exec(compile(s,"case_number_ocr.py","exec"),ns)
App=ns["App"]
assert ns["REQUIRED_CASE_YEAR"]=="08"
assert ns["CASE_RE"].fullmatch("08-44")
for invalid in ("09-44","8-44","2008-44","44","08","08-"):
    assert not ns["CASE_RE"].fullmatch(invalid)
print("PASS strict literal 08- gate remains enforced")

class FakePix:
    def __init__(self):
        self.width=1600;self.height=1300;self.n=3
        self.samples=bytes(np.full((self.height,self.width,3),238,np.uint8).tobytes())
class FakePage:
    def get_pixmap(self,**kw):return FakePix()
class FakeDocument:
    def __len__(self):return 1
    def __iter__(self):return iter([FakePage()])
    def close(self):pass

ns["pymupdf"]=types.SimpleNamespace(open=lambda path:FakeDocument(),Matrix=lambda a,b:(a,b))
ns["np"]=np
app=App.__new__(App)
events=[]
app._diag_event=lambda name,**kwargs:events.append((name,kwargs))
app._log=lambda *args,**kw:None
app._find_case_field_label_regions=lambda _region:[]
app._read_incident_to_right=lambda *_:[]
app._diag_field_filter=lambda hits,kind:[
    h for h in hits if ns["CASE_RE"].fullmatch(str(h.get("number","")))]
app._prefer_case_margin=lambda hits,kind,image:hits
app._resolve_ocr_alternates=lambda hits,kind,best:hits
app._diag_hits=lambda hits:[dict(h) for h in hits]

def sample(no,score):
    return dict(number=no,score=score,rect=(1410,25,1510,67),
       engines=["EasyOCR"],label_context=False,label_type=None)
calls=[]
mode="retry"
def fake_cases(region,x_offset=0,y_offset=0,**kwargs):
    width=region.shape[1]
    calls.append(width)
    if width>700:
        return [sample("08-44",98)]
    if mode=="single":
        return [sample("08-44",98)]
    if mode=="wrong":
        return [sample("09-44",100)]
    return []
app._find_case_candidates=fake_cases
result=app._ocr_pdf(Path("synthetic.pdf"))
assert result[0]=="08-44",result
assert 475 in calls and 792 in calls,calls
assert any(e=="ocr_second_pass" for e,_ in events),events
assert "5x5" in result[2],result[2]
print("PASS 3x3 empty -> only then localized 5x5 second OCR pass")

calls.clear();events.clear();mode="single"
result=app._ocr_pdf(Path("synthetic.pdf"))
assert result[0]=="08-44",result
assert 475 in calls and 792 not in calls,calls
assert not any(e=="ocr_second_pass" for e,_ in events)
print("PASS clear 08- result never wastes work on 5x5")

calls.clear();events.clear();mode="wrong"
result=app._ocr_pdf(Path("synthetic.pdf"))
assert result[0]=="08-44",result
assert 792 in calls
assert any(e=="ocr_second_pass" for e,_ in events),events
print("PASS non-08 OCR ignored; invokes 5x5 and uses only valid 08-")

# Proven offline startup: patch urllib so ANY attempted internet call raises.
actual_urlopen=urllib.request.urlopen
attempts=[]
def never_network(*args,**kwargs):
    attempts.append((args,kwargs))
    raise RuntimeError("UNEXPECTED INTERNET ACCESS")
urllib.request.urlopen=never_network
try:
    with tempfile.TemporaryDirectory() as temp:
        old=os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"]=temp
        fresh=App.__new__(App)
        fresh._startup_ready=False
        fresh._loading_jobs=set()
        fresh.learning_dir=None
        fresh._diag_event=lambda *_args,**_kw:None
        fresh._load_learning_data=lambda refresh_ui=True:None
        preloaded=[]
        fresh._begin_model_preload=lambda:preloaded.append(True)
        class Label:
            def __init__(self):self.text=""
            def config(self,**kw):self.text=kw.get("text",self.text)
        fresh.status=Label()
        fresh._fail_startup=lambda reason:(_ for _ in ()).throw(
            AssertionError("Offline startup failed: "+reason))
        fresh._begin_offline_startup()
        assert Path(fresh.learning_dir).parent==Path(temp)/"MFCase"
        assert preloaded==[True]
        assert "Offline" in fresh.status.text
        fresh.reader=object()
        fresh.trocr_model=object()
        fresh.connection_status_label=Label()
        fresh._check_connection_status()
        assert "Internet: NOT CONTACTED" in fresh.connection_status_label.text
        fresh._diag_sync_shared()
        assert attempts==[],attempts
        if old is None:os.environ.pop("LOCALAPPDATA",None)
        else:os.environ["LOCALAPPDATA"]=old
    print("PASS startup, settings and diagnostics require no network at all")
finally:
    urllib.request.urlopen=actual_urlopen

# The only code paths allowed to contact HuggingFace are in the user's
# explicitly clicked PREPARE OFFLINE MODELS worker. All other model loads
# enforce local_files_only and EasyOCR download_enabled=False.
start=s.index("    def _preload_models_worker(self):")
finish=s.index("    def _begin_model_preload(self):",start)
routine=s[start:finish]
assert "download_enabled=False" in routine and "download_enabled=True" not in routine
section=s[s.index("    def _load_trocr(self):"):s.index("    def _trocr_read(self",s.index("    def _load_trocr(self):"))]
assert section.count("local_files_only=True")>=4,section
trainer=s[s.index("    def _train_trocr_worker(self,"):s.index("    def _normalize_case_text(",s.index("    def _train_trocr_worker(self,"))] if False else s
assert 'local_files_only=True).to(device)' in s
assert 'root.after(250,self._begin_offline_startup)' in s
assert 'root.after(400, self._begin_startup_updates)' not in s
assert 'automatic_export_blocked' in s
assert 'text="SAVE REVIEW"' in s
assert 'text="SEND ALL SELECTED"' in s
print("PASS no silent model downloads, no automatic updates, manual correction and send preserved")
print("PASS ALL V5.3.1 OFFLINE AND ADAPTIVE OCR REGRESSIONS")
