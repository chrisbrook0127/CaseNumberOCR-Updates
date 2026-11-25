"""Only update historical regression assertions superseded by the restored v5.6.5 anchor precedence."""
import sys
from pathlib import Path
def change(path, replacements):
    content=path.read_text('utf8')
    for before,after in replacements:
        if before not in content:
            print('NOT IN OFFICIAL RELEASE TEST:',path.name,before[:85],flush=True)
            continue
        assert content.count(before)==1,(str(path),before[:100],content.count(before))
        content=content.replace(before,after,1)
    path.write_text(content,encoding='utf8')
def run(root):
    root=Path(root)
    change(root/'test_release_568.py',[
        ('def test_nearer_automatic_conflict_blocks_matching_distant_manual_pair(self):',
         'def test_matching_manual_boundaries_correct_intervening_automatic(self):'),
        ("        self.assertNotIn(3,plan)\n        self.assertIn(3,conflicts)\n        self.assertNotIn(1,plan)\n        self.assertIn(1,conflicts)",
         "        self.assertEqual(plan[1],('08-50',['i0','i4']))\n        self.assertEqual(plan[2],('08-50',['i0','i4']))\n        self.assertEqual(plan[3],('08-50',['i0','i4']))\n        self.assertFalse(conflicts)"),
        ('def test_nearest_automatic_pair_can_complete_without_manual_anchors(self):',
         'def test_matching_manual_pair_takes_priority_when_present(self):'),
        ("        self.assertEqual(plan[2],('08-57',['i1','i3']))",
         "        self.assertEqual(plan[2],('08-25',['i0','i4']))"),
        ('def test_confirmed_automatic_never_becomes_inference_target(self):',
         'def test_false_automatic_can_be_corrected_by_two_matching_manual(self):'),
        ("        self.assertNotIn(2,plan)\n        self.assertNotIn(2,conflicts)",
         "        self.assertEqual(plan[2],('08-57',['i0','i4']))\n        self.assertNotIn(2,conflicts)"),
        ('def test_incremental_range_uses_closest_auto_before_instead_of_farther_manual(self):',
         'def test_incremental_range_expands_to_matching_manual_boundaries(self):'),
        ('        a._apply_sequence_consensus.assert_called_once_with(affected_range=(2,7))',
         '        a._apply_sequence_consensus.assert_called_once_with(affected_range=(0,7))')
    ])
    change(root/'test_release_569.py',[
        ('def test_nearest_mixed_conflict_never_skips_to_farther_manual(self):',
         'def test_matching_manual_pair_overrides_intervening_automatic(self):'),
        ("        self.assertEqual(a._apply_sequence_consensus_impl(),0)\n        self.assertFalse(a.records['i1']['confirmed'])\n        self.assertFalse(a.records['i3']['confirmed'])",
         "        self.assertEqual(a._apply_sequence_consensus_impl(),3)\n        for iid in ('i1','i2','i3'):\n            self.assertEqual(m._case_identity(a.records[iid]['confirmed']),'08-56')\n            self.assertTrue(a.records[iid]['sequence_inferred'])")
    ])
if __name__=='__main__':run(sys.argv[1])
