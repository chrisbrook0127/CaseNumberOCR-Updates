"""MFCase V5.1.4: diagnostic run ledger plus strict field-first conflict handling.

Patch the exact previously published V5.1.3 release. The shipped update ZIP
contains only the two existing Python modules, preserving legacy updatability.
"""
from pathlib import Path
from datetime import datetime,timezone
import ast,base64,hashlib,io,zipfile,re

ROOT=Path("release-assets")
old=base64.b64decode((ROOT/"update.base64").read_text(),validate=True)
assert hashlib.sha256(old).hexdigest()=="2988f8bc504f225bf13d009555ca5e6286855888a69a98cab2aefbc450dd4ee0"
with zipfile.ZipFile(io.BytesIO(old)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    app=z.read("case_number_ocr.py").decode("utf-8")
    helper=z.read("updater_apply.py")
assert app.count('self.app_version = "5.1.3"')==1
app=app.replace('self.app_version = "5.1.3"','self.app_version = "5.1.4"',1)
app=app.replace('root.title("M.F. Case Number OCR — V5.1.2")',
                'root.title("M.F. Case Number OCR — V5.1.4")',1)

def swap(one,two):
    global app
    count=app.count(one)
    if count!=1:
        raise AssertionError(("source pattern mismatch",count,repr(one[:140])))
    app=app.replace(one,two,1)

# A separate local, append-only per-run JSONL record; no shared learning
# mutations and no network uploads. Explicit export creates a ZIP for user review.
diagnostics='''
    def _diag_event(self, event, **details):
        """Append an analysis-ready JSON record without interrupting OCR."""
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            filepath=getattr(self,"_diag_file",None)
            if filepath is None:
                return
            payload={"schema":"mfcase.diagnostic.v1",
                     "utc":_dt.now(_tz.utc).isoformat(timespec="milliseconds"),
                     "run_id":getattr(self,"_diag_run_id",None),
                     "version":getattr(self,"app_version","unknown"),
                     "event":event}
            if getattr(self,"_diag_current_file",None):
                payload["file"]=self._diag_current_file
            payload.update(details)
            with self._diag_lock:
                with open(filepath,"a",encoding="utf-8",newline="\\n") as handle:
                    handle.write(_json.dumps(payload,ensure_ascii=False,default=str,
                                             separators=(",",":"))+"\\n")
        except Exception as exc:
            # Diagnostics must never block evidence processing.
            try: logging.warning("Diagnostic logging failed: %s",exc)
            except Exception: pass

    def _diag_start_run(self):
        """Create a fresh, time-stamped run in local-only diagnostic storage."""
        try:
            import uuid
            from datetime import datetime as _dt, timezone as _tz
            directory=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"diagnostics"
            directory.mkdir(parents=True,exist_ok=True)
            run_id=_dt.now(_tz.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            self._diag_run_id=run_id
            self._diag_file=directory/("mfcase-"+run_id+".jsonl")
            self._diag_current_file=None
            self._diag_event("run_start",
                 selected_files=[Path(x[1]["path"]).name for x in self.scan_items],
                 scan_folder=str(self.folder),
                 year_prefix=self.scan_year_prefix,
                 scan_region_inches=[3,3],
                 printed_auto_threshold=100.0,handwritten_threshold=95.0,
                 auto_label_enabled=bool(self.auto_label_var.get()),
                 application_version=self.app_version)
            # Keep a bounded history on the workstation (not on the shared share).
            for excess in sorted(directory.glob("mfcase-*.jsonl"),reverse=True)[25:]:
                try: excess.unlink()
                except OSError: pass
        except Exception as exc:
            self._diag_file=None
            self._log("Could not initialize detailed diagnostic log: %s",exc)

    @staticmethod
    def _diag_hits(hits):
        return [{
            "number":str(h.get("number") or ""),
            "score":round(float(h.get("score") or 0),3),
            "bbox":list(h.get("rect")) if h.get("rect") is not None else None,
            "engines":list(h.get("engines") or []),
            "label_type":h.get("label_type"),
            "label_context":bool(h.get("label_context")),
            "learned_location":bool(h.get("learned_location"))
        } for h in (hits or [])[:100]]

    def _diag_export(self):
        """User-controlled ZIP export. No automatic upload or transmission."""
        import zipfile as _zipfile
        from datetime import datetime as _dt
        from tkinter import filedialog as _filedialog, messagebox as _messagebox
        active=getattr(self,"_diag_file",None)
        directory=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"diagnostics"
        logs=sorted(directory.glob("mfcase-*.jsonl"),reverse=True)[:10] if directory.is_dir() else []
        if active and Path(active).exists() and Path(active) not in logs:
            logs.insert(0,Path(active))
        if not logs:
            _messagebox.showinfo("Work log","No recorded scans yet. Run a scan first.")
            return
        suggested="MFCase_Work_Log_"+_dt.now().strftime("%Y%m%d_%H%M%S")+".zip"
        target=_filedialog.asksaveasfilename(
            title="Export MFCase OCR work log",defaultextension=".zip",
            initialfile=suggested,filetypes=[("ZIP archive","*.zip")])
        if not target:
            return
        notes=(
            "MFCase OCR diagnostic work log, schema mfcase.diagnostic.v1\\n"
            "Each JSONL line is a standalone JSON event. Filter on event/file/run_id.\\n"
            "Events: run_start, ocr_label_pass, field_ocr_pass, candidate_pass, "
            "ocr_candidates, candidate_filter, ocr_decision, row_decision, "
            "sequence_decision, transfer_result, run_end.\\n"
            "bbox is [left,top,right,bottom] in rendered page pixels.\\n"
            "score is the raw OCR engine score, not a statistical guarantee.\\n"
            "ignored contains candidate reads excluded and an explicit reason.\\n"
            "Do not share externally without checking department data policies.\\n"
            "Contains filenames, possible identifiers and OCR text in the 3-inch zone.\\n"
            "No original PDFs or images are included.\\n")
        try:
            with _zipfile.ZipFile(target,"w",compression=_zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("HOW_TO_READ.txt",notes)
                for path in logs:
                    archive.write(path,arcname=path.name)
                if self.log_path and Path(self.log_path).is_file():
                    archive.write(self.log_path,arcname="existing_ocr_log.txt")
            _messagebox.showinfo("Work log saved",
                 "Diagnostic ZIP saved. You can upload it here for a complete "
                 "file-by-file OCR decision review.\\n\\n"+str(target))
        except Exception as exc:
            _messagebox.showerror("Work log export failed",str(exc))

    def _diag_field_filter(self,hits,source):
        """Prefer a uniquely perfect read from a labeled case field.

        Ignore obvious non-case strings, mismatched-year strings and lower
        confidence alternate OCR interpretations; audit all discarded reads.
        A distinct competing 100% same-year case remains a manual conflict.
        """
        filtered=[]
        ignored=[]
        expected=str(getattr(self,"scan_year_prefix","") or self._year_prefix() or "")
        for h in hits:
            number=str(h.get("number") or "").strip()
            match=re.fullmatch(r"(\\\\d{1,2})\\\\s*-\\\\s*(\\\\d{1,7})",number)
            if not match:
                ignored.append({"candidate":self._diag_hits([h])[0],"reason":"not_a_complete_case_number"})
                continue
            if expected.isdigit() and int(match.group(1))!=int(expected):
                ignored.append({"candidate":self._diag_hits([h])[0],"reason":"year_mismatch"})
                continue
            filtered.append(h)
        if source in ("incident","case") and filtered:
            perfect=[h for h in filtered if float(h.get("score") or 0)>=99.95]
            if perfect:
                groups={(int(str(h["number"]).split("-")[0]),
                         int(str(h["number"]).split("-")[1])) for h in perfect}
                if len(groups)==1:
                    for h in filtered:
                        if h not in perfect:
                            ignored.append({"candidate":self._diag_hits([h])[0],
                                            "reason":"lower_score_than_unique_100pct_label_read"})
                    filtered=perfect
        self._diag_event("candidate_filter",source=source,
              kept=self._diag_hits(filtered),ignored=ignored,
              expected_year=expected)
        return filtered

'''
swap('    def _log(self, message, *args):', diagnostics+'    def _log(self, message, *args):')
swap('        self.app_version = "5.1.4"\n',
     '        self.app_version = "5.1.4"\n'
     '        self._diag_file=None\n'
     '        self._diag_run_id=None\n'
     '        self._diag_current_file=None\n'
     '        self._diag_lock=threading.RLock()\n')

# Put a one-click user export in SCAN's action bar; existing toolbar preserved.
swap('        self.clear_root_btn.pack(side="left", padx=5, ipadx=8, ipady=3)',
     '        self.clear_root_btn.pack(side="left", padx=5, ipadx=8, ipady=3)\n'
     '        self.work_log_btn=ttk.Button(action_bar,text="EXPORT WORK LOG",command=self._diag_export)\n'
     '        self.work_log_btn.pack(side="left",padx=5,ipadx=5,ipady=3)')

# Every SCAN / RESCAN starts its own run (including scan selection).
swap('        self._log("Scan started in %s with year filter %s", self.folder, self.scan_year_prefix or "ALL")',
     '        self._diag_start_run()\n'
     '        self._log("Scan started in %s with year filter %s", self.folder, self.scan_year_prefix or "ALL")')

# Worker includes the original filename and full candidate reading for each
# PDF before the UI's approval logic executes.
swap('                    result = self._ocr_pdf(source_path)',
     '                    self._diag_current_file=source_path.name\n'
     '                    result = self._ocr_pdf(source_path)')
swap('                    self._log("Scan failed for %s: %s", source_path, exc)',
     '                    self._diag_event("scan_error",file=source_path.name,error=repr(exc))\n'
     '                    self._log("Scan failed for %s: %s", source_path, exc)')

# Record what EasyOCR actually read, not merely the final case number. All
# raw text is LIMITED to the user's explicitly selected 3" field/search crop.
# These local diagnostic files are only exported at user request.
swap('        if valid_labels:\n', '        if valid_labels:\n') if False else None
swap('                valid_labels=[x for x in labels if x[4] in ("incident","case")]',
     '                valid_labels=[x for x in labels if x[4] in ("incident","case")]\n'
     '                self._diag_event("ocr_label_pass",page=page_index+1,\n'
     '                     region=[offset_x,0,width,side],\n'
     '                     labels=[{"bbox":list(x[:4]),"label":x[4]} for x in valid_labels])')
swap('                        if absolute:\n                            results.append((page_index,image,absolute,label[4]))',
     '                        self._diag_event("field_ocr_pass",page=page_index+1,\n'
     '                             label={"bbox":list(label[:4]),"type":label[4]},\n'
     '                             hits=self._diag_hits(absolute))\n'
     '                        if absolute:\n                            results.append((page_index,image,absolute,label[4]))')
swap('                    if inside:\n                        upper=_mfcase_header_field_candidates(inside,image.shape)',
     '                    self._diag_event("candidate_pass",page=page_index+1,\n'
     '                         source="unlabeled_3x3",all_hits=self._diag_hits(hits),\n'
     '                         region_hits=self._diag_hits(inside))\n'
     '                    if inside:\n                        upper=_mfcase_header_field_candidates(inside,image.shape)')
swap('            index,image,hits,kind=max(results,key=rank)\n'
     '            best=_select_primary_case_hit(hits,kind,image.shape)',
     '            index,image,hits,kind=max(results,key=rank)\n'
     '            self._diag_event("ocr_candidates",page=index+1,source=kind,\n'
     '                 all_candidates=self._diag_hits(hits),\n'
     '                 per_page=[{"page":x[0]+1,"source":x[3],\n'
     '                            "hits":self._diag_hits(x[2])} for x in results])\n'
     '            hits=self._diag_field_filter(hits,kind)\n'
     '            if not hits:\n'
     '                self._diag_event("ocr_decision",page=index+1,source=kind,\n'
     '                     outcome="manual_review",reason="no_year_matching_case_candidate")\n'
     '                return ("",(index,image,"3x3 case read rejected",None,[]),\n'
     '                         "3x3 field has no valid case number matching selected year — MANUAL REVIEW",\n'
     '                         False,0.0,[],False)\n'
     '            best=_select_primary_case_hit(hits,kind,image.shape)')
swap('            if ambiguous:\n                note="3x3 case field has conflicting OCR reads — MANUAL REVIEW"',
     '            self._diag_event("ocr_decision",page=index+1,source=kind,\n'
     '                 primary=number,score=float(best.get("score") or 0),\n'
     '                 ambiguous=ambiguous,conflicting=sorted(distinct),\n'
     '                 engines=engines,selected_bbox=best.get("rect"),\n'
     '                 outcome="manual_review" if ambiguous else "candidate_ready")\n'
     '            if ambiguous:\n'
     '                note=("3x3 case field has conflicting OCR reads: "\n'
     '                      + ", ".join(sorted(distinct))+" — MANUAL REVIEW")')

# Show the full candidate list even if an unrelated field produced digits.
# OCR readers can return low-confidence alternatives: retain in log.
swap('                    self._run_outcomes[reason] = self._run_outcomes.get(reason, 0) + 1',
     '                    self._diag_event("row_decision",file=Path(r["path"]).name,\n'
     '                         candidate=normalized_candidate,score=float(score or 0),\n'
     '                         source=r["scan_source"],ambiguous=bool(r["scan_ambiguous"]),\n'
     '                         status=note,engines=list(engines or []),\n'
     '                         outcome_bucket=reason)\n'
     '                    self._run_outcomes[reason] = self._run_outcomes.get(reason, 0) + 1')
swap('                            self._move_to_completed(iid)\n                            auto_done = True',
     '                            self._diag_event("approval_decision",file=Path(r["path"]).name,\n'
     '                                 outcome="auto_approved",rule="100pct_labeled_printed",\n'
     '                                 case=normalized_candidate,score=float(score or 0))\n'
     '                            self._move_to_completed(iid)\n                            auto_done = True')
swap('            neighbors = self._sequence_neighbors(iid, sequence_index)\n            if not neighbors:\n                continue',
     '            neighbors = self._sequence_neighbors(iid, sequence_index)\n'
     '            self._diag_event("sequence_decision",file=Path(r["path"]).name,\n'
     '                 candidate=r.get("candidate"),score=r.get("score"),\n'
     '                 source=r.get("scan_source"),\n'
     '                 neighbors=neighbors,stage="check_neighbors")\n'
     '            if not neighbors:\n                continue')
swap('                self._move_to_completed(iid, batch=True)',
     '                self._diag_event("approval_decision",file=Path(r["path"]).name,\n'
     '                     outcome="auto_approved",rule="two_matching_neighbors",\n'
     '                     case=consensus,anchors=[left[0],right[0]],\n'
     '                     original_candidate=candidate,score=observed_score)\n'
     '                self._move_to_completed(iid, batch=True)')

swap('                    os.replace(str(source), str(destination))',
     '                    os.replace(str(source), str(destination))\n'
     '                    self._diag_event("transfer_result",file=source.name,\n'
     '                         source_file=source.name,destination_file=destination.name,\n'
     '                         case=case,outcome="moved")')
swap('                    self.queue.put(("export_result", iid, source.name, "", str(exc)))',
     '                    self._diag_event("transfer_result",file=source.name,\n'
     '                         destination_file=str(destination) if destination else None,\n'
     '                         outcome="failed",error=repr(exc))\n'
     '                    self.queue.put(("export_result", iid, source.name, "", str(exc)))')

# The exact raw OCR strings (including unexpected case-like prefixes) explain
# otherwise opaque conflicts. Limit to the scanned crop, not whole PDF body.
tree=ast.parse(app)
target=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and
        n.name=="_find_case_field_label_regions"]
assert len(target)==1
segment=ast.get_source_segment(app,target[0])
needle='        parsed = []'
assert segment.count(needle)==1
enh='''        self._diag_event("ocr_raw_detections",scope="3x3_label",
            detections=[{"text":str(d[1])[:120],
                         "confidence":round(float(d[2] or 0)*100,3),
                         "bbox":[[round(float(p[0]),2),round(float(p[1]),2)]
                                 for p in d[0]]} for d in detections[:150]])
'''
# The method is part of class App; this logs every text box on the 3" crop.
segment=segment.replace(needle,enh+needle,1)
app=app.replace(ast.get_source_segment(app,target[0]),segment,1)

compile(app,"case_number_ocr.py","exec")
assert app.count('self.app_version = "5.1.4"')==1
assert 'dimension_px=int(round(3*72*2.2))' in app
assert 'observed_score < 95.0' in app
assert 'os.utime(destination, None)' in app
assert 'def _diag_export' in app and 'def _diag_field_filter' in app
assert app.count('text="EXPORT WORK LOG"')==1

with io.BytesIO() as out:
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        z.writestr("case_number_ocr.py",app)
        z.writestr("updater_apply.py",helper)
    payload=out.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    compile(z.read("case_number_ocr.py"),"case_number_ocr.py","exec")
    compile(z.read("updater_apply.py"),"updater_apply.py","exec")
sha=hashlib.sha256(payload).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(payload).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(payload)
(ROOT/"SHA256.txt").write_text(sha+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.1.4\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("V5.1.4 diagnostic audit and field-first OCR repair\n")
print("PASS V5.1.4 package",len(payload),"SHA256",sha)
