"""Standalone synthetic regression tests for release archives without bundled tests."""
import inspect
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from case_number_ocr import App

class DummyTree:
    def exists(self, item): return False
    def delete(self, item): pass

class Regression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App.__new__(App)
        a=self.app
        a.busy=False
        a.export_busy=False
        a.trocr_training=False
        a.folder=Path(self.tmp.name)
        a.records={}
        a.completed_tree=DummyTree()
        a._move_to_completed=lambda iid, batch=False: None
        a._insert_scan_row=lambda iid,r: None
        a._refresh_review_controls=lambda:None
        a._update_completed_count=lambda:None
        a._save_state=lambda:True
        a._rename_completed_in_place=lambda:None
        a._diag_event=lambda *args,**kwargs:None

    def add(self, idx, confirmed='', manual=False, candidate=''):
        iid=f"i{idx}"
        self.app.records[iid]=dict(
            path=self.app.folder / f"scan{idx:04d}.pdf",
            original_name=f"scan{idx:04d}.pdf",
            confirmed=confirmed,
            candidate=candidate,
            manual_approved=manual,
            auto_labeled=bool(confirmed and not manual),
            exported=False,
            scan_ambiguous=False,
            sequence_inferred=False,
            sequence_conflict=False,
            sequence_excluded=False,
            status="Manual verified" if manual else ("Auto verified" if confirmed else "Needs review"))
        return self.app.records[iid]

    def test_many_intermediate_files(self):
        left=self.add(0,confirmed="08-0167",manual=True)
        middle=[self.add(i) for i in range(1,101)]
        right=self.add(101,confirmed="08-0167",manual=True)
        assert self.app._apply_sequence_consensus() == 100
        assert all(r["confirmed"]=="08-0167" and r["sequence_inferred"] for r in middle)
        assert left["manual_approved"] and right["manual_approved"]

    def test_conflicting_anchors_do_not_infer(self):
        self.add(0, confirmed="08-100", manual=True)
        mid=self.add(1,candidate="08-999")
        self.add(2,confirmed="08-200",manual=True)
        self.app._apply_sequence_consensus()
        assert mid["confirmed"]=="",mid

    def test_prefix_and_duplicate_boxes(self):
        hit={"number":"08-0167","rect":[10,10,200,40],"score":90}
        near={"number":"08-0167","rect":[12,11,198,41],"score":80}
        wrong={"number":"234","rect":[300,0,360,60],"score":99}
        result=self.app._dedupe_verified_red_boxes([wrong,near,hit],{"confirmed":"08-0167"})
        assert len(result)==1,result
        assert result[0]["number"]=="08-0167"

    def test_green_check_queues_only(self):
        r=self.add(1,confirmed="08-0167")
        events=[]
        self.app._save_completed_case_edit=lambda iid, value: events.append(("complete",value.get())) or True
        self.app._save_state=lambda:events.append(("save",None)) or True
        self.app._quick_approve("i1")
        assert ("complete","08-0167") in events,events
        assert not any(name=="send" for name,_ in events)
        source=inspect.getsource(App._quick_approve)
        assert "self.export(" not in source and "_send" not in source

if __name__=="__main__":
    unittest.main(argv=[sys.argv[0]],verbosity=2)
