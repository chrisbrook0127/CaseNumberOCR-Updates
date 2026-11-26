"""MFCase v6.0.3: 5,000-file UI/network optimization without OCR changes."""
import ast
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf
import case_number_ocr as m


class Performance603(unittest.TestCase):
    def test_review_reads_only_selected_network_paths(self):
        a=m.App.__new__(m.App)
        a.records={f'i{x}': {'path':Path(f'/tmp/nonexistent{i}'), 'status':'OCR review'}
                   for x,i in enumerate(range(5000))}
        a.review_btn=Mock()
        a.tree=Mock()
        a.tree.selection.return_value=('i13',)
        a.tree.exists.return_value=True
        a.folder=None
        a._training_blocks_work=Mock(return_value=False)
        with patch.object(m.Path,'is_file',return_value=True) as check:
            a._refresh_review_controls()
        self.assertEqual(check.call_count,1)
        a.review_btn.config.assert_called_once_with(state='normal')

    def test_review_selection_still_verifies_actual_file(self):
        a=m.App.__new__(m.App)
        a.records={'found': {'path':Path('found.pdf'),'status':'Read'},
                   'missing': {'path':Path('missing.pdf'),'status':'Read'},
                   'pending': {'path':Path('pending.pdf'),'status':'Not scanned'}}
        with patch.object(m.Path,'is_file',side_effect=[True,False]) as check:
            self.assertEqual(a._review_ready_ids(['found','missing','pending']), ['found'])
            self.assertEqual(check.call_count,2)

    def test_blank_checker_borrows_handle_without_closing(self):
        a=m.App.__new__(m.App)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'blank.pdf'
            doc=pymupdf.open();doc.new_page();doc.save(path);doc.close()
            handle=pymupdf.open(path)
            with patch.object(m.pymupdf,'open',side_effect=AssertionError('must reuse handle')):
                self.assertTrue(a._pdf_is_blank(path,doc=handle))
                self.assertFalse(handle.is_closed)
            handle.close()
            self.assertTrue(a._pdf_is_blank(path))

    def test_ocr_borrowed_handle_never_closed_and_same_read(self):
        a=m.App.__new__(m.App)
        a._perf_stages={}
        a._diag_event=Mock()
        a._perf_file_report=Mock()
        a._prepare_printed_region=Mock()
        a._automatic_handwritten_4x4=Mock(return_value=[])
        a._ranked_learned_scan_zones=Mock(return_value=[])
        a.trocr_model=None
        a._page_ocr_budget=None
        a.stop_event=threading.Event()
        a.pause_event=threading.Event();a.pause_event.set()
        a._get_auto_threshold=Mock(return_value=95.)
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'printed.pdf'
            doc=pymupdf.open();doc.new_page();doc.save(f);doc.close()
            original=pymupdf.open(f)
            with patch.object(m.pymupdf,'open',side_effect=AssertionError('open called again')):
                read=a._ocr_pdf(f,doc=original)
                self.assertEqual(read[0],'')
                self.assertFalse(original.is_closed)
            original.close()

    def test_queue_backpressure_limits_large_images(self):
        a=m.App.__new__(m.App)
        a.queue=queue.Queue()
        a.stop_event=threading.Event()
        a.pause_event=threading.Event();a.pause_event.set()
        for x in range(7):a.queue.put(('row',x))
        finished=[]
        worker=threading.Thread(target=lambda:(a._throttle_scan_result_queue(6),finished.append(1)))
        worker.start()
        time.sleep(.05)
        self.assertTrue(worker.is_alive(),'producer did not wait for the consumer')
        a.queue.get_nowait()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(finished,[1])

    def test_stop_unblocks_bounded_queue(self):
        a=m.App.__new__(m.App)
        a.queue=queue.Queue()
        a.stop_event=threading.Event()
        a.pause_event=threading.Event();a.pause_event.set()
        for _ in range(9):a.queue.put('row')
        thread=threading.Thread(target=a._throttle_scan_result_queue)
        thread.start()
        time.sleep(.05);a.stop_event.set();thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_preview_stamps_do_not_stat_every_repaint(self):
        a=m.App.__new__(m.App)
        a.app_dir=Path('/tmp')
        item={'path':Path('/tmp/example.pdf'),'candidate':'08-570'}
        with patch.dict(m.os.environ,{'LOCALAPPDATA':'/tmp'}),\
             patch.object(m.Path,'stat',return_value=Mock(st_mtime_ns=1)) as stat:
            first=a._scan_preview_cache_file(item)
            second=a._scan_preview_cache_file(item)
            self.assertEqual(first,second)
            self.assertEqual(stat.call_count,1)
        item['path']=Path('/tmp/other.pdf')
        with patch.dict(m.os.environ,{'LOCALAPPDATA':'/tmp'}),\
             patch.object(m.Path,'stat',return_value=Mock(st_mtime_ns=3)) as stat:
            self.assertNotEqual(first,a._scan_preview_cache_file(item))
            self.assertEqual(stat.call_count,1)

    def test_no_ocr_learning_and_location_function_changes(self):
        code=Path(m.__file__).read_text(encoding='utf8')
        self.assertIn('self.app_version = "6.0.3"',code)
        for name in ('_train_trocr_worker','_trocr_read_scored_impl','_scan_one_learned_zone',
                     '_automatic_handwritten_4x4','_ranked_learned_scan_zones',
                     '_sequence_consensus_plan','_order_review_findings'):
            self.assertTrue(hasattr(m.App,name),name)

    def test_large_sorted_table_does_not_resort_per_scan(self):
        a=m.App.__new__(m.App)
        a.records={str(i):{} for i in range(5000)}
        a._table_sort_state={'scan':('file',False)}
        a.busy=True
        a.folder=Path('/tmp')
        a.tree=Mock()
        a.tree.exists.return_value=True
        a.scan_checked_iids=set()
        a._active_scan_iid=None
        a._bulk_scan_rows=False
        a.show_all_files_var=Mock()
        a.show_all_files_var.get.return_value=True
        a._schedule_scan_preview_refresh=Mock()
        a._update_scan_file_count=Mock()
        a._apply_table_sort=Mock()
        a._eligible_for_kurt=Mock(return_value=False)
        a.send_selected_btn=Mock()
        rec={'path':Path('/tmp/file.pdf'),'candidate':'','status':'Not scanned','engine_scores':{}}
        a._insert_scan_row('i-new',rec)
        self.assertTrue(a._scan_sort_dirty)
        a._apply_table_sort.assert_not_called()
        a._eligible_for_kurt.assert_not_called()


if __name__=='__main__':unittest.main()
