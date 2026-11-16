"""Build V5.2.6 from the exact V5.2.5 published case OCR application.

All case-number approval, review, learning, sequence-anchor and export paths
require the literal '08-' prefix. Do not modify PDFs, existing learning files,
or completed transfers. Preserve all other established functionality.
"""
from pathlib import Path
import ast,base64,hashlib,io,zipfile

ROOT=Path("release-assets")
raw=base64.b64decode((ROOT/"update.base64").read_text(),validate=True)
assert hashlib.sha256(raw).hexdigest()=="79e6b88976d1c874ba5295458814eaf8d00e3a8368d6ea591fc2e88c24348c3a"
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    original=z.read("case_number_ocr.py").decode()
    updater=z.read("updater_apply.py")
assert original==Path("development/v5.2.5_original_case_number_ocr.py").read_text()
s=original

def swap(a,b,n=1):
    global s
    count=s.count(a)
    assert count==n,("Unexpected original source layout",count,n,a[:145])
    s=s.replace(a,b)

def method(name,code):
    global s
    t=ast.parse(s)
    nodes=[x for x in ast.walk(t) if isinstance(x,ast.FunctionDef) and x.name==name]
    assert len(nodes)==1,(name,len(nodes))
    n=nodes[0];lines=s.splitlines(keepends=True)
    s="".join(lines[:n.lineno-1])+code.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(s,"case_number_ocr.py","exec")

# One non-negotiable two-character year. The OCR cannot infer 08 from another
# year or from a standalone number without an explicit 08-hyphen in the read.
swap('CASE_RE = re.compile(r"(?<!\\d)(\\d{1,2}-\\d{1,6})(?!\\d)")',
     'REQUIRED_CASE_YEAR = "08"\nCASE_RE = re.compile(r"(?<!\\d)(08-\\d{1,7})(?!\\d)")')
swap('INCIDENT_NUMBER_RE = re.compile(r"(?<!\\d)(\\d{1,2}\\s*-\\s*\\d{1,6})(?!\\d)")',
     'INCIDENT_NUMBER_RE = re.compile(r"(?<!\\d)(08\\s*-\\s*\\d{1,7})(?!\\d)")')
swap('expected=re.compile(r"\\d{1,2}-\\d{1,7}\\Z")',
     'expected=re.compile(r"08-\\d{1,7}\\Z")')
swap('match=re.fullmatch(r"(\\d{1,2})-(\\d{1,7})", original)',
     'match=re.fullmatch(r"(08)-(\\d{1,7})", original)')
swap('self.scan_year_prefix = ""','self.scan_year_prefix = REQUIRED_CASE_YEAR')
swap('self.year_var.set(year)','self.year_var.set(REQUIRED_CASE_YEAR)')
# Ignore year values from old settings, but leave their files intact.
swap('            saved = {}\n            state_error = False',
     '            year = REQUIRED_CASE_YEAR  # Historical settings cannot change the required 08 prefix.\n            saved = {}\n            state_error = False')
swap('''            if sent:
                item["status"]=f"Sent to COMPLETED: {pdf.name}"''',
'''            if item["confirmed"] and not CASE_RE.fullmatch(
                    normalize_case_number(item["confirmed"])):
                # Historical non-08 approval is not valid in this release.
                # Keep PDFs and learning; mark the root record for correction.
                previous_case=str(item["confirmed"])
                item.update(confirmed="",manual_approved=False,
                            auto_labeled=False,send_after_manual_approval=False,
                            sequence_inferred=False,sequence_conflict=False,
                            sequence_anchors=[],sequence_suggestion="",
                            status=f"REVIEW — saved {previous_case} rejected; 08- required")
                self._diag_event("saved_case_year_rejected",file=pdf.name,
                    case=previous_case,reason="required_literal_08_prefix")
            if sent:
                item["status"]=f"Sent to COMPLETED: {pdf.name}"''')

method("_year_prefix",'''
    def _year_prefix(self):
        """This release processes only 2008 case numbers written as 08-NNN."""
        if self.year_var.get().strip()!=REQUIRED_CASE_YEAR:
            self.year_var.set(REQUIRED_CASE_YEAR)
        return REQUIRED_CASE_YEAR
''')

# Year filtering is a non-optional hard gate. "8-19", "09-19", "2008-19",
# "19", and "08" without a hyphen/case digits are all invalid.
swap('expected=str(getattr(self,"scan_year_prefix","") or self._year_prefix() or "")',
     'expected=REQUIRED_CASE_YEAR')
swap('''            if expected.isdigit() and int(match.group(1))!=int(expected):
                ignored.append({"candidate":self._diag_hits([h])[0],"reason":"year_mismatch"})''',
'''            if match.group(1)!=REQUIRED_CASE_YEAR:
                ignored.append({"candidate":self._diag_hits([h])[0],
                                "reason":"required_literal_08_prefix_missing"})''')
swap('''        if not CASE_RE.fullmatch(normalized):
            return "OCR result is not a valid case number."''',
'''        if not CASE_RE.fullmatch(normalized):
            return "Case number must start with 08- followed by 1–7 case digits."''')
swap('''        year = self.scan_year_prefix or self._year_prefix()
        observed_year=normalized.split("-",1)[0]
        if year and not (year.isdigit() and observed_year.isdigit() and
                         int(observed_year)==int(year)):
            return "Case number does not match the selected year."''',
'''        observed_year=normalized.split("-",1)[0]
        if observed_year!=REQUIRED_CASE_YEAR:
            return "Case number must start with the literal 08- prefix."''')
swap('''        year=self.scan_year_prefix or self._year_prefix()
        if (year and year.isdigit() and
                int(case.split("-",1)[0])!=int(year)):
            return None''',
'''        if not case.startswith(REQUIRED_CASE_YEAR+"-"):
            return None''')

# Every detection route, including high-confidence printed, handwritten
# header, 3"x3" fallback and targeted right-margin OCR, searches only 08-NN.
swap(r'r"(?<!\d)(\d{1,2}\s*-\s*\d{1,6})(?!\d)"',
     r'r"(?<!\d)(08\s*-\s*\d{1,7})(?!\d)"',2)
swap(r'r"(?<!\d)(\d{1,2}\s*-\s*\d{1,7})(?!\d)"',
     r'r"(?<!\d)(08\s*-\s*\d{1,7})(?!\d)"',1)
swap('''        year_prefix = ""  # The selected batch year must never alter or filter OCR output.''',
'''        year_prefix = REQUIRED_CASE_YEAR  # OCR recognizes only explicit 08- case numbers.''')

# No bad year enters shared learning, even when a UI or plugin action calls
# the learning helper without first validating the manual review Entry.
swap('''    def _save_learning_example(self, record, confirmed_number):
        if not self.learning_dir:''',
'''    def _save_learning_example(self, record, confirmed_number):
        confirmed_number=normalize_case_number(confirmed_number)
        if not CASE_RE.fullmatch(confirmed_number):
            raise ValueError("Learning accepts only 08- followed by 1–7 case digits.")
        if not self.learning_dir:''')
swap('''    def _save_learning_label_for_file(self, source_name, confirmed_number):
        """Keep the ground-truth label consistent after a filename correction."""
        if not self.learning_dir:''',
'''    def _save_learning_label_for_file(self, source_name, confirmed_number):
        """Keep the ground-truth label consistent after a filename correction."""
        if not CASE_RE.fullmatch(normalize_case_number(confirmed_number)):
            raise ValueError("Case number must begin with 08- before saving learning.")
        if not self.learning_dir:''')
swap('''            if not example.get("image") or not example.get("confirmed_case_number"):
                continue''',
'''            if (not example.get("image") or not CASE_RE.fullmatch(
                    str(example.get("confirmed_case_number") or ""))):
                continue''')
swap('''            if current_year and not str(example.get("confirmed_case_number", "")).startswith(current_year + "-"):
                continue''',
'''            if (not CASE_RE.fullmatch(str(example.get("confirmed_case_number") or ""))
                    or not CASE_RE.fullmatch(str(example.get("ocr_candidate") or ""))):
                continue''')
swap('''            if ex.get("image") and ex.get("confirmed_case_number")
        ]''',
'''            if ex.get("image") and CASE_RE.fullmatch(
                str(ex.get("confirmed_case_number") or ""))
        ]''')
# Persist the enforced 08 year even if an old settings file had another year.
swap('json.dumps({"year": self.year_var.get().strip()}, indent=2)',
     'json.dumps({"year": REQUIRED_CASE_YEAR}, indent=2)',2)

# Rejected 09-xx at high confidence must never be silently reassigned by the
# range consensus. Weak/unreadable interior files can still inherit valid
# 08-xx from independent 08-xx endpoints.
swap('''                valid=bool(CASE_RE.fullmatch(original))
                if (valid and score>=95.0 and
                    numeric(original)!=numeric(consensus)):''',
'''                valid=bool(CASE_RE.fullmatch(original))
                other_year=bool(re.fullmatch(r"\\d{1,2}-\\d{1,7}",original))
                if (other_year and not valid and score>=95.0):
                    rec["status"]=(
                        f"{score:.1f}% OCR {original} lacks required 08- prefix; MANUAL REVIEW")
                    self._diag_event("sequence_range_rejected",
                        file=Path(path).name,reason="confident_non_08_year",
                        original=original,score=score)
                    self._insert_scan_row(iid,rec)
                    continue
                if (valid and score>=95.0 and
                    numeric(original)!=numeric(consensus)):''')

swap('''"Enter exactly the digits read, in the form 1–2 digits, a hyphen, and 1–6 digits. No year or leading digits will be added."''',
     '''"Only 08- followed by 1–7 case digits is valid. No year will be added or corrected automatically."''')
swap('''err.config(text="Enter exactly the reading: 1–2 digits, a hyphen, and 1–6 digits. No year or leading digits are added.")''',
     '''err.config(text="Use 08- followed by 1–7 case digits. Other years and missing 08- are not accepted.")''')
swap('''text="Confirmed case number (YY- plus 1–7 digits):"''',
     '''text="Confirmed case number (08- plus 1–7 digits):"''')

swap('''                    elif not normalized_candidate:
                        r["status"]="No usable case number in 3x3 — checking before/after fallback"''',
'''                    elif not normalized_candidate:
                        r["status"]="No valid 08- case number in 3x3 — checking before/after fallback"''')

# Do not discard past PDFs, logs, shared learning or completed files.
# Preserve the existing source, versioned after a validation pass.
s=s.replace("5.2.5","5.2.6")
compile(s,"case_number_ocr.py","exec")
tree=ast.parse(s)
assert 'self.app_version = "5.2.6"' in s
assert 'root.title("M.F. Case Number OCR — V5.2.6")' in s
assert 'REQUIRED_CASE_YEAR = "08"' in s
assert 'CASE_RE = re.compile(r"(?<!\\d)(08-\\d{1,7})(?!\\d)")' in s
assert 'def _apply_sequence_consensus(self):' in s
assert 'def _make_scan_preview(' in s
assert 'def _scan_progress_update(' in s
assert 'text="REVIEW SELECTED"' in s
assert 'os.utime(destination, None)' in s
assert 'dimension_px=int(round(3*72*2.2))' in s

Path("development/v5.2.6_case_number_ocr.py").write_text(s)
out=io.BytesIO()
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",s)
    z.writestr("updater_apply.py",updater)
release=out.getvalue()
with zipfile.ZipFile(io.BytesIO(release)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    for name in z.namelist():compile(z.read(name),name,"exec")
digest=hashlib.sha256(release).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(release).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(release)
(ROOT/"SHA256.txt").write_text(digest+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.2.6\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("MFCase 5.2.6: strict literal 08- case year across all approval paths\n")
print("PASS V5.2.6 build",len(release),"bytes","sha256",digest)
