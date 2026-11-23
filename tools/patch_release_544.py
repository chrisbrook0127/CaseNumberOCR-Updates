from pathlib import Path
import re,sys,hashlib
app=Path(sys.argv[1]).resolve()
p=app/"case_number_ocr.py"
s=p.read_text("utf-8")
assert re.search(r'self\.app_version\s*=\s*"5\.4\.[123]"',s), "Unexpected MFCase version"
def replace_method(src,name,patch):
    m=re.search(r'^    def '+re.escape(name)+r'\(',src,re.M)
    assert m,name
    nxt=re.search(r'^    def |^def |^class ',src[m.end():],re.M)
    assert nxt,name+' end'
    e=m.end()+nxt.start()
    return src[:m.start()]+patch(src[m.start():e])+src[e:]
def green(prev):
    return '''    def _quick_approve(self, iid):
        """Green check completes a single file; SEND FILES moves it."""
        if self.busy or self.export_busy or self.trocr_training:
            return
        commit=getattr(self,"_scan_case_commit",None)
        if commit:
            commit()
            if getattr(self,"_scan_case_editor",None) is not None:
                return
        record=self.records.get(iid)
        if not record or record.get("exported"):
            return
        value=record.get("case_draft") or record.get("confirmed") or record.get("candidate") or ""
        if not CASE_RE.fullmatch(normalize_case_number(value)):
            self.status.config(text="Enter a complete 08- case number before accepting.")
            self._start_scan_case_edit(iid)
            return
        class Value:
            def get(self): return value
        if self._save_completed_case_edit(iid,Value()):
            record.pop("case_draft",None)
            self._save_state()
            self._insert_scan_row(iid,record)

'''
s=replace_method(s,"_quick_approve",green)
def sequence(method):
    begin=next((method.find(x) for x in
        ("        ranges=[]","        # ONLY three consecutive files:",
         "        # Each PAIR of consecutive INDEPENDENT anchors","        blocked=set()")
        if method.find(x)>=0),None)
    assert begin is not None,'Before/after planner unknown'
    end=method.find('        changed=set()',begin)
    assert end>begin,'Before/after planner end unknown'
    plan='''        # Consecutive independent anchors may surround 100+ files.
        # The GUI sort order does not affect this original-file ordering.
        blocked=set()
        plan={}
        positions=[i for i,value in enumerate(anchors) if value]
        for left_pos,right_pos in zip(positions,positions[1:]):
            left,right=anchors[left_pos],anchors[right_pos]
            interior=range(left_pos+1,right_pos)
            if any(str(order[j][1].get("status") or "") in
                   ("Not scanned","Scanning","Moved to BLANKS") or
                   str(order[j][1].get("status") or "").startswith("Scan error:")
                   for j in interior):
                continue
            if key(left)==key(right):
                for pos in interior:
                    if not excluded(order[pos][1]) and not anchors[pos]:
                        plan[pos]=(left,[order[left_pos][0],order[right_pos][0]])
            else:
                for pos in interior:
                    if not excluded(order[pos][1]) and not anchors[pos] and order[pos][1].get("candidate"):
                        blocked.add(pos)
'''
    return method[:begin]+plan+method[end:]
s=replace_method(s,"_apply_sequence_consensus",sequence)
helper='''    @staticmethod
    def _dedupe_verified_red_boxes(candidates,record):
        found=[]
        for hit in candidates or []:
            if not isinstance(hit,dict) or not CASE_RE.fullmatch(normalize_case_number(hit.get("number"))):
                continue
            rect=hit.get("rect")
            if not isinstance(rect,(list,tuple)) or len(rect)!=4:
                continue
            try:
                x0,y0,x1,y1=map(float,rect)
            except (ValueError,TypeError):
                continue
            if not (0<=x0<x1 and 0<=y0<y1):
                continue
            found.append(hit)
        preferred=_case_identity(record.get("confirmed") or record.get("candidate"))
        def rank(hit):
            x0,y0,x1,y1=map(float,hit["rect"])
            return (_case_identity(hit.get("number"))==preferred,
                    float(hit.get("score") or 0),-(x1-x0)*(y1-y0))
        kept=[]
        for hit in sorted(found,key=rank,reverse=True):
            x0,y0,x1,y1=map(float,hit["rect"])
            dup=False
            for other in kept:
                a0,b0,a1,b1=map(float,other["rect"])
                overlap=max(0,min(x1,a1)-max(x0,a0))*max(0,min(y1,b1)-max(y0,b0))
                small=min((x1-x0)*(y1-y0),(a1-a0)*(b1-b0))
                if small>0 and overlap/small>=0.55:
                    dup=True
                    break
            if not dup:
                kept.append(hit)
        return kept

'''
idx=s.find('    def _show_review_item(self, index):')
assert idx>=0
s=s[:idx]+helper+s[idx:]
start=s.index('    def _show_review_item(self, index):')
end=s.index('    def ',start+6)
method=s[start:end]
m=re.search(r'(?m)^        visual_candidates\s*=.*$',method)
assert m,"visual_candidates missing"
insert=m.end()
if method[m.start():insert].count("(")>method[m.start():insert].count(")"):
    nextpos=method.find("\n",insert)+1
    while nextpos<len(method):
        stop=method.find("\n",nextpos)
        if stop<0:break
        if ")" in method[nextpos:stop]:
            insert=stop
            break
        nextpos=stop+1
method=method[:insert]+'\n        visual_candidates = self._dedupe_verified_red_boxes(visual_candidates, r)'+method[insert:]
s=s[:start]+method+s[end:]
s=re.sub(r'(self\.app_version\s*=\s*)"5\.4\.[123]"',r'\g<1>"5.4.4"',s)
assert 'self.app_version = "5.4.4"' in s
compile(s,str(p),"exec")
p.write_text(s,"utf-8")
for name in ("README_FIRST.txt","SETUP.bat"):
    f=app/name
    if f.exists():
        t=f.read_text("utf-8")
        t=re.sub(r'(?i)v?5\.4\.[123]',lambda m:('V' if m.group(0).startswith("V") else 'v' if m.group(0).startswith("v") else '')+'5.4.4',t)
        f.write_text(t,"utf-8")
(app/"CHANGELOG_V5_4_4.txt").write_text(
    "MFCase v5.4.4 — before/after, green-check and OCR overlay fixes\n"
    "- Green check completes and queues; only SEND FILES transfers.\n"
    "- Matching independently verified anchors surround arbitrary gaps, including 100+ files.\n"
    "- Manually corrected numbers anchor; inferred numbers do not.\n"
    "- Strict 08- OCR red-box evidence maintained; overlapping boxes consolidated.\n"
    "- Private PDFs and learning data excluded from release.\n",encoding="utf-8")
manifest={name:hashlib.sha256((app/name).read_bytes()).hexdigest()
          for name in ("case_number_ocr.py","updater_apply.py","SETUP.bat","README_FIRST.txt")}
(app/"verify_package.py").write_text(
    "from pathlib import Path\nimport hashlib\nROOT=Path(__file__).resolve().parent\nEXPECTED="+repr(manifest)+"\n"
    "for name,expected in EXPECTED.items():\n"
    "    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,name\n"
    "print('PASS MFCase v5.4.4 integrity')\n",encoding="utf-8")
print("Patched and integrity-stamped MFCase v5.4.4")
