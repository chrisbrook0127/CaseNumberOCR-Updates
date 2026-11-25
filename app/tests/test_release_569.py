"""Runtime mixed manual/automatic anchors, nearest-only, sent history regressions."""
import unittest
from pathlib import Path
from unittest.mock import Mock
import case_number_ocr as m
def row(number='',kind='review',index=0,**extra):
    r=dict(path=Path('/tmp')/f'Image{index:05d}.pdf',
        original_name=f'Image{index:05d}.pdf',
        confirmed=number,candidate=number,
        manual_approved=(kind=='manual'),auto_labeled=(kind=='auto'),
        sequence_inferred=(kind=='inferred'),sequence_excluded=False,
        case_draft='',sequence_conflict=False,scan_ambiguous=False,
        exported=False,status={'manual':'Manual verified',
            'auto':'Auto verified','review':'Needs review',
            'inferred':'Before/after inferred'}[kind])
    r.update(extra);return r
class Regression(unittest.TestCase):
    def setUp(self):self.a=m.App.__new__(m.App)
    def plan(self,rows):
        order=[(f'i{i}',row(*record,index=i)) for i,record in enumerate(rows)]
        return self.a._sequence_consensus_plan(order)
    def test_manual_before_auto_after(self):
        p,_=self.plan([('08-56','manual'),('','review'),('08-56','auto')])
        self.assertEqual(p[1],('08-56',['i0','i2']))
    def test_auto_before_manual_after(self):
        p,_=self.plan([('08-56','auto'),('','review'),('08-56','manual')])
        self.assertEqual(p[1],('08-56',['i0','i2']))
    def test_matching_manual_pair_corrects_conflicting_auto(self):
        p,c=self.plan([('08-56','manual'),('','review'),('08-57','auto'),
                       ('','review'),('08-56','manual')])
        self.assertEqual(p[1],('08-56',['i0','i4']))
        self.assertEqual(p[3],('08-56',['i0','i4']))
        self.assertNotIn(1,c);self.assertNotIn(3,c)
    def test_100_intermediate(self):
        p,_=self.plan([('08-56','manual')]+[('', 'review')]*100+
                      [('08-56','auto')])
        self.assertEqual(len(p),100)
    def test_older_machine_complete_without_manual_flag(self):
        rec=row('08-56','auto',status='98.7% OCR approved — ready to send')
        self.assertEqual(self.a._sequence_case_number(rec),'08-56')
    def test_ocr_suggestion_does_not_anchor(self):
        rec=row('08-56','auto',status='Auto-detected — review required')
        self.assertIsNone(self.a._sequence_case_number(rec))
    def test_manual_confirmation_overrides_old_ocr_conflict(self):
        rec=row('08-56','manual',scan_ambiguous=True,
                sequence_excluded=True,sequence_conflict=True)
        self.assertEqual(self.a._sequence_case_number(rec),'08-56')
    def test_auto_conflict_never_anchors(self):
        rec=row('08-56','auto',scan_ambiguous=True)
        self.assertIsNone(self.a._sequence_case_number(rec))
    def test_sent_auto_can_bound_unsent_manual(self):
        rec=row('08-56','auto',exported=True,
                manual_approved=False,status='Sent to COMPLETED (history)')
        order=[('history',rec),('review',row('','review',1)),
               ('manual',row('08-56','manual',2))]
        p,_=self.a._sequence_consensus_plan(order)
        self.assertEqual(p[1],('08-56',['history','manual']))
    def test_incremental_signature_includes_sent_history(self):
        a=self.a;a.folder=Path('/tmp')
        a.records={'i0':row('08-56','auto'), 'i1':row('','review',1),
                   'i2':row('08-56','manual',2)}
        a._apply_sequence_consensus=Mock(return_value=1)
        def order():
            result=list(a.records.items())
            if getattr(a,'add_history',False):
                result.append(('sent',row('08-56','auto',3,
                    exported=True,manual_approved=False,
                    status='Sent to COMPLETED (history)')))
            return result
        a._sequence_order_with_sent_history=order
        self.assertEqual(a._incremental_sequence_check('i2'),1)
        a._apply_sequence_consensus.reset_mock()
        self.assertEqual(a._incremental_sequence_check('i2'),0)
        a.add_history=True
        self.assertEqual(a._incremental_sequence_check('i2'),1)
        a._apply_sequence_consensus.assert_called_once()
if __name__=='__main__':unittest.main(verbosity=2)
