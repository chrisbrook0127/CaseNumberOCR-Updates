"""Build MFCase V5.1.5 from the immutable, published V5.1.4 source.

Use scan *read order* instead of original filename gaps for sequence consensus.
Also fix the title bar and make Windows update-install errors visible.
"""
from pathlib import Path
import ast,base64,hashlib,io,re,zipfile,subprocess

ROOT=Path("release-assets")
raw=base64.b64decode(subprocess.check_output(
    ["git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),validate=True)
assert hashlib.sha256(raw).hexdigest()=="8830ce832e78f5e4eeece7e45de820c4b9fa6b6ce1e6c95c01b16a08a28dabe2"
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    app=z.read("case_number_ocr.py").decode("utf-8")
    helper=z.read("updater_apply.py").decode("utf-8")
assert app.count('self.app_version = "5.1.4"')==1
app=app.replace('self.app_version = "5.1.4"','self.app_version = "5.1.5"',1)
assert app.count('root.title("M.F. Case Number OCR — V5.1.4")')==1
app=app.replace('root.title("M.F. Case Number OCR — V5.1.4")',
                'root.title("M.F. Case Number OCR — V5.1.5")',1)

def replace_method(src,method,replacement):
    t=ast.parse(src)
    nodes=[x for x in ast.walk(t) if isinstance(x,ast.FunctionDef) and x.name==method]
    assert len(nodes)==1,(method,len(nodes))
    n=nodes[0]
    lines=src.splitlines(keepends=True)
    src="".join(lines[:n.lineno-1])+replacement.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(src,"patched_case_number_ocr.py","exec")
    return src

index=r'''
    def _sequence_index(self):
        """Freeze independent OCR reads in the exact naturally sorted scan queue.

        Filenames need NOT have consecutive numeric suffixes. An unreadable or
        excluded file occupies its position and cannot be silently skipped.
        Snapshot values prevent an earlier approval changing a later comparison.
        """
        queue_items=getattr(self,"scan_items",[]) or []
        indexed={}
        order=[]
        for iid,_item in queue_items:
            rec=self.records.get(iid)
            if not rec:
                continue
            path=rec.get("path")
            if not path or rec.get("exported") or not path.is_file():
                continue
            case=self._sequence_case_number(rec)
            order.append((len(order),iid,case))
        for position,(_number,iid,_case) in enumerate(order):
            indexed[iid]=(order,position)
        return indexed
'''
neighbors=r'''
    def _sequence_neighbors(self, iid, sequence_index=None):
        """Return two distinct independent matching scan reads within 3 slots.

        Middle files use immediately before and immediately after. The first
        and last file may use the next two or previous two only if all THREE
        files independently read the same case (checked by consensus caller).
        No filename adjacency or gap check is used.
        """
        indexed=sequence_index if sequence_index is not None else self._sequence_index()
        located=indexed.get(iid)
        if located is None:
            return None
        group,index=located
        length=len(group)
        if length<3:
            return None
        candidate=str(self.records[iid].get("candidate") or "").strip()
        match_key=lambda s: tuple(int(x) for x in str(s).split("-",1)) if (
            s and re.fullmatch(r"\d{1,2}-\d{1,7}",str(s))) else None
        key=match_key(candidate)
        windows=[]
        if index>0 and index+1<length:
            windows.append((group[index-1],group[index+1]))
        if index>=2:
            windows.append((group[index-2],group[index-1]))
        if index+2<length:
            windows.append((group[index+1],group[index+2]))
        for a,b in windows:
            if key and match_key(a[2])==key and match_key(b[2])==key:
                return ((a[1],a[2]),(b[1],b[2]))
        # Keep the original before/after disagreement safeguard. A real
        # contradictory middle OCR read must go to manual review.
        if index>0 and index+1<length:
            a,b=group[index-1],group[index+1]
            if a[2] and b[2] and match_key(a[2])==match_key(b[2]):
                return ((a[1],a[2]),(b[1],b[2]))
        return None
'''
app=replace_method(app,"_sequence_index",index)
app=replace_method(app,"_sequence_neighbors",neighbors)

# Leading zeros are ignored when comparing three independently read cases.
# Preserve the original detected strings for audit and export normalization.
app=app.replace(
    '            if left[1] != right[1]:',
    '            if tuple(map(int,left[1].split("-",1))) != tuple(map(int,right[1].split("-",1))):',1)
app=app.replace(
    '            if candidate != consensus:',
    '            if tuple(map(int,candidate.split("-",1))) != tuple(map(int,consensus.split("-",1))):',1)
old='f"Sequence-confirmed {consensus} in 3x3 from consecutive originals "'
assert app.count(old)==1
app=app.replace(old,'f"Sequence-confirmed {consensus} from three consecutive scan reads "',1)
old='f"Unlabeled handwritten {normalized_candidate}: {score:.1f}% "\n                                "— waiting for BOTH matching sequence neighbors"'
assert app.count(old)==1
app=app.replace(old,'f"Unlabeled handwritten {normalized_candidate}: {score:.1f}% "\n                                "— awaiting 3 matching reads in scan order"',1)
assert app.count('self._diag_start_run()')==1
app=app.replace('        self._diag_start_run()\n',
                '        self._diag_start_run()\n'
                '        self._diag_event("sequence_order",mode="natural_filename_scan_queue",\n'
                '             ordered_files=[Path(rec["path"]).name for iid,rec in self.scan_items],\n'
                '             minimum_independent_matches=3,\n'
                '             filenames_need_consecutive_numbers=False)\n',1)

# Preserve existing Kurt send rules and safety guards, plus update failure
# diagnostics so a Windows-only updater failure is no longer silent.
assert app.count('            subprocess.Popen([\n                sys.executable, str(staged_helper),')==1
app=app.replace(
    '            subprocess.Popen([\n                sys.executable, str(staged_helper),',
    '            self._log("Launching external updater for %s from %s", archive_path, self.app_version)\n'
    '            subprocess.Popen([\n                sys.executable, str(staged_helper),',1)

# Make the external updater report errors to the Windows operator. Keep
# rollback, ZIP validation and restart behavior completely unchanged.
assert helper.count('import traceback')==0
helper=helper.replace('import tempfile\n','import tempfile\nimport traceback\n',1)
old_footer="""    except Exception as exc:
        (Path(tempfile.gettempdir()) / 'MF_CaseNumberOCR_Update_Error.txt').write_text(str(exc), encoding='utf-8')
        raise"""
assert helper.count(old_footer)==1
new_footer="""    except Exception as exc:
        log_path=Path(tempfile.gettempdir()) / 'MF_CaseNumberOCR_Update_Error.txt'
        try:
            log_path.write_text(traceback.format_exc(),encoding='utf-8')
        except OSError:
            pass
        if os.name == 'nt':
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    'MFCase GitHub update failed.\\n\\n' + str(exc) +
                    '\\n\\nDiagnostic: ' + str(log_path) +
                    '\\n\\nYour existing application and learning are preserved.',
                    'MFCase Update Error', 0x10)
            except Exception:
                pass
        raise"""
helper=helper.replace(old_footer,new_footer,1)

compile(app,"case_number_ocr.py","exec")
compile(helper,"updater_apply.py","exec")
assert 'self.app_version = "5.1.5"' in app
assert 'root.title("M.F. Case Number OCR — V5.1.5")' in app
assert 'dimension_px=int(round(3*72*2.2))' in app
assert 'observed_score < 95.0' in app
assert 'os.utime(destination, None)' in app
assert 'def _diag_export(' in app
assert 'tuple(map(int,left[1].split("-",1))) != tuple(map(int,right[1].split("-",1)))' in app
assert 'minimum_independent_matches=3' in app
assert 'filename run' not in ast.get_source_segment(
    app,next(x for x in ast.walk(ast.parse(app))
        if isinstance(x,ast.FunctionDef) and x.name=="_sequence_neighbors"))

temp=io.BytesIO()
with zipfile.ZipFile(temp,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",app)
    z.writestr("updater_apply.py",helper)
blob=temp.getvalue()
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    compile(z.read("case_number_ocr.py"),"app.py","exec")
    compile(z.read("updater_apply.py"),"updater.py","exec")
sha=hashlib.sha256(blob).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(blob).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(blob)
(ROOT/"SHA256.txt").write_text(sha+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.1.5\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("MFCase V5.1.5 sorted scan-read consensus and visible updater error reporting\n")
print("V5.1.5 PACKAGE PASS SHA256:",sha,"size:",len(blob))
