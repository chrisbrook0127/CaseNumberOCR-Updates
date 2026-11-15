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
