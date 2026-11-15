"""Patch the authoritative MFCase V5.1.1 release for V5.1.2.

Runs in GitHub Actions. No original PDF, case data, or private learning model
is placed in this repository. Fails closed if upstream source differs.
"""
from __future__ import annotations
import ast
import base64
import hashlib
import io
import re
import zipfile
from pathlib import Path

root=Path("release-assets")
original=base64.b64decode((root/"update.base64").read_text().strip(),validate=True)
expected="0376f099f14801e20e55cae0313408dd49e4a70fcbd7a88e7a8ccc652c1f617b"
assert hashlib.sha256(original).hexdigest()==expected, "Source is not the published V5.1.1"
with zipfile.ZipFile(io.BytesIO(original)) as z:
    assert z.testzip() is None
    entries={n:z.read(n) for n in z.namelist()}
    assert "case_number_ocr.py" in entries and "updater_apply.py" in entries
    assert all(n and "/" not in n and "\\" not in n for n in entries)

source=entries["case_number_ocr.py"].decode("utf-8")
assert source.count('self.app_version = "5.1.1"')==1
source=source.replace('self.app_version = "5.1.1"','self.app_version = "5.1.2"',1)

def replace_method(code, name, replacement):
    tree=ast.parse(code)
    matches=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(matches)==1, (name, len(matches))
    node=matches[0]
    lines=code.splitlines(keepends=True)
    code="".join(lines[:node.lineno-1])+replacement.rstrip()+"\n"+"".join(lines[node.end_lineno:])
    compile(code, "case_number_ocr.py","exec")
    return code

helper='''
def _mfcase_header_field_candidates(hits, page_shape):
    """Unique upper-right 3-inch header reading; never elevate body OCR.

    Pick the highest text line within the 3x3 window. Two different numbers
    on that same line are ambiguous and must go to review. Unlabeled reads
    can NEVER auto-export without both verified neighbor filenames matching.
    """
    ph,pw=page_shape[:2]
    span=int(round(3*72*2.2))
    x_left=max(0,pw-span)
    selected=[]
    for hit in hits:
        rect=hit.get("rect")
        if not rect or len(rect)!=4:
            continue
        try:
            x0,y0,x1,y1=map(float,rect)
        except (TypeError,ValueError):
            continue
        number=str(hit.get("number") or "").strip()
        if not re.fullmatch(r"\\d{1,2}-\\d{1,6}",number):
            continue
        if x0>=x_left-4 and x1<=pw+4 and 0<=y0<y1<=min(ph,span)+4:
            selected.append(hit)
    if not selected:
        return []
    selected.sort(key=lambda h:(h["rect"][1],h["rect"][0]))
    first_y=float(selected[0]["rect"][1])
    row_tolerance=max(28.0,min(ph,span)*0.095)
    top_line=[h for h in selected if float(h["rect"][1])<=first_y+row_tolerance]
    return top_line if len({h["number"] for h in top_line})==1 else []


'''
source=replace_method(source,"_mfcase_header_field_candidates",helper)

ocr_method='''
    def _ocr_pdf(self,path):
        """OCR ONLY the page's top-right 3 x 3 inches, at 2.2x raster.

        A 100% labeled printed read qualifies for immediate transfer; an
        unlabeled handwritten candidate requires 95% AND two matching
        independently-read neighbors (enforced by _poll/sequence consensus).
        No readable number in this crop always means manual review.
        """
        doc=pymupdf.open(path)
        try:
            if not len(doc):
                return ("",None,"Empty PDF — manual review required",False,0.0,[],False)
            results=[]
            pages=[]
            any_label=False
            dimension_px=int(round(3*72*2.2))
            for page_index,page in enumerate(doc):
                pix=page.get_pixmap(matrix=pymupdf.Matrix(2.2,2.2),alpha=False)
                image=np.frombuffer(pix.samples,dtype=np.uint8).reshape(
                    pix.height,pix.width,pix.n).copy()
                pages.append(image)
                height,width=image.shape[:2]
                side=min(height,dimension_px)
                crop_width=min(width,dimension_px)
                offset_x=width-crop_width
                region=image[:side,offset_x:].copy()
                # The main OCR detector is NEVER called on the full page.
                labels=self._find_case_field_label_regions(region)
                valid_labels=[x for x in labels if x[4] in ("incident","case")]
                if not valid_labels:
                    try:
                        from PIL import Image,ImageEnhance,ImageOps
                        focused=ImageOps.autocontrast(Image.fromarray(region).convert("RGB"),cutoff=1)
                        focused=ImageEnhance.Contrast(focused).enhance(1.4)
                        doubled=focused.resize(
                            (focused.width*2,focused.height*2),Image.Resampling.BICUBIC)
                        detected=self._find_case_field_label_regions(np.asarray(doubled))
                        valid_labels=[(r[0]/2,r[1]/2,r[2]/2,r[3]/2,r[4])
                                      for r in detected if r[4] in ("incident","case")]
                    except Exception as exc:
                        self._log("Enhanced 3x3 label recognition failed: %s",exc)
                if valid_labels:
                    any_label=True
                    for label in valid_labels:
                        hits=self._read_incident_to_right(region,label[:4])
                        absolute=[]
                        for h in hits:
                            candidate=dict(h)
                            rect=candidate.get("rect")
                            if rect:
                                candidate["rect"]=(int(rect[0]+offset_x),int(rect[1]),
                                                   int(rect[2]+offset_x),int(rect[3]))
                            absolute.append(candidate)
                        if absolute:
                            results.append((page_index,image,absolute,label[4]))
                else:
                    try:
                        hits=self._find_case_candidates(
                            region,x_offset=offset_x,y_offset=0,run_trocr=True,
                            case_pattern_override=re.compile(
                                r"(?<!\\d)(\\d{1,2}\\s*-\\s*\\d{1,6})(?!\\d)"))
                    except Exception as exc:
                        self._log("3x3 header OCR failed: %s",exc)
                        hits=[]
                    inside=[]
                    for h in hits:
                        rect=h.get("rect")
                        if not rect:
                            # A TrOCR-only, unlocalized guess must never
                            # trigger the 95% handwritten auto-approval rule.
                            continue
                        x0,y0,x1,y1=map(float,rect)
                        if (x0>=offset_x-4 and x1<=width+4 and
                                y0>=0 and y1<=side+4):
                            inside.append(h)
                    if inside:
                        upper=_mfcase_header_field_candidates(inside,image.shape)
                        if upper and max(float(h.get("score") or 0) for h in upper)>=95.0:
                            results.append((page_index,image,upper,"header"))
                        else:
                            results.append((page_index,image,inside,"fallback"))
            if any_label:
                # If a caption is visible, body text and unlabeled text
                # cannot compete with the formal case-number field.
                results=[r for r in results if r[3] in ("incident","case")]
            if not results:
                index=max(0,len(pages)-1)
                why=("Case-number caption in 3x3 area but no complete adjacent "
                     "case number — MANUAL REVIEW" if any_label else
                     "No case number found in top-right 3x3 inches — MANUAL REVIEW")
                return ("",(index,pages[-1],"3x3 case field not read",None,[]),
                        why,False,0.0,[],False)
            def rank(entry):
                index,image,hits,kind=entry
                return ({"incident":3,"case":2,"header":1,"fallback":0}[kind],
                        max((float(h.get("score") or 0) for h in hits),default=0.0))
            index,image,hits,kind=max(results,key=rank)
            best=_select_primary_case_hit(hits,kind,image.shape)
            number=best["number"]
            engines=list(best.get("engines") or [])
            engine_names={e.split("-",1)[0] for e in engines}
            agree="EasyOCR" in engine_names and "TrOCR" in engine_names
            distinct={h["number"] for h in hits}
            across={h["number"] for r in results if r[3]==kind for h in r[2]}
            ambiguous=len(distinct)>1 or len(across)>1
            visuals=[]
            for h in hits:
                rect=h.get("rect")
                visuals.append({
                    "number":h["number"],
                    "rect":[int(v) for v in rect] if rect else None,
                    "score":float(h.get("score") or 0),
                    "engines":list(h.get("engines") or []),
                    "label_type":h.get("label_type"),
                    "label_distance":0.0,
                    "learned_location":False})
            if ambiguous:
                note="3x3 case field has conflicting OCR reads — MANUAL REVIEW"
            elif kind=="incident":
                note="Case field right-of-label full number — 3x3 labeled printed OCR"
            elif kind=="case":
                note="CASE NUMBER field full number — 3x3 labeled printed OCR"
            elif kind=="header":
                note="Upper-right header 3x3 handwritten candidate — verify 95% and two matching neighbors"
            else:
                note="Upper-right fallback within 3x3 — MANUAL REVIEW"
            if len(doc)>1:
                note+=f" • page {index+1} of {len(doc)}"
            return (number,(index,image,f"{number} — {kind}",
                    best.get("rect"),visuals),note,
                    "TrOCR" in engine_names,float(best.get("score") or 0),
                    engines,agree)
        finally:
            doc.close()


'''
source=replace_method(source,"_ocr_pdf",ocr_method)

# Keep the on-disk learned 3-inch location mapping consistent with scanning.
old_size='int(round(5 * 72 * 2.2))'
assert source.count(old_size)==2,("learning search sizes",source.count(old_size))
source=source.replace(old_size,'int(round(3 * 72 * 2.2))')
source=source.replace("top-right 5 x 5 inch search window","top-right 3 x 3 inch search window")
source=source.replace("top-right 5x5","top-right 3x3")

# Only OCR from a recognized labeled field may immediately auto-send.
# It MUST be a 100%-matching printed (non-TrOCR) read. Handwritten headers
# need two same-case neighboring filename scans, even when the score is 100%.
start_token='                    auto_done = False\n'
end_token='                    if not auto_done and self.tree.exists(iid):\n'
assert source.count(start_token)==1 and source.count(end_token)==1
start=source.index(start_token)
end=source.index(end_token,start)
replacement='''                    auto_done = False
                    labeled = r["scan_source"] in ("incident", "case")
                    printed = labeled and not bool(hand)
                    perfectly_read = float(score or 0) >= 99.95
                    if (normalized_candidate and self.auto_label_var.get()
                            and printed and perfectly_read
                            and not r["scan_ambiguous"]):
                        block_reason = self._auto_label_block_reason(
                            score, agreement, normalized_candidate)
                        if block_reason is None:
                            r["confirmed"] = normalized_candidate
                            r["auto_labeled"] = True
                            r["manual_approved"] = True
                            r["status"] = "Labeled printed case: 100% OCR — sending to Kurt"
                            self._move_to_completed(iid)
                            auto_done = True
                            self._run_outcomes["auto_approved"] = (
                                self._run_outcomes.get("auto_approved", 0) + 1)
                        else:
                            r["status"] = note + " • Auto-confirm blocked: " + block_reason
                    elif r["scan_source"] == "header":
                        if normalized_candidate and float(score or 0) >= 95.0 and not r["scan_ambiguous"]:
                            r["status"] = (
                                f"Unlabeled handwritten {normalized_candidate}: {score:.1f}% "
                                "— waiting for BOTH matching sequence neighbors")
                        else:
                            r["status"] = (
                                "Unlabeled 3x3 case: confidence below 95% or competing "
                                "OCR readings — MANUAL REVIEW")
                    elif labeled and normalized_candidate:
                        r["status"] = (
                            f"Labeled case OCR {score:.1f}% — "
                            "manual review unless matching sequence neighbors confirm")
'''
source=source[:start]+replacement+source[end:]

# A unique 95%+ header reading may serve as an independent *neighbor*
# but cannot auto-export by itself.
old='float(record.get("score") or 0)<99.5'
assert source.count(old)==1
source=source.replace(old,'float(record.get("score") or 0)<95.0')

# Existing sequence logic can auto-infer an entirely blank middle page; that
# contradicts the explicit 3x3 manual-review rule. Do not do that anymore.
# Conflicting candidates can never be silently overwritten.
seq_begin='            strong_read = (candidate and not r.get("scan_ambiguous")'
seq_end='            r["sequence_original_candidate"]=candidate'
assert source.count(seq_begin)==1 and source.count(seq_end)==1
begin=source.index(seq_begin)
end=source.index(seq_end,begin)
new_seq='''            source_kind = r.get("scan_source")
            observed_score = float(r.get("score") or 0)
            if not candidate or not CASE_RE.fullmatch(candidate):
                # Even matching neighbors cannot invent a missing 3x3 number.
                r["status"] = "No readable case number in 3x3 area — MANUAL REVIEW"
                continue
            if source_kind not in ("incident", "case", "header"):
                continue
            if source_kind == "header":
                if r.get("scan_ambiguous") or observed_score < 95.0:
                    continue
            if candidate != consensus:
                r["sequence_conflict"] = True
                r["status"] = (
                    f"3x3 OCR {candidate} disagrees with both neighbors {consensus} "
                    "— MANUAL REVIEW")
                if r.get("auto_labeled"):
                    r["confirmed"] = ""
                    r["auto_labeled"] = False
                    r["manual_approved"] = False
                    if self.completed_tree.exists(iid):
                        self.completed_tree.delete(iid)
                    r["completed_iid"] = None
                self._insert_scan_row(iid,r)
                updated += 1
                continue
            if r.get("auto_labeled") and r.get("manual_approved"):
                continue
'''
source=source[:begin]+new_seq+source[end:]
# When a weak labeled read and its matching neighbors agree, preserve
# original confidence (never counterfeit a 100% printed reading).
source=source.replace(
    'f"Sequence-inferred {consensus} from consecutive originals "',
    'f"Sequence-confirmed {consensus} in 3x3 from consecutive originals "')
# Do not call a visible detected 95% reading fabricated OCR digits.
source=source.replace('f"{left[0]} / {right[0]} — no OCR digits fabricated"',
                      'f"{left[0]} / {right[0]} — original OCR score retained"')

# On an atomic transfer the existing timestamp follows the file by default.
# Windows Explorer sorts by filesystem Date modified, NOT the PDF's /ModDate
# dictionary. Touch the destination after the successful move without
# rewriting the original evidence PDF bytes or embedded document metadata.
old='''                    os.replace(str(source), str(destination))
                    reserved_by_us = False'''
new='''                    os.replace(str(source), str(destination))
                    reserved_by_us = False
                    try:
                        # Refresh Windows Explorer "Date modified" when MFCase
                        # finishes processing; PDF contents remain unchanged.
                        os.utime(destination, None)
                    except OSError as metadata_error:
                        self._log(
                            "Transfer succeeded, but Date modified could not be "
                            "updated for %s: %s", destination, metadata_error)'''
assert source.count(old)==1
source=source.replace(old,new,1)

# Prevent stale misleading fixed 90% user-facing strings.
source=source.replace('"Auto-confirmed (90%+) — ready for export"',
                      '"100% labeled printed OCR — ready for export"')
source=source.replace('"Confirmed by matching sequence neighbors (no OCR score) — ready for export"',
                      '"Confirmed by matching neighbors, OCR score retained — ready for export"')
source=source.replace("V5.1.1","V5.1.2")

compile(source,"case_number_ocr.py","exec")
tree=ast.parse(source)
functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
assert "_mfcase_header_field_candidates" in functions
ns={"re":re}
exec(compile(ast.Module(body=[functions["_mfcase_header_field_candidates"]],
                        type_ignores=[]),"header_tests","exec"),ns)
choose=ns["_mfcase_header_field_candidates"]
shape=(1700,1320,3)
def hit(num,score,x0,y0):
    return {"number":num,"score":score,"rect":(x0,y0,x0+75,y0+38)}
assert choose([hit("08-19",95.1,1150,40)],shape)[0]["number"]=="08-19"
assert choose([hit("08-14",99.0,1170,240),hit("08-19",96,1150,18)],shape)[0]["number"]=="08-19"
assert choose([hit("08-14",100,1170,18),hit("08-19",100,1150,17)],shape)==[]
assert choose([hit("08-19",100,300,30)],shape)==[]
assert choose([hit("08-19",100,1200,490)],shape)==[]
assert choose([hit("08-19",100,1200,20),hit("08-19",96,1220,29)],shape)
assert not choose([{"number":"08-19","score":100,"rect":None}],shape)
assert 'dimension_px=int(round(3*72*2.2))' in source
assert 'not bool(hand)' in source and 'perfectly_read' in source
assert 'observed_score < 95.0' in source
assert 'No readable case number in 3x3 area' in source
assert 'os.utime(destination, None)' in source
assert 'self.app_version = "5.1.2"' in source
assert '5*72*2.2' not in ast.get_source_segment(source, next(
    n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="_ocr_pdf"))
entries["case_number_ocr.py"]=source.encode("utf-8")
entries["CHANGELOG_V5_1_2.txt"]=(
    "V5.1.2 — strict top-right 3in x 3in OCR search\n"
    "SOURCE: Published V5.1.1 release (checksum pinned).\n"
    "No case number in the 3x3 zone => manual review, even with neighbors.\n"
    "Labeled printed 100% non-conflicting OCR => auto-confirm and Kurt transfer.\n"
    "Unlabeled handwritten 95%+ => auto-confirm only if both original filename\n"
    "neighbors independently match the same case; otherwise manual review.\n"
    "Conflict, ambiguous score, or out-of-zone text never auto-approved.\n"
    "Learned field rectangles now use 3x3 coordinates, not 5x5.\n"
    "On transfer, Windows file Date modified is set to transfer time;\n"
    "PDF bytes and internal /ModDate remain untouched to preserve originals.\n"
    "All OCR-learning, UI selection, update code, and leading-zero rules retained.\n"
).encode()
with io.BytesIO() as temp:
    with zipfile.ZipFile(temp,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as out:
        for n,data in entries.items():
            out.writestr(n,data)
    payload=temp.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as verify:
    assert verify.testzip() is None
    assert set(verify.namelist())==set(entries)
    for n in ["case_number_ocr.py","updater_apply.py"]:
        compile(verify.read(n),n,"exec")
digest=hashlib.sha256(payload).hexdigest()
(root/"update.base64").write_text(base64.b64encode(payload).decode("ascii"))
(root/"CaseNumberOCR_Update.zip").write_bytes(payload)
(root/"SHA256.txt").write_text(digest+"\n")
(root/"RELEASE_TAG.txt").write_text("v5.1.2\n")
(root/"PUBLISH_REQUEST.txt").write_text("V5.1.2 strict 3-inch OCR region and Windows Date modified correction\n")
print("PASS: V5.1.2 build and 3x3/95%/printed-100%/timestamp guards")
print("SHA256:",digest,"BYTES:",len(payload))
