import sys,unittest,time,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import case_number_ocr as m
class Tree:
 def __init__(self):self.rows={};self.insertions=0
 def get_children(self,*a):return tuple(self.rows)
 def exists(self,i):return i in self.rows
 def insert(self,*a,iid,**kw):self.rows[iid]=kw;self.insertions+=1
 def delete(self,i):self.rows.pop(i,None)
 def item(self,i,**kw):self.rows[i].update(kw)
 def selection(self):return ()
 def selection_add(self,i):raise AssertionError('No per-row automatic selections')
class LargeFolder(unittest.TestCase):
 def app(self):
  a=m.App.__new__(m.App);a.folder=Path('/fake');a.tree=Tree();a.completed_tree=Tree()
  a.show_all_files_var=Mock();a.show_all_files_var.get.return_value=False
  a.year_var=Mock();a.completed_edit_widgets={};a.completed_edit_drafts={};a.completed_checked_iids=set();a.scan_checked_iids=set();a.completed_photo_refs={};a._table_sort_state={}
  a.status=Mock();a.records={};a.send_selected_btn=None;a._rescan_force_iids=set()
  for name in ['_load_sent_history','_refresh_scan_button','_refresh_learning_button_state','_refresh_review_controls','_update_completed_count','_update_scan_file_count','_save_state','_recheck_after_decision','_schedule_auto_export','_schedule_scan_preview_refresh']:
   setattr(a,name,Mock())
  return a
 def test_6000_folder_load_no_rows_previews_or_stat(self):
  a=self.app();pdfs=[a.folder/f'Image{i}.pdf' for i in range(6000)]
  start=time.perf_counter()
  with patch.object(Path,'is_file',side_effect=AssertionError('per-file stat')):
   a._apply_folder_load('08',{},pdfs,False)
  self.assertEqual(len(a.records),6000);self.assertEqual(a.tree.insertions,0);self.assertEqual(a.completed_tree.insertions,0)
  self.assertEqual(len(a._pending_scan_items()),6000)
  print('6000-record mocked-UI load seconds:',round(time.perf_counter()-start,3))
 def test_pending_queue_ignores_display_selection(self):
  a=self.app();a.records={'a':dict(path=a.folder/'a.pdf',status='Not scanned'),'b':dict(path=a.folder/'b.pdf',status='Scan error: interrupted'),'c':dict(path=a.folder/'c.pdf',status='REVIEW')}
  self.assertEqual({i for i,r in a._pending_scan_items()},{'a','b'})
  self.assertEqual([i for i,r in a._pending_scan_items({'b'})],['b'])
 def test_review_appears_and_approval_removes_row(self):
  a=self.app();r=dict(path=a.folder/'a.pdf',status='REVIEW',candidate='08-341');a.records={'a':r}
  a._insert_scan_row('a',r);self.assertTrue(a.tree.exists('a'))
  r.update(confirmed='08-341',manual_approved=True);a._insert_scan_row('a',r);self.assertFalse(a.tree.exists('a'));self.assertIn('a',a.records)
 def test_completed_stays_out_of_scan_queue(self):
  a=self.app();a.records={'a':dict(path=a.folder/'a.pdf',status='done',confirmed='08-341',manual_approved=True)}
  self.assertEqual(a._pending_scan_items(),[])
 def test_show_all_reveals_all_unsent_rows(self):
  a=self.app();a._apply_folder_load('08',{},[a.folder/f'{i}.pdf' for i in range(6000)],False)
  a.show_all_files_var.get.return_value=True;a._rebuild_scan_list();self.assertEqual(len(a.tree.rows),6000)
  a.show_all_files_var.get.return_value=False;a._rebuild_scan_list();self.assertEqual(len(a.tree.rows),0);self.assertEqual(len(a.records),6000)
 def test_release_preserves_detection_metadata(self):
  h=dict(number='08-341',rect=[1,2,3,4],box_verified=True)
  r=dict(snippet=(0,object(),'08-341',[1,2,3,4],[h]))
  m.App._release_page_pixels(r);self.assertIsNone(r['snippet']);self.assertEqual(m.App._valid_ocr_box_candidates(r),[h])
 def test_no_hidden_completed_thumbnail(self):
  a=self.app();a._make_completed_thumbnail=Mock(side_effect=AssertionError('thumbnail'))
  a._insert_completed_row('a',{});a._make_completed_thumbnail.assert_not_called()
 def test_export_eligibility_does_not_require_visible_row(self):
  a=self.app();a.busy=False;a._sequence_review_pending=False
  with tempfile.TemporaryDirectory() as d:
   a.folder=Path(d);p=a.folder/'a.pdf';p.write_bytes(b'test')
   r=dict(path=p,confirmed='08-341',manual_approved=True)
   self.assertTrue(a._eligible_for_kurt('a',r));self.assertFalse(a.tree.exists('a'))
if __name__=='__main__':unittest.main()
