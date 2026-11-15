"""MFCase V5.2.3 — finish-approved transfers and 100% 3x3 fallback.

Patch immutable published V5.2.2 from its exact SHA256 source. Keep the
updater's two-file compatibility, the 3x3 region, root-only enumeration,
learning, row thumbnails, and review functionality intact.
"""
from pathlib import Path
import ast,base64,hashlib,io,subprocess,zipfile

ROOT=Path("release-assets")
pinned=base64.b64decode(subprocess.check_output([
    "git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),
    validate=True)
assert hashlib.sha256(pinned).hexdigest()=="97c8f05fd9ffbe1b806967a0974682b50717651f106945330d04a222e589d196"
with zipfile.ZipFile(io.BytesIO(pinned)) as z:
    assert z.testzip() is None and set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    src=z.read("case_number_ocr.py").decode("utf-8")
    helper=z.read("updater_apply.py")
assert src.count('self.app_version = "5.2.2"')==1
src=src.replace('self.app_version = "5.2.2"','self.app_version = "5.2.3"',1)
for old,new in [
    ("M.F. Case Number OCR — V5.2.2","M.F. Case Number OCR — V5.2.3"),
    ("Case Number OCR & Renamer — V5.2.2","Case Number OCR & Renamer — V5.2.3"),
    ("Application version: V5.2.2","Application version: V5.2.3"),
    ("MFCase-Connectivity/5.2.0","MFCase-Connectivity/5.2.3")]:
    src=src.replace(old,new)

def swap(old,new,expected=1):
    global src
    got=src.count(old)
    assert got==expected,(got,expected,old[:135])
    src=src.replace(old,new,expected)

# The OCR engine has already limited fallback candidates to localized boxes
# inside the top-right 3"x3" region. The selected box MUST be non-null;
# unlabeled candidates may only auto-send at an exact displayed 100% with
# no other same-year competing read.
swap('''                    positioned_header = r["scan_source"] == "header"
                    strong = float(score or 0) >= self._get_auto_threshold()
                    if (normalized_candidate and (labeled or positioned_header)
                            and strong and not r["scan_ambiguous"]
                            and self.auto_label_var.get()):''',
'''                    positioned_header = r["scan_source"] == "header"
                    strong = float(score or 0) >= self._get_auto_threshold()
                    # A fallback's 100% score is usable only with a real OCR
                    # bounding box from _ocr_pdf's 3x3 region, not an unlocated
                    # TrOCR guess or a preview-only crop.
                    fallback_100 = (
                        r["scan_source"] == "fallback"
                        and float(score or 0) >= 99.95
                        and isinstance(snip,(tuple,list)) and len(snip)>=4
                        and snip[3] is not None
                    )
                    if (normalized_candidate
                            and ((labeled or positioned_header) and strong
                                 or fallback_100)
                            and not r["scan_ambiguous"]
                            and self.auto_label_var.get()):''')

swap('''                            rule=("95pct_labeled_case_field" if labeled
                                  else "95pct_unique_upper_right_header")
                            r["status"]=(f"{score:.1f}% {('labeled field' if labeled else 'upper-right header')}"
                                         f" — automatically approved; sending to COMPLETED")''',
'''                            rule=("95pct_labeled_case_field" if labeled
                                  else "95pct_unique_upper_right_header" if positioned_header
                                  else "100pct_localized_3x3_fallback")
                            area=("labeled field" if labeled else "upper-right header"
                                  if positioned_header else "3x3 localized fallback")
                            r["status"]=(
                                f"{score:.1f}% {area} — automatically approved; "
                                "sending to COMPLETED")''')

swap('''                    elif labeled or positioned_header:
                        r["status"]=(f"{score:.1f}% case-field OCR; below 95% "
                                      "or auto-confirm disabled — manual review")
                    # Unrelated fallback detections never self-authorize.''',
'''                    elif labeled or positioned_header:
                        r["status"]=(f"{score:.1f}% case-field OCR; below 95% "
                                      "or auto-confirm disabled — manual review")
                    elif r["scan_source"]=="fallback":
                        r["status"]=(f"{float(score or 0):.1f}% 3x3 fallback — "
                            "requires an unambiguous localized 100% OCR read "
                            "and auto-confirm enabled; otherwise MANUAL REVIEW")
                    # A fallback at 100% with one localized case can auto-send;
                    # unlocated guesses and conflicting year-matched cases cannot.''')

swap('''                note="Upper-right fallback within 3x3 — MANUAL REVIEW"''',
'''                note="Upper-right fallback within 3x3 — unique 100% localized read may auto-approve"''')

# Remove the hidden distinction between automatically and manually approved
# COMPLETED rows: every *confirmed* eligible file still in the root is sent
# when SCAN completes. Human validation overrides an OCR ambiguity, as before.
swap('''        if automatic and not (rec.get("auto_labeled") or (human_approved and rec.get("send_after_manual_approval"))):
            return False
''',
'''        # Every verified COMPLETED record may be transferred after scanning.
        # Records without confirmed+manual_approved are rejected above.
''')
swap('''        """A reviewed human decision overrides OCR uncertainty, not file safety.

        Automatic transfers are limited to machine-approved results. Reviewed
        and corrected files remain in Completed until SEND TO KURT is pressed.
        OCR ambiguity/sequence conflict blocks automatic movement, but cannot
        permanently block an explicitly verified human case-number correction.
        """''',
'''        """Transfer only verified PDFs still in root; keep explicit OCR conflicts
        in review unless a person confirmed the corrected case number.
        """''')

swap('''        """Coalesce approvals; use the Tk event loop rather than moving files in callbacks."""''',
'''        """After a scan, move every eligible COMPLETED PDF, including saved reviews."""''')
swap('''        """SEND TO KURT; checks are optional for manual sends, not automatic sends."""''',
'''        """Move verified root PDFs into COMPLETED after scan/review."""''')

# Correct the misleading "100% labeled printed" status for all methods,
# and the stale message after an otherwise successful scan.
swap('''            queue_status = "100% labeled printed OCR — ready for export"''',
'''            source=r.get("scan_source")
            if source=="fallback":
                queue_status="100% localized 3x3 OCR — ready for export"
            else:
                queue_status=f"{float(r.get('score') or 0):.1f}% OCR approved — ready for export"''')
swap('''                    self.status.config(text="Scan complete. Approved files are queued for Kurt; review the remaining files.")''',
'''                    self.status.config(
                        text="Scan complete. Sending all approved PDFs to COMPLETED; "
                             "unverified files remain in REVIEW.")''')

# If scan finishes while the OCR-learning phase is active, the existing
# scheduling guard defers transfers; re-trigger on learning completion.
# Do not send before scanning finishes: _schedule_auto_export itself checks busy.
anchor='''                    self._update_training_progress(100, message, finished=True)
                    self._learning_auto_run = False'''
assert src.count(anchor)==2
src=src.replace(anchor,'''                    self._update_training_progress(100, message, finished=True)
                    self._learning_auto_run = False
                    # Flush any approved transfers deferred during training.
                    self._schedule_auto_export()''')

# If a transfer finishes while more approvals are created, drain the queue.
# Current export-done branch only reruns when _export_reschedule is set.
swap('''                    if self._export_reschedule:
                        self._export_reschedule = False
                        self._schedule_auto_export()
                elif kind == "engine_status":''',
'''                    self._export_reschedule = False
                    self._schedule_auto_export()
                elif kind == "engine_status":''')

# Sequence consensus now spans any number of intervening SCANNED files.
# Evidence is the two nearest independent reliable reads bounding the run,
# not consecutive filename numbers. No OCR-based inference overrides an
# independently strong disagreeing read or an existing manual confirmation.
def replace_method(name,body):
    global src
    tree=ast.parse(src)
    found=[n for n in ast.walk(tree)
           if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(found)==1,(name,len(found))
    n=found[0];lines=src.splitlines(keepends=True)
    src="".join(lines[:n.lineno-1])+body.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(src,"case_number_ocr.py","exec")

sequence_case=r'''
    def _sequence_case_number(self, record):
        """Return an independent anchored OCR case for sequence comparison.

        95% labeled/header or 100% uniquely localized 3x3 fallback can anchor.
        Manual approvals can anchor. An inferred value cannot anchor.
        """
        if (not record or record.get("sequence_inferred") or
                record.get("sequence_excluded") or
                str(record.get("status") or "").startswith("Unverified") or
                record.get("scan_ambiguous") or record.get("sequence_conflict")):
            return None
        manually_verified=(bool(record.get("manual_approved")) and
                           not bool(record.get("auto_labeled")))
        case=normalize_case_number(
            record.get("confirmed") if manually_verified else record.get("candidate"))
        if not CASE_RE.fullmatch(case):
            return None
        year=self.scan_year_prefix or self._year_prefix()
        if (year and year.isdigit() and
                int(case.split("-",1)[0])!=int(year)):
            return None
        if manually_verified:
            return case
        score=float(record.get("score") or 0)
        source=record.get("scan_source")
        if source=="fallback":
            snip=record.get("snippet")
            if (score<99.95 or not isinstance(snip,(tuple,list))
                    or len(snip)<4 or snip[3] is None):
                return None
        elif source not in ("incident","case","header"):
            return None
        elif score<95.0:
            return None
        return case
'''
replace_method("_sequence_case_number",sequence_case)

range_consensus=r'''
    def _apply_sequence_consensus(self):
        """Secondary before/after rule over contiguous *scan positions*.

        If two adjacent independent OCR anchors in sorted scan order match,
        approve every unverified middle item, even with a weak incorrect OCR
        case (e.g. 08-64 at 75.3% surrounded by 08-44 at 100%).
        Strong, contradictory reads, manual decisions and OCR conflicts are
        NEVER silently overwritten. Export occurs after the scan is complete.
        """
        if not self.auto_label_var.get():
            return 0
        order=[]
        for iid,_item in (getattr(self,"scan_items",[]) or []):
            record=self.records.get(iid)
            if not record:
                continue
            # Keep real sequence slots, even if a scan cannot be read; do not
            # require adjacent source filenames or ascending numeric suffixes.
            order.append((iid,record))
        anchors=[]
        for index,(iid,record) in enumerate(order):
            number=self._sequence_case_number(record)
            if number:
                anchors.append((index,iid,number))
        self._diag_event("sequence_anchor_snapshot",order=[
            {"index":pos,"file":Path(rec["path"]).name if rec.get("path") else iid,
             "candidate":rec.get("candidate"),"score":rec.get("score"),
             "source":rec.get("scan_source"),"anchor":(
                 next((case for pos2,aid,case in anchors if pos2==pos),None))}
            for pos,(iid,rec) in enumerate(order)])
        updated=0
        for previous,following in zip(anchors,anchors[1:]):
            left_pos,left_iid,left_case=previous
            right_pos,right_iid,right_case=following
            if right_pos<=left_pos+1:
                continue
            numeric=lambda val:tuple(map(int,str(val).split("-",1)))
            if numeric(left_case)!=numeric(right_case):
                self._diag_event("sequence_range_rejected",reason="different_anchor_cases",
                    first=left_case,last=right_case,
                    from_file=Path(self.records[left_iid]["path"]).name,
                    to_file=Path(self.records[right_iid]["path"]).name)
                continue
            consensus=left_case
            first_name=Path(self.records[left_iid]["path"]).name
            last_name=Path(self.records[right_iid]["path"]).name
            for _pos in range(left_pos+1,right_pos):
                iid,rec=order[_pos]
                path=rec.get("path")
                if (not path or not Path(path).is_file() or
                        rec.get("exported") or rec.get("sequence_excluded") or
                        rec.get("manual_approved") or
                        rec.get("scan_ambiguous") or rec.get("sequence_conflict") or
                        rec.get("status") in ("Not scanned","Scanning","Moved to BLANKS") or
                        str(rec.get("status") or "").startswith("Scan error:")):
                    continue
                original=normalize_case_number(rec.get("candidate"))
                score=float(rec.get("score") or 0)
                valid=bool(CASE_RE.fullmatch(original))
                if (valid and score>=95.0 and
                    numeric(original)!=numeric(consensus)):
                    self._diag_event("sequence_range_rejected",
                        file=Path(path).name,reason="high_confidence_conflicting_middle",
                        original=original,score=score,expected=consensus,
                        first=first_name,last=last_name)
                    rec["status"]=(f"{score:.1f}% OCR {original} conflicts with "
                        f"matching range {consensus}; manual review required")
                    self._insert_scan_row(iid,rec)
                    continue
                block=self._auto_label_block_reason(100.0,False,consensus)
                if block is not None:
                    self._diag_event("sequence_range_rejected",
                        file=Path(path).name,reason=block)
                    continue
                rec["sequence_original_candidate"]=original
                rec["sequence_original_score"]=score
                rec["sequence_original_ambiguous"]=False
                rec["sequence_anchors"]=[left_iid,right_iid]
                rec["candidate"]=consensus
                rec["confirmed"]=consensus
                rec["sequence_inferred"]=True
                rec["sequence_excluded"]=False
                rec["sequence_conflict"]=False
                rec["scan_ambiguous"]=False
                rec["auto_labeled"]=True
                rec["manual_approved"]=True
                rec["status"]=(
                    f"Range-confirmed {consensus} between {first_name} and {last_name}"
                    + (f"; original OCR {original} at {score:.1f}%"
                       if original else "; original OCR unreadable"))
                self._diag_event("approval_decision",file=Path(path).name,
                    outcome="auto_approved",rule="matching_boundary_scan_range",
                    case=consensus,first=first_name,last=last_name,
                    original_candidate=original,original_score=score)
                self._move_to_completed(iid,batch=True)
                updated+=1
        if updated:
            self._refresh_review_controls()
            self._update_completed_count()
            self._save_state()
        self._diag_event("sequence_range_summary",anchors=len(anchors),
                         inferred=updated,scanned=len(order))
        self._schedule_auto_export()
        return updated
'''
replace_method("_apply_sequence_consensus",range_consensus)

# Preserve all OCR and file safety logic. No background extra server sends.
compile(src,"case_number_ocr.py","exec")
tree=ast.parse(src)
assert 'self.app_version = "5.2.3"' in src
assert 'root.title("M.F. Case Number OCR — V5.2.3")' in src
assert "100pct_localized_3x3_fallback" in src
assert "and snip[3] is not None" in src
assert 'dimension_px=int(round(3*72*2.2))' in src
assert 'def _diag_export(' in src and 'def _diag_sync_shared(' in src
assert 'def _make_scan_preview(' in src and 'text="REVIEW SELECTED"' in src
assert 'os.utime(destination, None)' in src
assert 'def _apply_sequence_consensus(self):' in src
for method in ["_eligible_for_kurt","_schedule_auto_export","_poll","_ocr_pdf"]:
    assert len([n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==method])==1
o=io.BytesIO()
with zipfile.ZipFile(o,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",src)
    z.writestr("updater_apply.py",helper)
blob=o.getvalue()
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    for p in z.namelist():compile(z.read(p),p,"exec")
digest=hashlib.sha256(blob).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(blob).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(blob)
(ROOT/"SHA256.txt").write_text(digest+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.2.3\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("V5.2.3: auto-export all COMPLETED after scan and unique 100pct localized fallback\n")
print("PASS: built MFCase V5.2.3, bytes",len(blob),"SHA256",digest)
