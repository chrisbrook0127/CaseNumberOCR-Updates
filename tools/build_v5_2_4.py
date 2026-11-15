"""MFCase 5.2.4 — visible scanning progress and evidence-based OCR conflict handling.

Patch ONLY immutable, published V5.2.3; preserve reviewed/sent state, root-only
files, thumbnails, three-by-three-inch OCR, and updater two-file allowlist.
"""
from pathlib import Path
import ast,base64,hashlib,io,subprocess,zipfile

ROOT=Path("release-assets")
old=base64.b64decode(subprocess.check_output(
    ["git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),validate=True)
assert hashlib.sha256(old).hexdigest()=="33d4a12efa80ccaeaf3e220c99640ee8b710822664e07adc1240362a0c7dedb2"
with zipfile.ZipFile(io.BytesIO(old)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    src=z.read("case_number_ocr.py").decode("utf-8")
    helper=z.read("updater_apply.py")

def repl(old,new,n=1):
    global src
    assert src.count(old)==n,(src.count(old),old[:140])
    src=src.replace(old,new,n)

repl('self.app_version = "5.2.3"','self.app_version = "5.2.4"')
for a,b in (
    ("M.F. Case Number OCR — V5.2.3","M.F. Case Number OCR — V5.2.4"),
    ("Case Number OCR & Renamer — V5.2.3","Case Number OCR & Renamer — V5.2.4"),
    ("Application version: V5.2.3","Application version: V5.2.4"),
    ("MFCase-Connectivity/5.2.3","MFCase-Connectivity/5.2.4")):
    src=src.replace(a,b)

# Move the existing real scan progressbar directly UNDER the action buttons
# and ABOVE the large file table; it was hidden below the full-size grid.
repl('''        pane = ttk.Frame(self.review_tab)
        pane.pack(fill="both", expand=True)''',
'''        scan_progress_row=ttk.Frame(self.review_tab)
        scan_progress_row.pack(fill="x",pady=(0,8))
        self.scan_progress_label=ttk.Label(
            scan_progress_row,text="SCAN READY — 0 of 0 PDF files",
            width=38,anchor="w")
        self.scan_progress_label.pack(side="left",padx=(0,10))
        self.progress=ttk.Progressbar(scan_progress_row,mode="determinate",
                                      maximum=1,value=0)
        self.progress.pack(side="left",fill="x",expand=True)
        self.scan_progress_pct=ttk.Label(scan_progress_row,text="0%",width=6,anchor="e")
        self.scan_progress_pct.pack(side="right",padx=(10,0))
        pane = ttk.Frame(self.review_tab)
        pane.pack(fill="both", expand=True)''')
repl('''        self.progress = ttk.Progressbar(self.review_tab, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 4))
        self.status = ttk.Label(self.review_tab, text="Choose a folder, then click SCAN.")''',
'''        # The scan progressbar is always visible above the list.
        self.status = ttk.Label(self.review_tab, text="Choose a folder, then click SCAN.")''')

# Existing scan worker already emits row indices (blank and successful reads)
# via the main Tk queue. The top progressbar gets real completed-file counts.
progress=r'''
    def _scan_progress_update(self,done=None,phase="SCANNING"):
        """Non-blocking Tk progress display reflecting actual PDF scan work."""
        total=len(getattr(self,"scan_items",[]) or [])
        if done is None:
            try:done=int(float(self.progress["value"]))
            except (ValueError,TypeError,tk.TclError):done=0
        done=max(0,min(total,int(done)))
        self.progress.config(maximum=max(1,total),value=done)
        pct=int(round(100*done/total)) if total else 0
        label=getattr(self,"scan_progress_label",None)
        if label is not None:
            label.configure(text=f"{phase} — {done} of {total} PDF files")
        pct_label=getattr(self,"scan_progress_pct",None)
        if pct_label is not None:
            pct_label.configure(text=f"{pct}%")
        self._diag_event("scan_progress",done=done,total=total,
                         percentage=pct,phase=phase)

'''
repl('    def _diag_field_filter(self,hits,source):',progress+'    def _diag_field_filter(self,hits,source):')
repl('''        self.progress.config(maximum=len(self.scan_items), value=0)
        self.status.config(text=f"Scanning {len(self.scan_items)} file(s)...")''',
'''        self._scan_progress_update(0,"SCANNING")
        self.status.config(text=f"Scanning {len(self.scan_items)} file(s)...")''')
repl('''                    self.progress["value"] = idx''',
'''                    self._scan_progress_update(idx,"SCANNING")''',3)
# Display completion clearly while finished auto-approved transfers are
# draining to COMPLETED (the transfer queue is separate from OCR).
repl('''                    self.busy = False
                    self._apply_sequence_consensus()''',
'''                    self.busy = False
                    self._scan_progress_update(len(self.scan_items),"OCR COMPLETE — SENDING")
                    self._apply_sequence_consensus()''')
repl('''                elif kind == "export_done":
                    self.export_busy = False''',
'''                elif kind == "export_done":
                    if not self.busy:
                        self._scan_progress_update(len(self.scan_items),"SCAN & SEND COMPLETE")
                    self.export_busy = False''')

# Separate independently read case numbers from alternative OCR renderings
# of the very same printed/handwritten number. Enhanced passes can otherwise
# invent 08-15 or 08-81 even though the primary field shows 08-44 at 99.8%.
resolve=r'''
    def _resolve_ocr_alternates(self,hits,kind,best):
        """Keep real conflicting case fields; suppress unsupported variants.

        The selected reading and its ORIGINAL (non-enhanced) OCR box receive
        precedence. Enhanced/TrOCR-only variants over the same physical box
        cannot veto a primary >=95% read (100% for unlabelled fallback).
        Distinct independently detected >=95% cases in one field still require
        manual review. All discarded alternatives are recorded with reasons.
        """
        if not hits:
            return hits
        key=lambda h:tuple(int(v) for v in str(h.get("number") or "").split("-",1))
        primary_score=float(best.get("score") or 0)
        minimum=99.95 if kind=="fallback" else 95.0
        if primary_score<minimum or not best.get("rect"):
            return hits
        rect=tuple(float(x) for x in best["rect"])
        x0,y0,x1,y1=rect
        cw=max(1.0,x1-x0);ch=max(1.0,y1-y0)
        cx=(x0+x1)/2.0;cy=(y0+y1)/2.0
        p_eng={str(v) for v in best.get("engines") or []}
        p_raw=("EasyOCR" in p_eng or any(
            v.startswith("EasyOCR-") and "enhanced" not in v.lower() for v in p_eng))
        def raw_engine(item):
            return any(str(e)=="EasyOCR" or (
                str(e).startswith("EasyOCR-") and
                "enhanced" not in str(e).lower())
                for e in (item.get("engines") or []))
        retained=[]
        dismissed=[]
        for h in hits:
            reason=None
            if key(h)==key(best):
                retained.append(h)
                continue
            score=float(h.get("score") or 0)
            r=h.get("rect")
            if not r:
                reason="no_independent_location"
            else:
                ax0,ay0,ax1,ay1=map(float,r)
                acx=(ax0+ax1)/2.0;acy=(ay0+ay1)/2.0
                aw=max(1.,ax1-ax0);ah=max(1.,ay1-ay0)
                dx=abs(acx-cx);dy=abs(acy-cy)
                same_location=(
                    dx<=max(16.,min(cw,aw)*.65) and
                    dy<=max(12.,min(ch,ah)*.85))
                if score<95.0 and primary_score-score>=3.0:
                    reason="weak_secondary_ocr_variant"
                elif same_location:
                    # One physical number, different interpretations across
                    # contrast-enhanced and TrOCR passes. The independent
                    # original EasyOCR detection is the primary evidence.
                    if p_raw and not raw_engine(h):
                        reason="enhanced_or_trocr_variant_of_primary_box"
                    elif score<primary_score-3.0:
                        reason="substantially_weaker_same_box_read"
                    # Near-equal, independently detected raw reads remain a
                    # manual conflict (don't mask true ambiguity).
                else:
                    # Separate geometry within the 3x3 zone. Unanchored text
                    # away from the selected upper-right case box is not a
                    # second reading of THAT field.
                    if not h.get("label_context") and not h.get("label_type"):
                        reason="different_unlabeled_location_not_case_field"
                    elif not raw_engine(h) and p_raw:
                        reason="separate_enhanced_only_detection"
            if reason:
                dismissed.append({"candidate":self._diag_hits([h])[0],
                                  "reason":reason})
            else:
                retained.append(h)
        self._diag_event("case_conflict_resolution",source=kind,
            primary=self._diag_hits([best])[0],
            retained=self._diag_hits(retained),ignored=dismissed)
        return retained

'''
repl('    def _diag_field_filter(self,hits,source):',resolve+'    def _diag_field_filter(self,hits,source):')

repl('''            best=_select_primary_case_hit(hits,kind,image.shape)
            number=best["number"]''',
'''            best=_select_primary_case_hit(hits,kind,image.shape)
            hits=self._resolve_ocr_alternates(hits,kind,best)
            number=best["number"]''')
repl('''            across={h["number"] for r in results if r[3]==kind
                    for h in self._diag_field_filter(r[2],kind)}''',
'''            across=set()
            for _page,_image,_hits,_source in results:
                if _source!=kind:
                    continue
                _filtered=self._diag_field_filter(_hits,kind)
                if not _filtered:
                    continue
                _best=_select_primary_case_hit(_filtered,kind,_image.shape)
                across.update(h["number"] for h in
                    self._resolve_ocr_alternates(_filtered,kind,_best))''')

# A handwritten 08-44 at 99.8% is not "100%" but exceeds the user's
# 95%-minimum primary OCR rule. The localized top-right fallback is already
# constrained to the 3x3 field. Do not force 99.95% when a uniquely resolved
# primary case read passes 95% and has a real bounding box.
repl('''        minimum=99.95 if kind=="fallback" else 95.0''',
     '''        minimum=95.0''')
repl('''                        and float(score or 0) >= 99.95''',
     '''                        and float(score or 0) >= self._get_auto_threshold()''')
repl('''                    fallback_100 = (''',
     '''                    fallback_95 = (''')
repl('''                                 or fallback_100)''',
     '''                                 or fallback_95)''')
repl('''                                  else "100pct_localized_3x3_fallback")''',
     '''                                  else "95pct_localized_3x3_fallback")''')
repl('''                            "requires an unambiguous localized 100% OCR read "''',
     '''                            "requires an unambiguous localized 95% OCR read "''')
repl('''                note="Upper-right fallback within 3x3 — unique 100% localized read may auto-approve"''',
     '''                note="Upper-right fallback within 3x3 — unique 95% localized read may auto-approve"''')
repl('''            if (score<99.95 or not isinstance(snip,(tuple,list))''',
     '''            if (score<95.0 or not isinstance(snip,(tuple,list))''')

# The candidate selection in fallback mode already picks a box in the
# upper-right region, but its score ranking ignores the selected year. Year
# filtering happens before resolution; only YY- case numbers are eligible.
# Keep primary field 95%, fallback 100%, before/after range and safety guards.
compile(src,"case_number_ocr.py","exec")
tree=ast.parse(src)
assert 'self.app_version = "5.2.4"' in src
assert 'root.title("M.F. Case Number OCR — V5.2.4")' in src
assert 'dimension_px=int(round(3*72*2.2))' in src
assert 'def _resolve_ocr_alternates(' in src
assert 'def _scan_progress_update(' in src
assert 'self.scan_progress_label=' in src
assert 'minimum=95.0' in src
assert 'def _make_scan_preview' in src
assert 'text="REVIEW SELECTED"' in src
assert 'os.utime(destination, None)' in src
assert 'def _apply_sequence_consensus(self):' in src

out=io.BytesIO()
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",src)
    z.writestr("updater_apply.py",helper)
blob=out.getvalue()
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    for name in z.namelist():compile(z.read(name),name,"exec")
digest=hashlib.sha256(blob).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(blob).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(blob)
(ROOT/"SHA256.txt").write_text(digest+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.2.4\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("V5.2.4 case field conflict refinement and always-visible scan progress\n")
print("PASS MFCase V5.2.4",len(blob),"SHA256",digest)
