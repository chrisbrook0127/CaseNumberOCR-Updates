"""Synthetic release checks for MFCase v5.5.0.
Uses only fabricated case numbers and PDFs; no user data.
"""
import sys,tempfile,unittest,os
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from case_number_ocr import App,_case_identity
from PIL import Image

class T(unittest.TestCase):
 def app(self):
  a=App.__new__(App)
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
  a.folder=Path(t.name);a.records={};a.busy=False;a.export_busy=False;a.trocr_training=False
  a._sequence_review_pending=False
  a._diag_event=lambda *args,**kw:None
  a._insert_scan_row=lambda *args,**kw:None
  a._move_to_completed=lambda *args,**kw:None
  a._refresh_review_controls=lambda:None
  a._update_completed_count=lambda:None
  a._save_state=lambda:True
  a._rename_completed_in_place=lambda:None
  a.completed_tree=type('T',(),{'exists':lambda s,k:False,'delete':lambda s,k:None})()
  return a
 def add(self,a,i,case='',human=False,score=0):
  r=dict(path=a.folder/f'{i:04d}.pdf',original_name=f'{i:04d}.pdf',
    status='Auto verified' if case else 'OCR incomplete',
    candidate=case,confirmed=case,score=score,
    manual_approved=bool(case),auto_labeled=bool(case) and not human,
    sequence_inferred=False,sequence_excluded=False,sequence_conflict=False,
    scan_ambiguous=False,exported=False)
  a.records[f'i{i}']=r
  return r
 def test_100_pages_override_auto_100_percent(self):
  a=self.app()
  self.add(a,0,'08-0167',True)
  for i in range(1,101):self.add(a,i)
  wrong=self.add(a,45,'08-999',False,100)
  self.add(a,101,'08-0167',True)
  assert a._apply_sequence_consensus()>=100
  assert wrong['sequence_inferred'] and _case_identity(wrong['confirmed'])=='08-167'
  assert all(_case_identity(a.records[f'i{i}']['confirmed'])=='08-167' for i in range(1,101))
 def test_do_not_override_manual(self):
  a=self.app();self.add(a,0,'08-0167',True)
  x=self.add(a,5,'08-999',True)
  self.add(a,10,'08-0167',True);a._apply_sequence_consensus()
  assert x['confirmed']=='08-999' and not x['sequence_inferred']
 def test_incremental_before_after_while_busy(self):
  a=self.app();a.busy=True
  self.add(a,0,'08-0167',True)
  mid=self.add(a,1)
  assert a._incremental_sequence_check()==0
  self.add(a,2,'08-0167',True)
  assert a._incremental_sequence_check()==1
  assert _case_identity(mid['confirmed'])=='08-167'
 def test_crop_strict_08_prefix(self):
  a=self.app()
  class Fake:
   def __init__(self,value):self.value=value
   def readtext(self,*a,**k):return [([[0,0],[30,0],[30,20],[0,20]],self.value,.99)]
  a.reader=Fake('08-0167');a.trocr_model=None;a.trocr_error='unavailable'
  assert a._ocr_review_crop(Image.new('RGB',(100,40)))[0]=='08-0167'
  a.reader=Fake('234')
  assert a._ocr_review_crop(Image.new('RGB',(100,40)))[0]==''
 def test_locked_confidence_and_version(self):
  import inspect
  s=inspect.getsource(App)
  assert 'self.app_version = "5.5.0"' in s
  assert '95' in s
  assert 'def _open_settings_window(' in s
  assert 'def _refresh_network_indicator(' in s
if __name__=='__main__':
 unittest.main(argv=[sys.argv[0]],verbosity=2)
