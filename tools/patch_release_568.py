"""Apply strict nearest completed-anchor rule on verified MFCase v5.6.7 source."""
from pathlib import Path
import sys

p=Path(sys.argv[1]);s=p.read_text(encoding='utf-8')
assert 'self.app_version = "5.6.7"' in s, 'Unexpected installed source version'

def splice(start,end,text):
    global s
    assert s.count(start)==1 and s.count(end)==1, (start,end)
    i=s.index(start);j=s.index(end,i+len(start))
    s=s[:i]+text+s[j:]

splice('        humans=[j for j,(_iid,r) in enumerate(order)',
       '        if left>=right:',
'''        # The absolute nearest independently COMPLETED file wins,
        # regardless of whether approval was human or automatic.
        independent=[j for j,(_iid,rec) in enumerate(order)
                     if self._sequence_case_number(rec)]
        prev=bisect_left(independent,index)-1
        nxt=bisect_right(independent,index)
        previous=independent[prev] if prev>=0 else index
        following=independent[nxt] if nxt<len(independent) else index
        left=min(previous,index)
        right=max(following,index)
''')
splice('    def _sequence_consensus_plan(self, order, positions=None):',
       '    def _apply_sequence_consensus(self, affected_range=None):',
'''    def _sequence_consensus_plan(self, order, positions=None):
        """Use only closest independent COMPLETED anchors on the two sides.

        Manual and automatic anchors have equal priority; inferred cases
        do not qualify as anchors and may never override independent ones.
        """
        anchors=[self._sequence_case_number(r) for _,r in order]
        independent=[i for i,a in enumerate(anchors) if a]
        independent_set=set(independent)
        plan={};conflicts=set()
        for pos in (positions if positions is not None else range(len(order))):
            if pos in independent_set:
                continue
            iid,r=order[pos];status=str(r.get("status") or "")
            if (r.get("exported") or r.get("case_draft") or r.get("sequence_excluded") or
                status in ("Not scanned","Scanning","Moved to BLANKS") or status.startswith("Scan error:")):
                continue
            lo=bisect_left(independent,pos)-1
            hi=bisect_right(independent,pos)
            if lo<0 or hi>=len(independent):
                continue
            left,right=independent[lo],independent[hi]
            if anchors[left]!=anchors[right]:
                conflicts.add(pos)
                continue
            plan[pos]=(anchors[left],[order[left][0],order[right][0]])
        return plan,conflicts

''')
old='''            if iid not in self.records or r.get("exported") or (r.get("manual_approved") and not r.get("auto_labeled")
                                     and not r.get("sequence_inferred")):
                continue
'''
new='''            # Preserve independently completed manual AND automatic anchors.
            if iid not in self.records or r.get("exported") or self._sequence_case_number(r):
                continue
'''
assert s.count(old)==1, 'Independent-anchor protection did not match'
s=s.replace(old,new)
s=s.replace('        Matching manually confirmed boundaries can span hundreds of pages.',
            '        The nearest independent boundaries can span hundreds of pages.')
s=s.replace('self.app_version = "5.6.7"','self.app_version = "5.6.8"')
s=s.replace('root.title("M.F. Case Number OCR — V5.6.7")',
            'root.title("M.F. Case Number OCR — V5.6.8")')
assert 'self.app_version = "5.6.8"' in s
compile(s,str(p),'exec')
p.write_text(s,encoding='utf-8')
print('PASS v5.6.8 strict nearest-anchor source patch')
