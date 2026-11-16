"""Build MFCase V5.2.5 from immutable published V5.2.4.

Repair case-number selection for hand-written 08-44 at top-right beside fax
P.005/015 / P.007/015 headers. Preserve SCAN, REVIEW SELECTED, learning,
3x3 limits, sequence-range consensus, root-only listing, and COMPLETED moves.
"""
from pathlib import Path
import ast,base64,hashlib,io,re,subprocess,zipfile

ROOT=Path("release-assets")
base=base64.b64decode(subprocess.check_output(
    ["git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),
    validate=True)
assert hashlib.sha256(base).hexdigest()=="93daa71262881dadf9d1436b4eb8218b92194011002f8599cacd356271b36d32"
with zipfile.ZipFile(io.BytesIO(base)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    src=z.read("case_number_ocr.py").decode()
    updater=z.read("updater_apply.py")

def patch(old,new,count=1):
    global src
    assert src.count(old)==count,(src.count(old),count,old[:160])
    src=src.replace(old,new,count)

def replace_method(name,body):
    global src
    t=ast.parse(src)
    nodes=[n for n in ast.walk(t) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(nodes)==1,(name,len(nodes))
    n=nodes[0]
    lines=src.splitlines(keepends=True)
    src="".join(lines[:n.lineno-1])+body.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(src,"case_number_ocr.py","exec")

patch('self.app_version = "5.2.4"','self.app_version = "5.2.5"')
for old,new in (
    ("M.F. Case Number OCR — V5.2.4","M.F. Case Number OCR — V5.2.5"),
    ("Case Number OCR & Renamer — V5.2.4","Case Number OCR & Renamer — V5.2.5"),
    ("Application version: V5.2.4","Application version: V5.2.5"),
    ("MFCase-Connectivity/5.2.4","MFCase-Connectivity/5.2.5")):
    src=src.replace(old,new)

# 1. Strictly prefer the handwritten margin at the FAR RIGHT, not a fax
#    page counter slightly to its left. This source-independent function is
#    also used by fallbacks when OCR sees both 08-15 and 08-44.
choose= r'''
def _select_primary_case_hit(hits, source, image_shape):
    """Choose a physical case-number location before scoring OCR variants.

    The true handwritten upper-right margin is right of fax page counters,
    such as '122 P.005/015'. A high-confidence fax interpretation cannot
    beat the real case-number box merely by having a greater OCR score.
    """
    if not hits:
        raise ValueError("Cannot select a candidate from an empty list")
    height,width=image_shape[:2]
    def engine_quality(hit):
        engines={str(v) for v in hit.get("engines") or []}
        raw=any(v=="EasyOCR" or
                (v.startswith("EasyOCR-") and "enhanced" not in v.lower())
                for v in engines)
        agree=any(v.startswith("TrOCR") for v in engines) and raw
        return (agree,raw,float(hit.get("score") or 0))
    if source in ("fallback","header"):
        margin=[]
        for h in hits:
            rect=h.get("rect")
            if not rect:
                continue
            x0,y0,x1,y1=map(float,rect)
            # Printed fax counters and body numbers are left of the
            # hand-marked case number at the very top-right edge.
            if ((x0+x1)/2>=width*0.82 and y0<=height*0.24
                    and 0<=y0<y1):
                margin.append(h)
        if margin:
            return max(margin,key=lambda h:(
                (float(h["rect"][0])+float(h["rect"][2]))/(2*width),
                engine_quality(h)))
    return max(hits,key=engine_quality)
'''
replace_method("_select_primary_case_hit",choose)

# 2. Existing header classifier incorrectly rejects an entire top row if
#    OCR creates even one false alternative. Only candidates in the actual
#    far-right margin compete; other top-of-page fax/court numbers do not.
header=r'''
def _mfcase_header_field_candidates(hits,page_shape):
    """Candidate readings in the physical right-margin case-number location."""
    ph,pw=page_shape[:2]
    span=int(round(3*72*2.2))
    xmin=pw-min(span,pw)
    expected=re.compile(r"\d{1,2}-\d{1,7}\Z")
    candidates=[]
    for h in hits:
        rect=h.get("rect")
        value=str(h.get("number") or "")
        if not rect or not expected.fullmatch(value):
            continue
        try:
            x0,y0,x1,y1=map(float,rect)
        except (ValueError,TypeError):
            continue
        if (x0>=xmin-4 and x1<=pw+4 and y0>=0 and
                y1<=min(ph,span)+4 and y0<y1):
            candidates.append(h)
    if not candidates:
        return []
    margin=[h for h in candidates
            if (float(h["rect"][0])+float(h["rect"][2]))/2>=pw*.82
            and float(h["rect"][1])<=ph*.24]
    if not margin:
        return [] # Not a well-located header; do not authorize a fax page number.
    # Limit independent candidates to the physical right-margin word row.
    first=min(float(h["rect"][1]) for h in margin)
    return [h for h in margin if abs(float(h["rect"][1])-first)<=max(24.,span*.06)]
'''
replace_method("_mfcase_header_field_candidates",header)

# 3. The OCR candidate generator runs a full 3"x3" crop plus enhanced
#    variants. TrOCR occasionally turns a printed fax page fraction
#    "P.005/015" into a fictional "08-15". When the ORIGINAL detected text
#    contains a slash and has no genuine YY-NN number, it MUST NOT be
#    treated as a case-number source for downstream TrOCR guesses.
patch('''            if run_trocr and likely_case_line:
                try:''',
'''            # Fax pagination, e.g. "122 P.005/015", is not a case number.
            # Never let TrOCR hallucinations turn a slash into an 08- prefix.
            fax_pagination=bool(re.search(
                r"(?:\bP\s*[.:-]?\s*\d{1,4}\s*/\s*\d{1,4}\b|"
                r"\b\d{1,4}\s*/\s*\d{1,4}\b)",
                text,re.I) and not strict_matches)
            if run_trocr and likely_case_line and not fax_pagination:
                try:''')
patch('''            for _bbox, raw_text, _raw_score, (bx0, by0, bx1, by1) in parsed:
                if sum(ch.isdigit() for ch in raw_text) < 2 and "-" not in raw_text:''',
'''            for _bbox, raw_text, _raw_score, (bx0, by0, bx1, by1) in parsed:
                if ("/" in str(raw_text) and
                    not case_pattern.search(str(raw_text))):
                    # Skip fax counters in the enhanced OCR pass too.
                    continue
                if sum(ch.isdigit() for ch in raw_text) < 2 and "-" not in raw_text:''')

# 4. A localized right-margin crop gets its OWN OCR pass. The chosen
#    number still must be inside the original 3x3 region; this only improves
#    recognition of small handwritten case numbers and their trailing digits.
patch('''                    if inside:
                        upper=_mfcase_header_field_candidates(inside,image.shape)''',
'''                    # Focus on rightmost handwriting. This is a subset of
                    # the existing 3x3 OCR region, never a full-page read.
                    margin_x=int(round(crop_width*.55))
                    margin_h=min(side,int(round(1.5*72*2.2)))
                    margin_crop=region[:margin_h,margin_x:].copy()
                    if margin_crop.size:
                        try:
                            focused_hits=self._find_case_candidates(
                                margin_crop,x_offset=offset_x+margin_x,
                                y_offset=0,run_trocr=True,
                                case_pattern_override=re.compile(
                                    r"(?<!\d)(\d{1,2}\s*-\s*\d{1,7})(?!\d)"))
                            focused_valid=[]
                            for h in focused_hits:
                                rect=h.get("rect")
                                if rect and float(rect[0])>=offset_x+margin_x-4:
                                    focused_valid.append(h)
                            self._diag_event("handwritten_margin_pass",
                                page=page_index+1,
                                region=[offset_x+margin_x,0,width,margin_h],
                                hits=self._diag_hits(focused_valid))
                            inside=self._merge_case_hits(inside,focused_valid)
                        except Exception as exc:
                            self._log("Top-right margin OCR pass failed: %s",exc)
                    if inside:
                        upper=_mfcase_header_field_candidates(inside,image.shape)''')

# 5. Never let a fax-header number veto a real handwritten case at the
#    right margin. Apply this filter BEFORE candidate selection and before
#    ambiguity is calculated, preserving original reads in JSONL diagnostics.
region_methods=r'''
    @staticmethod
    def _is_corner_case_rect(rect,image):
        """Require the physical upper-right handwritten case-number zone."""
        if rect is None or image is None:
            return False
        try:
            h,w=image.shape[:2]
            x0,y0,x1,y1=map(float,rect)
            return (x0>=0 and y0>=0 and x1>x0 and y1>y0
                    and (x0+x1)/2>=w*.82
                    and y0<=h*.24 and x1<=w+6)
        except (TypeError,ValueError,AttributeError):
            return False

    def _prefer_case_margin(self,hits,kind,image):
        if kind not in ("header","fallback") or not hits:
            return hits
        strong_margin=[h for h in hits if
            self._is_corner_case_rect(h.get("rect"),image)]
        if not strong_margin:
            self._diag_event("margin_priority",source=kind,
                outcome="no_corner_case_read",
                candidates=self._diag_hits(hits))
            return hits
        # Only variants in the same PHYSICAL case-number zone are compared.
        # Any separate number to its left is outside the intended case field
        # and must not create a false conflict.
        ignored=[h for h in hits if h not in strong_margin]
        self._diag_event("margin_priority",source=kind,
            outcome="right_margin_priority",kept=self._diag_hits(strong_margin),
            ignored=[{"candidate":x,"reason":"outside_upper_right_case_field"}
                     for x in self._diag_hits(ignored)])
        return strong_margin

'''
patch('''    def _resolve_ocr_alternates(self,hits,kind,best):''',
      region_methods+'''    def _resolve_ocr_alternates(self,hits,kind,best):''')
patch('''            hits=self._diag_field_filter(hits,kind)
            if not hits:''',
'''            hits=self._diag_field_filter(hits,kind)
            hits=self._prefer_case_margin(hits,kind,image)
            if not hits:''')
patch('''                _filtered=self._diag_field_filter(_hits,kind)
                if not _filtered:''',
'''                _filtered=self._diag_field_filter(_hits,kind)
                _filtered=self._prefer_case_margin(_filtered,kind,_image)
                if not _filtered:''')

# Fallback auto approval is allowed ONLY for a well-localized OCR box in
# the actual top-right corner. A fax pagination guess elsewhere may appear
# in REVIEW, but cannot automatically rename a PDF.
patch('''                        and snip[3] is not None
                    )''',
'''                        and snip[3] is not None
                        and self._is_corner_case_rect(snip[3],snip[1])
                    )''')
patch('''                    or len(snip)<4 or snip[3] is None):''',
'''                    or len(snip)<4 or snip[3] is None
                    or not self._is_corner_case_rect(snip[3],snip[1])):''')

# For low-confidence OCR alternatives (e.g. 88% 08-64), do not mark
# the fax-derived 91% "08-15" as the case source or a valid anchor.
# If the proper 08-44 is still below 95%, preserve it as a review read
# and let the already-established matching before/after range rule
# supply a verified case number. The independent anchor threshold stays 95%.

# Make the reviewing panel explain why a potential fax-header number
# was rejected. The crop preview and manual box selection remain unchanged.
patch('''            else:
                note="Upper-right fallback within 3x3 — unique 95% localized read may auto-approve"''',
'''            else:
                note=("Upper-right fallback in 3x3 — right-margin case box "
                      "takes priority over fax pagination; 95% required for "
                      "direct approval, otherwise before/after or REVIEW")''')

compile(src,"case_number_ocr.py","exec")
t=ast.parse(src)
assert 'self.app_version = "5.2.5"' in src
assert 'root.title("M.F. Case Number OCR — V5.2.5")' in src
assert 'handwritten_margin_pass' in src
assert 'outside_upper_right_case_field' in src
assert '_is_corner_case_rect(snip[3],snip[1])' in src
assert 'fax_pagination' in src
assert 'dimension_px=int(round(3*72*2.2))' in src
assert 'def _apply_sequence_consensus(self):' in src
assert 'def _make_scan_preview(' in src
assert 'def _scan_progress_update(' in src
assert 'text="REVIEW SELECTED"' in src
assert 'os.utime(destination, None)' in src
for name in ["_find_case_candidates","_prefer_case_margin","_ocr_pdf",
             "_apply_sequence_consensus","_poll"]:
    assert len([n for n in ast.walk(t) if isinstance(n,ast.FunctionDef) and n.name==name])==1

out=io.BytesIO()
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",src)
    z.writestr("updater_apply.py",updater)
payload=out.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    for name in z.namelist():compile(z.read(name),name,"exec")
digest=hashlib.sha256(payload).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(payload).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(payload)
(ROOT/"SHA256.txt").write_text(digest+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.2.5\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text(
    "MFCase V5.2.5 — rightmost handwritten case OCR priority, fax header suppression, "
    "95% corner-only approval, preserved range fallback and progress\n")
print("PASS build V5.2.5 SHA256",digest,"bytes",len(payload))
