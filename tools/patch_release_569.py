"""MFCase v5.6.9: allow manual and automatic approvals to anchor interchangeably."""
from pathlib import Path
import ast,sys
p=Path(sys.argv[1]); s=p.read_text('utf-8')
assert s.count('self.app_version = "5.6.8"')==1
def replace_method(name, body):
    global s
    tree=ast.parse(s)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='App')
    node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
    lines=s.splitlines(keepends=True)
    assert lines[node.lineno-1].startswith('    def '+name+'(')
    lines[node.lineno-1:node.end_lineno]=[line+'\n' for line in body.rstrip('\n').split('\n')]
    s=''.join(lines)
    ast.parse(s)
replace_method('_sequence_case_number', '''    def _sequence_case_number(self, record):
        """Independent manual and machine completions have equal anchor priority."""
        if (not record or record.get("sequence_inferred") or
                record.get("case_draft") or
                str(record.get("status") or "").startswith("Unverified")):
            return None
        status=str(record.get("status") or "").lower()
        human_verified=(bool(record.get("manual_approved")) and
                        not bool(record.get("auto_labeled")))
        # Older verified automatic records may lack manual_approved=True.
        # A merely suggested/unapproved OCR number is never an anchor.
        machine_verified=bool(record.get("auto_labeled")) and (
            bool(record.get("manual_approved")) or
            bool(record.get("exported")) or
            "auto verified" in status or
            "automatically approved" in status or
            "ocr approved" in status or
            status.startswith("completed"))
        if not (human_verified or machine_verified):
            return None
        # Human corrections override stale OCR ambiguity flags. Machine
        # results must have no unresolved OCR conflicts or exclusions.
        if not human_verified and (record.get("scan_ambiguous") or
                                   record.get("sequence_conflict") or
                                   record.get("sequence_excluded")):
            return None
        number=normalize_case_number(record.get("confirmed") or "")
        if not CASE_RE.fullmatch(number):
            return None
        return _case_identity(number)
''')
replace_method('_incremental_sequence_check', '''    def _incremental_sequence_check(self, changed_iid=None):
        """Recheck the nearest completed boundaries, including already-sent files."""
        if not self.folder:
            return 0
        order=self._sequence_order_with_sent_history()
        anchors=tuple((iid,self._sequence_case_number(rec))
                      for iid,rec in order if self._sequence_case_number(rec))
        if anchors==getattr(self,"_last_sequence_signature",None):
            return 0
        self._last_sequence_signature=anchors
        if changed_iid is None:
            return self._apply_sequence_consensus()
        index=next((j for j,(iid,_rec) in enumerate(order)
                    if iid==changed_iid),None)
        if index is None:
            return self._apply_sequence_consensus()
        # Revoke unsupported prior inferences if an anchor disappears.
        if not self._sequence_case_number(order[index][1]):
            return self._apply_sequence_consensus()
        independent=[j for j,(_iid,rec) in enumerate(order)
                     if self._sequence_case_number(rec)]
        before=bisect_left(independent,index)-1
        after=bisect_right(independent,index)
        previous=independent[before] if before>=0 else index
        following=independent[after] if after<len(independent) else index
        left=min(previous,index);right=max(following,index)
        if left>=right:return 0
        return self._apply_sequence_consensus(affected_range=(left,right+1))
''')
s=s.replace('self.app_version = "5.6.8"','self.app_version = "5.6.9"')
s=s.replace('Case Number OCR — V5.6.8','Case Number OCR — V5.6.9')
ast.parse(s);p.write_text(s,encoding='utf-8')
print('PASS patched v5.6.9 mixed manual/automatic anchor eligibility')
