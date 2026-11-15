"""V5.2.2: field-first 95% OCR, independent blank-middle fallback, and thumbnails."""
from pathlib import Path
import ast,base64,hashlib,io,re,subprocess,zipfile

ROOT=Path("release-assets")
old=base64.b64decode(subprocess.check_output(
    ["git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),validate=True)
assert hashlib.sha256(old).hexdigest()=="138a2382736c0c71d0f12ddd3d7247334ac1bdcef6b8558131f5aa45b9a002ed"
with zipfile.ZipFile(io.BytesIO(old)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    src=z.read("case_number_ocr.py").decode()
    updater=z.read("updater_apply.py")
assert src.count('self.app_version = "5.2.1"')==1
src=src.replace('self.app_version = "5.2.1"','self.app_version = "5.2.2"',1)
for a,b in [
    ("M.F. Case Number OCR — V5.2.1","M.F. Case Number OCR — V5.2.2"),
    ("Case Number OCR & Renamer — V5.2.1","Case Number OCR & Renamer — V5.2.2"),
    ("Application version: V5.2.1","Application version: V5.2.2")]:
    src=src.replace(a,b)

def repl(a,b):
    global src
    count=src.count(a)
    assert count==1,(count,a[:125])
    src=src.replace(a,b,1)

def method(name,body):
    global src
    tree=ast.parse(src)
    found=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(found)==1,(name,len(found))
    n=found[0]
    lines=src.splitlines(keepends=True)
    src="".join(lines[:n.lineno-1])+body.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(src,"case_number_ocr.py","exec")

# A labeled case number is the FIRST rule. Confidence >=95% from the exact
# right-hand field is enough without neighbors. The OCR engine name is not a
# surrogate for where the digits are on the page.
repl('''        """Automatically accept valid OCR reads at 90% confidence or higher."""
        return 90.0''',
     '''        """Require >=95% for an isolated unambiguous case-field OCR read."""
        return 95.0''')
repl('''        """Only the enabled switch, valid case format/year, and 90% score gate auto-confirm."""''',
     '''        """Require enabled auto-confirm, valid year, and >=95% OCR confidence."""''')
# A unique 95%-or-higher labeled read dominates lower-scoring OCR alternatives;
# a second, distinct 95%+ read within the same field remains an actual conflict.
repl('''            perfect=[h for h in filtered if float(h.get("score") or 0)>=99.95]''',
     '''            perfect=[h for h in filtered if float(h.get("score") or 0)>=95.0]''')
repl('''"reason":"lower_score_than_unique_100pct_label_read"''',
     '''"reason":"below_unique_95pct_label_read"''')

# Capture CASE NO., CASE NUMBER, INCIDENT NO., INCIDENT NUMBER, even when the
# printed label and number are returned as one combined OCR text box.
repl(r'''            match = INCIDENT_LABEL_RE.search(text)
            if match:
                # The label and the case digits can be in ONE OCR detection.
                end = x0 + (x1 - x0) * (match.end() / max(1, len(text)))
                found.append((x0, y0, min(x1, end), y1, "incident", score))
            elif re.search(r"\bcase\s+number\b", text, re.I):
                match = re.search(r"case\s+number", text, re.I)
                end = x0 + (x1 - x0) * match.end() / max(1, len(text))
                found.append((x0, y0, min(x1, end), y1, "case", score))''',
     r'''            match = CASE_FIELD_LABEL_RE.search(text)
            if match:
                end = x0 + (x1-x0)*(match.end()/max(1,len(text)))
                label_kind = "incident" if "incident" in match.group(0).lower() else "case"
                found.append((x0,y0,min(x1,end),y1,label_kind,score))''')

# Distinguish CASE from INCIDENT in joined split-label detections.
repl('max(left[3],right[3]), "incident",',
     'max(left[3],right[3]),\n                                   '
     '"incident" if "incident" in match.group(0).lower() else "case",')

# The 95% OCR direct decision is evaluated *before* the secondary sequence rule.
a=src.index('                    auto_done = False\n')
b=src.index('                    self._diag_event("row_final",',a)
assert b>a
new_block='''                    auto_done = False
                    labeled = r["scan_source"] in ("incident","case")
                    positioned_header = r["scan_source"] == "header"
                    strong = float(score or 0) >= self._get_auto_threshold()
                    if (normalized_candidate and (labeled or positioned_header)
                            and strong and not r["scan_ambiguous"]
                            and self.auto_label_var.get()):
                        block_reason=self._auto_label_block_reason(
                            score,agreement,normalized_candidate)
                        if block_reason is None:
                            r["confirmed"]=normalized_candidate
                            r["auto_labeled"]=True
                            r["manual_approved"]=True
                            rule=("95pct_labeled_case_field" if labeled
                                  else "95pct_unique_upper_right_header")
                            r["status"]=(f"{score:.1f}% {('labeled field' if labeled else 'upper-right header')}"
                                         f" — automatically approved; sending to COMPLETED")
                            self._diag_event("approval_decision",
                                file=Path(r["path"]).name,outcome="auto_approved",
                                rule=rule,case=normalized_candidate,
                                score=float(score or 0),source=r["scan_source"])
                            self._move_to_completed(iid)
                            auto_done=True
                            self._run_outcomes["auto_approved"]=(
                                self._run_outcomes.get("auto_approved",0)+1)
                        else:
                            r["status"]=note+" • Auto-confirm blocked: "+block_reason
                    elif not normalized_candidate:
                        r["status"]="No usable case number in 3x3 — checking before/after fallback"
                    elif r["scan_ambiguous"]:
                        r["status"]=note+" — conflicting case-field reads; manual review"
                    elif labeled or positioned_header:
                        r["status"]=(f"{score:.1f}% case-field OCR; below 95% "
                                      "or auto-confirm disabled — manual review")
                    # Unrelated fallback detections never self-authorize.
'''
src=src[:a]+new_block+src[b:]

# Snapshots from the scan queue include already-transferred ANCHORS.
# Previously, as soon as high-confidence neighbors auto-sent, they vanished
# from sequence lookup and could not rescue an unreadable middle file.
repl('''        if (record.get("exported") or record.get("sequence_excluded")''',
     '''        if (record.get("sequence_excluded")''')
repl('''            if not path or rec.get("exported") or not path.is_file():
                continue
            case=self._sequence_case_number(rec)''',
     '''            if not path or str(rec.get("status") or "") in ("Not scanned","Scanning"):
                continue
            # An already-sent strong OCR read remains an independent anchor.
            case=self._sequence_case_number(rec)''')

# Fall back only for a missing/unusable OCR result. Two independent immediate
# neighbors MUST agree in the naturally ordered scan queue. A conflicting
# candidate may NEVER be silently overwritten by the neighbors.
repl('''            if not candidate or not CASE_RE.fullmatch(candidate):
                # Even matching neighbors cannot invent a missing 3x3 number.
                r["status"] = "No readable case number in 3x3 area — MANUAL REVIEW"
                continue''',
     '''            if not candidate or not CASE_RE.fullmatch(candidate):
                if r.get("scan_ambiguous") or r.get("sequence_conflict"):
                    r["status"]="Unreadable conflicting case numbers — MANUAL REVIEW"
                    continue
                if self.auto_label_var.get() and self._auto_label_block_reason(
                        100.0,False,consensus) is None:
                    r["sequence_original_candidate"]=candidate
                    r["sequence_original_ambiguous"]=False
                    r["scan_ambiguous"]=False
                    r["sequence_conflict"]=False
                    r["sequence_anchors"]=[left[0],right[0]]
                    r["confirmed"]=consensus
                    r["sequence_inferred"]=True
                    r["auto_labeled"]=True
                    r["manual_approved"]=True
                    r["status"]=(f"No OCR read — inferred {consensus} from "
                                 "matching before/after scan reads; sending to COMPLETED")
                    self._diag_event("approval_decision",file=Path(r["path"]).name,
                         outcome="auto_approved",rule="blank_middle_matching_neighbors",
                         case=consensus,anchors=[left[0],right[0]],ocr_found=False)
                    self._move_to_completed(iid,batch=True)
                    updated+=1
                else:
                    r["sequence_suggestion"]=consensus
                    r["status"]="No readable case; before/after suggests "+consensus
                    self._insert_scan_row(iid,r)
                    updated+=1
                continue''')
# Don't force three reads for a file which already has a valid >=95% number.
# The sequence pass must not override a previously auto-approved high
# confidence label/header, even if adjacent files belong to another case.
repl('''            if r.get("manual_approved") and not r.get("auto_labeled"):
                continue
            source_kind = r.get("scan_source")''',
     '''            if r.get("manual_approved") and (
                    not r.get("auto_labeled")
                    or (r.get("scan_source") in ("incident","case","header")
                        and float(r.get("score") or 0)>=95.0)):
                continue
            source_kind = r.get("scan_source")''')

# Correct misleading old help strings. Confidence values are OCR engine
# scores, not calibrated probability of correctness.
src=src.replace("an unlabeled handwritten candidate requires 95% AND two matching",
                "a unique unlabeled header candidate at >=95% qualifies immediately;")
src=src.replace("independently-read neighbors (enforced by _poll/sequence consensus).",
                "matching before-and-after neighbors rescue a blank middle page.")
src=src.replace("No readable number in this crop always means manual review.",
                "An unreadable middle file can use the secondary neighbor fallback.")
src=src.replace("two matching neighbors","matching neighbors")

# SCAN table displays a readable OCR crop immediately adjacent to PDF name.
repl('''        cols = ("scan_select", "file", "candidate", "score", "workflow", "status")''',
     '''        self._scan_preview_photos={}
        self._scan_preview_widgets={}
        self._scan_preview_keys={}
        self._scan_preview_refresh_id=None
        ttk.Style().configure("MFCase.Scan.Treeview",rowheight=74)
        cols = ("scan_select", "file", "preview", "candidate", "score", "workflow", "status")''')
repl('''        self.tree = ttk.Treeview(pane, columns=cols, show="headings", selectmode="extended")''',
     '''        self.tree = ttk.Treeview(pane, columns=cols, show="headings",
                                 style="MFCase.Scan.Treeview",selectmode="extended")''')
repl('''        self.tree.column("file", width=620, minwidth=220, stretch=True)
        self.tree.heading("candidate", text="Candidate")''',
     '''        self.tree.column("file",width=350,minwidth=175,stretch=True)
        self.tree.heading("preview",text="CASE NUMBER THUMBNAIL")
        self.tree.column("preview",width=230,minwidth=210,stretch=False,anchor="center")
        self.tree.heading("candidate", text="Candidate")''')
repl('''        self.tree["displaycolumns"] = ("scan_select", "file", "candidate", "score", "workflow", "status")''',
     '''        self.tree["displaycolumns"] = ("scan_select", "file", "preview", "candidate", "score", "workflow", "status")''')
repl('''        self.tree.configure(yscrollcommand=sy.set)''',
     '''        self.tree.configure(yscrollcommand=lambda first,last:(
             sy.set(first,last),self._schedule_scan_preview_refresh()))''')
repl('''        self.tree.bind("<<TreeviewSelect>>", self._sync_scan_selection)''',
     '''        self.tree.bind("<<TreeviewSelect>>", self._sync_scan_selection)
        self.tree.bind("<Configure>",lambda e:self._schedule_scan_preview_refresh(),add="+")
        self.tree.bind("<KeyRelease>",lambda e:self._schedule_scan_preview_refresh(),add="+")''')
repl('''        values=("☑" if iid in self.scan_checked_iids else "☐",
                record["path"].name,
                record.get("candidate","") if candidate_override is None else candidate_override,''',
     '''        values=("☑" if iid in self.scan_checked_iids else "☐",
                record["path"].name,"",
                record.get("candidate","") if candidate_override is None else candidate_override,''')
repl('''            btn.config(state="normal" if allowed else "disabled")

    def _send_all_selected''',
     '''            btn.config(state="normal" if allowed else "disabled")
        self._schedule_scan_preview_refresh()

    def _send_all_selected''')

# Overlay cropped images into a Treeview data column. ttk.Treeview supports
# images only in column #0, which cannot be moved beside PDF FILE. Labels are
# deliberately placed into the preview column of visible rows (lazy updates).
preview=r'''
    def _scan_preview_cache_file(self, record):
        import hashlib as _hashlib
        base=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"preview_cache"
        name=str(record.get("path") or "")
        try:
            stamp=Path(name).stat().st_mtime_ns
        except OSError:
            stamp=0
        key=(name,stamp,str(record.get("candidate") or ""),str(record.get("confirmed") or ""))
        token=_hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:25]
        return base/(token+".png")

    def _make_scan_preview(self,iid,record):
        from PIL import Image,ImageDraw,ImageFont,ImageTk
        cache_path=self._scan_preview_cache_file(record)
        snippet=record.get("snippet")
        existing=self._scan_preview_keys.get(iid)
        key=(str(cache_path),id(snippet))
        if existing==key and iid in self._scan_preview_photos:
            return self._scan_preview_photos[iid]
        tile=None
        if snippet and len(snippet)>1 and snippet[1] is not None:
            try:
                page=Image.fromarray(snippet[1]).convert("RGB")
                candidates=snippet[4] if len(snippet)>4 and isinstance(snippet[4],list) else []
                case=str(record.get("confirmed") or record.get("candidate") or "")
                def same_case(value):
                    try:return tuple(map(int,str(value).split("-",1)))==tuple(map(int,case.split("-",1)))
                    except (ValueError,TypeError):return False
                chosen=next((h for h in candidates if h.get("rect") and same_case(h.get("number"))),None)
                rect=(record.get("manual_crop_rect") if record.get("manual_crop_origin")=="drawn"
                      else chosen.get("rect") if chosen else None)
                rect=rect or snippet[3] or next((h.get("rect") for h in candidates if h.get("rect")),None)
                if rect:
                    x0,y0,x1,y1=map(int,map(float,rect))
                    w=max(10,x1-x0);h=max(10,y1-y0)
                    # Include the label to the LEFT of the number.
                    left=min(210,max(35,int(w*1.7)))
                    box=(max(0,x0-left),max(0,y0-max(11,int(h*.4))),
                         min(page.width,x1+max(12,int(w*.14))),
                         min(page.height,y1+max(11,int(h*.4))))
                else:
                    side=int(round(3*72*2.2))
                    box=(max(0,page.width-side),0,page.width,min(page.height,side))
                crop=page.crop(box)
                crop.thumbnail((216,64),Image.Resampling.LANCZOS)
                tile=Image.new("RGB",(222,68),"white")
                tile.paste(crop,((222-crop.width)//2,(68-crop.height)//2))
            except Exception as exc:
                self._log("Case preview crop failed: %s",exc)
        if tile is None and cache_path.is_file():
            try:
                tile=Image.open(cache_path).convert("RGB")
                tile.thumbnail((222,68),Image.Resampling.LANCZOS)
            except Exception:
                tile=None
        if tile is None:
            tile=Image.new("RGB",(222,68),"white")
            draw=ImageDraw.Draw(tile)
            msg="Scan for preview" if not record.get("candidate") else "Crop unavailable"
            draw.text((9,25),msg,fill="#555555")
        ImageDraw.Draw(tile).rectangle((0,0,221,67),outline="#999999",width=1)
        if snippet and snippet[1] is not None:
            try:
                cache_path.parent.mkdir(parents=True,exist_ok=True)
                tile.save(cache_path)
            except OSError:
                pass
        photo=ImageTk.PhotoImage(tile)
        self._scan_preview_photos[iid]=photo
        self._scan_preview_keys[iid]=key
        return photo

    def _schedule_scan_preview_refresh(self):
        if not hasattr(self,"tree") or self._scan_preview_refresh_id is not None:
            return
        try:
            self._scan_preview_refresh_id=self.root.after_idle(self._refresh_scan_previews)
        except tk.TclError:
            self._scan_preview_refresh_id=None

    def _refresh_scan_previews(self):
        self._scan_preview_refresh_id=None
        if not hasattr(self,"tree") or not self.tree.winfo_exists():
            return
        visible=set()
        height=max(self.tree.winfo_height(),75)
        for y in range(0,height+74,36):
            iid=self.tree.identify_row(y)
            if iid and iid in self.records and iid not in visible:
                visible.add(iid)
                bbox=self.tree.bbox(iid,"preview")
                if not bbox:
                    continue
                x,top,w,h=bbox
                photo=self._make_scan_preview(iid,self.records[iid])
                widget=self._scan_preview_widgets.get(iid)
                if widget is None:
                    widget=tk.Label(self.tree,borderwidth=0,highlightthickness=0,
                                    cursor="hand2")
                    widget.bind("<Button-1>",lambda e,key=iid:self._select_preview_row(key))
                    widget.bind("<Double-1>",lambda e,key=iid:self._open_preview_review(key))
                    self._scan_preview_widgets[iid]=widget
                widget.configure(image=photo)
                width=min(222,max(0,w-6));image_height=min(68,max(0,h-4))
                if width>0 and image_height>0:
                    widget.place(x=x+(w-width)//2,y=top+(h-image_height)//2,
                                 width=width,height=image_height)
        for iid,widget in list(self._scan_preview_widgets.items()):
            if iid not in visible or not self.tree.exists(iid):
                try:widget.place_forget()
                except tk.TclError:pass
            if not self.tree.exists(iid):
                try:widget.destroy()
                except tk.TclError:pass
                self._scan_preview_widgets.pop(iid,None)
                self._scan_preview_photos.pop(iid,None)
                self._scan_preview_keys.pop(iid,None)

    def _select_preview_row(self,iid):
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self._refresh_review_controls()

    def _open_preview_review(self,iid):
        self._select_preview_row(iid)
        if iid in self._review_ready_ids():
            self.review_selected()

'''
repl('''    def _insert_scan_row(self, iid, record, candidate_override=None, score_override=None):''',
     preview+'''    def _insert_scan_row(self, iid, record, candidate_override=None, score_override=None):''')

# Cover a subtle OCR bug: a printed label detected on one page must not
# suppress a strong upper-right header reading on another page. Labeled
# reads still take priority when a complete adjacent number is available.
repl('''            if any_label:
                # If a caption is visible, body text and unlabeled text
                # cannot compete with the formal case-number field.
                results=[r for r in results if r[3] in ("incident","case")]''',
     '''            if any(r[3] in ("incident","case") for r in results):
                # A complete labeled read wins over any fallback or header.
                results=[r for r in results if r[3] in ("incident","case")]''')

# Replace the old broad sequence-confirmation workflow. The user's
# before/after rule is ONLY for a missing / unusable middle case number.
# A known candidate (even weak) is not silently changed into its neighbors'
# value, and a strong labeled or header read never waits for neighbors.
sequence_method=r'''
    def _apply_sequence_consensus(self):
        """Secondary fallback ONLY for no usable case number in the middle.

        Scan filenames need not have consecutive numbers. The two immediate
        scan neighbors must independently OCR the same full case number.
        Already-transferred strong reads remain valid sequence anchors.
        """
        updated=0
        seq=self._sequence_index()
        for iid,rec in list(self.records.items()):
            path=rec.get("path")
            if (rec.get("exported") or rec.get("sequence_excluded")
                    or not path or not path.is_file()
                    or rec.get("status") in ("Not scanned","Scanning","Moved to BLANKS")
                    or str(rec.get("status") or "").startswith("Scan error:")):
                continue
            candidate=normalize_case_number(rec.get("candidate"))
            if candidate and CASE_RE.fullmatch(candidate):
                # Read exists: only field/header 95% rule, or manual review,
                # may authorize it. Never infer over a readable case number.
                continue
            if rec.get("scan_ambiguous") or rec.get("sequence_conflict"):
                continue
            if rec.get("manual_approved") and not rec.get("sequence_inferred"):
                continue
            neighbors=self._sequence_neighbors(iid,seq)
            self._diag_event("sequence_decision",
                file=path.name,candidate=candidate,
                neighbors=neighbors,stage="blank_middle_before_after_fallback")
            if not neighbors:
                continue
            before,after=neighbors
            try:
                first=tuple(map(int,str(before[1]).split("-",1)))
                last=tuple(map(int,str(after[1]).split("-",1)))
            except (TypeError,ValueError):
                continue
            if first!=last:
                rec["status"]=f"Sequence boundary {before[1]} / {after[1]} — manual review"
                self._insert_scan_row(iid,rec)
                continue
            consensus=str(before[1])
            if self._auto_label_block_reason(100.0,False,consensus) is not None:
                rec["sequence_suggestion"]=consensus
                rec["status"]=f"Before/after suggests {consensus} — manual review"
                self._insert_scan_row(iid,rec)
                updated+=1
                continue
            rec["sequence_original_candidate"]=candidate
            rec["sequence_original_ambiguous"]=False
            rec["scan_ambiguous"]=False
            rec["sequence_conflict"]=False
            rec["sequence_anchors"]=[before[0],after[0]]
            rec["confirmed"]=consensus
            rec["sequence_inferred"]=True
            rec["auto_labeled"]=True
            rec["manual_approved"]=True
            rec["status"]=f"No OCR read — {consensus} inferred from matching before/after scans"
            self._diag_event("approval_decision",file=path.name,
                outcome="auto_approved",rule="blank_middle_matching_neighbors",
                case=consensus,anchors=[before[0],after[0]],ocr_found=False)
            self._move_to_completed(iid,batch=True)
            updated+=1
        if updated:
            self._refresh_review_controls()
            self._update_completed_count()
            self._save_state()
        self._schedule_auto_export()
        return updated
'''
method("_apply_sequence_consensus",sequence_method)

# Strictly preserve the 3x3 OCR region, root-folder scan only, shared learning,
# manually selected training crop, and legacy two-file updater compatibility.
compile(src,"case_number_ocr.py","exec")
tree=ast.parse(src)
assert 'self.app_version = "5.2.2"' in src
assert 'root.title("M.F. Case Number OCR — V5.2.2")' in src
assert 'text="REVIEW SELECTED"' in src
assert 'def _make_scan_preview' in src
assert 'def _refresh_scan_previews' in src
assert 'dimension_px=int(round(3*72*2.2))' in src
assert 'os.utime(destination, None)' in src
assert 'def _diag_export' in src
assert 'minimum_independent_matches=3' in src
for n in ["_ocr_pdf","_apply_sequence_consensus","_refresh_scan_previews","_insert_scan_row"]:
    assert len([a for a in ast.walk(tree) if isinstance(a,ast.FunctionDef) and a.name==n])==1
o=io.BytesIO()
with zipfile.ZipFile(o,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",src)
    z.writestr("updater_apply.py",updater)
blob=o.getvalue()
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    for n in z.namelist():compile(z.read(n),n,"exec")
digest=hashlib.sha256(blob).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(blob).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(blob)
(ROOT/"SHA256.txt").write_text(digest+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.2.2\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("V5.2.2: 95% field priority, blank-middle fallback, scan thumbnails\n")
print("BUILD PASS V5.2.2 sha256",digest,"bytes",len(blob))
