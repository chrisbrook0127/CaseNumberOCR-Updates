"""
Case Number OCR & PDF Renamer - local Windows app
Case format: two-digit year + hyphen + 1-7 digits.
The program NEVER silently renames an uncertain case number.
"""
from __future__ import annotations
import os, re, shutil, threading, queue, json, logging, time, math, hashlib
import sys, subprocess, tempfile, urllib.request, urllib.error, zipfile
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import pymupdf
except ImportError:
    pymupdf = None
try:
    import easyocr
except ImportError:
    easyocr = None
try:
    import numpy as np
except ImportError:
    np = None

REQUIRED_CASE_YEAR = "08"
CASE_RE = re.compile(r"(?<!\d)(08-\d{1,7})(?!\d)")

def normalize_case_number(value):
    """Return the OCR reading without adding, padding, or inferring digits.

    Only whitespace around the hyphen and OCR dash glyphs are normalized;
    the digits and their lengths are preserved exactly as read.
    """
    text = str(value or "").strip().replace("—", "-").replace("–", "-")
    text = re.sub(r"\s* - \s*", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return text

LABEL_RE = re.compile(r"(?:Case\s+Number|INCIDENT\s+N[O0]\s*\.?)", re.IGNORECASE)
INCIDENT_LABEL_RE = re.compile(r"\bINCIDENT\s*(?:N[O0]|NUM(?:BER)?|#)\s*[.:#]?", re.IGNORECASE)
CASE_FIELD_LABEL_RE = re.compile(
    r"(?:CASE\s*(?:NO\.?|NUM(?:BER)?|#)|INCIDENT\s*(?:N[O0]\s*\.?|NUM(?:BER)?|#))",
    re.IGNORECASE)
INCIDENT_NUMBER_RE = re.compile(r"(?<!\d)(08\s*-\s*\d{1,7})(?!\d)")


def _select_primary_case_hit(hits, source, image_shape):
    """Choose a physical case-number location before scoring OCR variants.

    The true handwritten upper-right margin is right of fax page counters,
    such as '122 P.005/015'. A high-confidence fax interpretation cannot
    beat the real case-number box merely by having a greater OCR score.
    """
    if not hits:
        raise ValueError("Cannot select a candidate from an empty list")
    height,width=image_shape[:2]
    def engine_quality(hit):
        engines={str(v) for v in hit.get("engines") or []}
        raw=any(v=="EasyOCR" or
                (v.startswith("EasyOCR-") and "enhanced" not in v.lower())
                for v in engines)
        agree=any(v.startswith("TrOCR") for v in engines) and raw
        return (agree,raw,float(hit.get("score") or 0))
    if source in ("fallback","header"):
        margin=[]
        for h in hits:
            rect=h.get("rect")
            if not rect:
                continue
            x0,y0,x1,y1=map(float,rect)
            # Printed fax counters and body numbers are left of the
            # hand-marked case number at the very top-right edge.
            if ((x0+x1)/2>=width*0.82 and y0<=height*0.24
                    and 0<=y0<y1):
                margin.append(h)
        if margin:
            return max(margin,key=lambda h:(
                (float(h["rect"][0])+float(h["rect"][2]))/(2*width),
                engine_quality(h)))
    return max(hits,key=engine_quality)


def _original_pdf_sequence_key(path):
    """A sequential scan ID must be the FINAL numeric run in the original stem.

    Group only otherwise-identical filename templates; never infer across a
    missing filename, other roll, or different suffix/prefix.
    """
    name = Path(path).stem
    match = re.fullmatch(r"(.*?)(\d+)([^\d]*)", name)
    if not match:
        return None
    prefix, digits, suffix = match.groups()
    return (prefix.casefold(), suffix.casefold(), int(digits))


import hashlib



def _mfcase_header_field_candidates(hits,page_shape):
    """Candidate readings in the physical right-margin case-number location."""
    ph,pw=page_shape[:2]
    span=int(round(3*72*2.2))
    xmin=pw-min(span,pw)
    expected=re.compile(r"08-\d{1,7}\Z")
    candidates=[]
    for h in hits:
        rect=h.get("rect")
        value=str(h.get("number") or "")
        if not rect or not expected.fullmatch(value):
            continue
        try:
            x0,y0,x1,y1=map(float,rect)
        except (ValueError,TypeError):
            continue
        if (x0>=xmin-4 and x1<=pw+4 and y0>=0 and
                y1<=min(ph,span)+4 and y0<y1):
            candidates.append(h)
    if not candidates:
        return []
    margin=[h for h in candidates
            if (float(h["rect"][0])+float(h["rect"][2]))/2>=pw*.82
            and float(h["rect"][1])<=ph*.24]
    if not margin:
        return [] # Not a well-located header; do not authorize a fax page number.
    # Limit independent candidates to the physical right-margin word row.
    first=min(float(h["rect"][1]) for h in margin)
    return [h for h in margin if abs(float(h["rect"][1])-first)<=max(24.,span*.06)]


def _mfcase_export_case_name(value):
    """Remove zeros ONLY before case digits, retaining the original year."""
    original=normalize_case_number(value)
    match=re.fullmatch(r"(08)-(\d{1,7})", original)
    if not match:
        raise ValueError(f"Invalid case number for export: {value!r}")
    year,case_digits=match.groups()
    return f"{year}-{int(case_digits)}"


class App:
    def __init__(self, root):
        self.root = root
        root.title("M.F. Case Number OCR — V5.3.0")
        root.geometry("1180x780")
        root.minsize(1000, 650)
        self.folder = None
        self.reader = None
        self.trocr_processor = None
        self.trocr_model = None
        self.trocr_training = False
        self.queue = queue.Queue()
        self.busy = False
        self.paused = False
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()
        self.learning_pause_event = threading.Event()
        self.learning_pause_event.set()
        self.learning_stop_event = threading.Event()
        self.records = {}
        self.completed_photo_refs = {}
        self.completed_edit_widgets = {}
        self.completed_edit_drafts = {}
        self.completed_unverify_buttons = {}
        # Checkbox selection is independent of Treeview's review-row highlighting.
        self.completed_checked_iids = set()
        # Scan checkboxes do not change the Treeview review highlight.
        self.scan_checked_iids = set()
        # Never export before a running scan's sequence check has completed.
        self.export_busy = False
        self._auto_export_after = None
        self._export_reschedule = False
        self._export_in_flight_iids = set()
        self.scan_items = []
        self.learning_dir = None
        self.learning_data = {"examples": [], "corrections": {},
                              "trocr_v2_trained_count": 0,
                              "trocr_v2_attempted_count": 0,
                              "trocr_v2_validation": {}}
        self.data_lock = threading.RLock()
        self.trocr_training = False
        self.torch_device = "cpu"
        self.trocr_error = ""
        self.training_popup = None
        self.training_progressbar = None
        self.training_percent_label = None
        self.training_stage_label = None
        self.training_close_button = None
        self.training_pause_button = None
        self.training_stop_button = None
        self.auto_label_var = tk.BooleanVar(value=False)
        self.auto_threshold_var = tk.StringVar(value="99")
        self.log_path = None
        self.scan_year_prefix = REQUIRED_CASE_YEAR
        self.update_busy = False
        self.update_release = None
        self._startup_auto_update = False
        self._startup_dialog = None
        self._startup_phase_label = None
        self._startup_progressbar = None
        self._startup_learning_pending = False
        self._offline_prepare_busy = False
        self._auto_learning_started = False
        self._learning_connection_source = None
        self._learning_auto_run = False
        # All animation is driven by Tk's own event loop; file and model I/O
        # happens in workers so the loading indicator remains responsive.
        self._loading_jobs = set()
        self._loader_panel = None
        self._loader_arc = None
        self._loader_canvas = None
        self._loader_label = None
        self._loader_tick_id = None
        self._loader_angle = 0
        self._folder_load_generation = 0
        self._folder_load_timeout_id = None
        self.app_version = "5.3.0"
        self._diag_file=None
        self._diag_run_id=None
        self._diag_current_file=None
        self._diag_lock=threading.RLock()
        self._startup_ready = False
        self._startup_error = None
        self._learning_revision = None
        self._cached_model_dir = None
        self._run_outcomes = {}
        self.app_dir = Path(__file__).resolve().parent
        self.update_config_path = self.app_dir / "update_config.json"
        self._ui()
        # Start maximized so the complete queue and review controls have as much
        # usable screen space as possible. On Windows, "zoomed" is the normal
        # maximized-window state (taskbar and title bar remain accessible).
        self.root.after_idle(self._maximize_window)
        self.auto_label_var.trace_add("write", lambda *_: self._refresh_auto_eligibility_status())
        self.auto_threshold_var.trace_add("write", lambda *_: self._refresh_auto_eligibility_status())
        self.year_var.trace_add("write", lambda *_: self._refresh_auto_eligibility_status())
        self.auto_label_var.set(True)
        self.auto_threshold_var.set("90")
        self._refresh_auto_eligibility_status()
        root.after(150, self._poll)
        # Production is local-only. Network access is possible only after an
        # explicit click on UPDATE or PREPARE OFFLINE MODELS.
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY","1")
        os.environ.setdefault("DO_NOT_TRACK","1")
        if os.environ.get("MFCASE_SKIP_STARTUP_MAINTENANCE") == "1":
            self._startup_ready = True  # headless test harness
        else:
            root.after(250,self._begin_offline_startup)

    def _loader_start(self, job, description):
        """Show a centered, animated, quarter-screen-scale spinner (UI thread only)."""
        self._loading_jobs.add(job)
        if self._loader_panel is None:
            self.root.update_idletasks()
            # Diameter is one quarter of the shorter visible window dimension.
            # A minimum keeps the indicator readable on small displays.
            diameter = max(128, min(300, int(min(
                self.root.winfo_width(), self.root.winfo_height()) * 0.25)))
            panel = tk.Frame(self.root, bg="#f4f7fc", relief="solid",
                             borderwidth=2, highlightthickness=2,
                             highlightbackground="#1456a8")
            self._loader_panel = panel
            panel.place(relx=0.5, rely=0.5, anchor="center")
            canvas = tk.Canvas(panel, width=diameter + 36, height=diameter + 36,
                               bg="#f4f7fc", highlightthickness=0)
            canvas.pack(padx=18, pady=(16, 2))
            edge = 23
            far = diameter + 13
            canvas.create_oval(edge, edge, far, far, outline="#dce5f2", width=12)
            self._loader_arc = canvas.create_arc(
                edge, edge, far, far, start=0, extent=115,
                outline="#1456a8", width=14, style=tk.ARC)
            self._loader_canvas = canvas
            self._loader_label = tk.Label(
                panel, text=description, font=("Segoe UI", 12, "bold"),
                bg="#f4f7fc", fg="#12345a", wraplength=diameter + 46)
            self._loader_label.pack(padx=14, pady=(0, 18))
            self._loader_tick()
        else:
            self._loader_label.configure(text=description)
        self._loader_panel.lift()

    def _loader_tick(self):
        if self._loader_panel is None:
            return
        self._loader_angle = (self._loader_angle - 9) % 360
        self._loader_canvas.itemconfigure(self._loader_arc, start=self._loader_angle)
        self._loader_tick_id = self.root.after(45, self._loader_tick)

    def _loader_finish(self, job):
        self._loading_jobs.discard(job)
        if self._loading_jobs:
            if self._loader_label is not None:
                if "rolls" in self._loading_jobs:
                    self._loader_label.configure(text="Loading PDF rolls…")
                elif "learning" in self._loading_jobs:
                    self._loader_label.configure(text="Loading shared learning…")
            return
        if self._loader_tick_id is not None:
            self.root.after_cancel(self._loader_tick_id)
            self._loader_tick_id = None
        if self._loader_panel is not None:
            self._loader_panel.destroy()
        self._loader_panel = None
        self._loader_canvas = None
        self._loader_arc = None
        self._loader_label = None

    def _folder_load_worker(self, folder, generation):
        """Enumerate rolls and read folder state without blocking animation."""
        try:
            logging.basicConfig(
                filename=str(folder / ".case_ocr_log.txt"), level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
                encoding="utf-8", force=True)
            settings_path = folder / ".case_ocr_settings.json"
            year = "08"
            if settings_path.exists():
                try:
                    year = str(json.loads(settings_path.read_text(encoding="utf-8")).get("year", "")).strip() or "08"
                except Exception:
                    logging.exception("Could not read folder settings")
            year = REQUIRED_CASE_YEAR  # Historical settings cannot change the required 08 prefix.
            saved = {}
            state_error = False
            state_path = folder / ".case_ocr_state.json"
            if state_path.exists():
                try:
                    saved = json.loads(state_path.read_text(encoding="utf-8")).get("files", {})
                except Exception:
                    logging.exception("Could not read saved job state; rebuilding queue")
                    state_error = True
            pdfs=sorted(
                (p for p in folder.iterdir()
                 if p.is_file() and p.suffix.lower()==".pdf"),
                key=self._natural_pdf_sort_key)
            self.queue.put(("folder_load_result", generation, year, saved, pdfs, state_error))
        except Exception as exc:
            self.queue.put(("folder_load_error", generation, str(exc)))

    def _apply_folder_load(self, year, saved, pdfs, state_error):
        """Install the folder listing onto Tk controls, preserving saved state."""
        self.year_var.set(REQUIRED_CASE_YEAR)
        for tree in (self.tree, self.completed_tree):
            for item in tree.get_children():
                tree.delete(item)
        for editing_iid in list(self.completed_edit_widgets):
            self._close_completed_edit(editing_iid)
        self.completed_edit_drafts.clear()
        self.completed_checked_iids.clear()
        self.scan_checked_iids.clear()
        self.records = {}
        self.completed_photo_refs.clear()
        if state_error:
            messagebox.showwarning(
                "Saved queue could not be read",
                "The saved queue file could not be read. The PDFs are still intact; the queue will be rebuilt.")
        for pdf in pdfs:
            old=saved.get(pdf.name,{})
            if old.get("exported"):
                old={}  # A PDF physically in root is not already sent.
            sent=False
            item = {
                "path": pdf, "candidate": old.get("candidate", ""),
                "confirmed": old.get("confirmed", ""), "status": old.get("status", "Not scanned"),
                "snippet": None, "handwritten": bool(old.get("handwritten", False)),
                "score": float(old.get("score", 0.0) or 0.0),
                "engines": list(old.get("engines", [])),
                "engine_agreement": bool(old.get("engine_agreement", False)),
                "auto_labeled": bool(old.get("auto_labeled", False)),
                "manual_approved": bool(old.get("manual_approved", False)),
                "send_after_manual_approval": bool(old.get("send_after_manual_approval", False)),
                 "completed_iid": None, "exported": False,
                "original_name": old.get("original_name") or
                                 (pdf.name if not sent else old.get("source_name",pdf.name)),
                "original_path": old.get("original_path",""),
                "export_name": old.get("export_name", ""),
                "preview_meta": old.get("preview_meta", {}),
                "manual_crop_rect": tuple(old.get("manual_crop_rect", [])) if old.get("manual_crop_rect") else None,
                "manual_crop_origin": old.get("manual_crop_origin",""),
                "training_type": old.get("training_type", "unsure"),
                "scan_source": old.get("scan_source", ""),
                "scan_ambiguous": bool(old.get("scan_ambiguous", False)),
                "sequence_inferred": bool(old.get("sequence_inferred", False)),
                "sequence_conflict": bool(old.get("sequence_conflict", False)),
                "sequence_anchors": list(old.get("sequence_anchors", [])),
                "sequence_suggestion": old.get("sequence_suggestion", ""),
                "sequence_excluded": bool(old.get("sequence_excluded", False)),
                "scan_selected": bool(old.get("scan_selected", True)),
                "learning_pending": bool(old.get("learning_pending", False)),
                "export_error": str(old.get("export_error", ""))
            }
            if item["confirmed"] and not CASE_RE.fullmatch(
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
                item["status"]=f"Sent to COMPLETED: {pdf.name}"
                item["scan_selected"]=False
            iid = f"pdf::{pdf.name}"
            if iid in self.records:
                iid=f"pdf::COMPLETED::{pdf.name}"
            self.records[iid] = item
            if item["scan_selected"]:
                self.scan_checked_iids.add(iid)
            if item["confirmed"] and not item["exported"]:
                self._insert_completed_row(iid,item)
                item["completed_iid"]=iid
            else:
                item["completed_iid"]=None
            self._insert_scan_row(iid,item)
        self.status.config(
            text=f"Found {len(pdfs)} PDF(s) in this folder. Saved review/export progress restored where available.")
        pending_files = any(
            not item.get("exported") and not item.get("confirmed")
            and item.get("status") != "Moved to BLANKS" and Path(item["path"]).is_file()
            for item in self.records.values())
        self._refresh_scan_button()
        self._refresh_learning_button_state()
        self._refresh_review_controls()
        self._update_completed_count()
        self._update_scan_file_count()
        # Restored review queues also get the sequence consistency check; it
        # must not depend on scanning new PDFs in this application session.
        self._apply_sequence_consensus()
        self._save_state()
        self._schedule_auto_export()

    def _ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 2))
        brand = ttk.Label(header, text="M.F.", font=("Segoe UI", 18, "bold"),
                          foreground="#ffffff", background="#1456a8", padding=(10, 3))
        brand.pack(side="left", padx=(0, 10))
        title_stack = ttk.Frame(header)
        title_stack.pack(side="left", fill="x", expand=True)
        ttk.Label(title_stack, text="Case Number OCR & Renamer — V5.3.0",
                  font=("Segoe UI", 17, "bold")).pack(anchor="w")

        # Coworker-facing navigation and learning action.
        self.select_files_nav_btn = tk.Button(
            header, text="SCAN", command=lambda: self._show_main_view("select"),
            bg="#0d4388", fg="white", activebackground="#0d4388", activeforeground="white",
            relief="sunken", bd=2, padx=14, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.completed_nav_btn = tk.Button(
            header, text="REVIEW", command=lambda: self._show_main_view("completed"),
            bg="#1456a8", fg="white", activebackground="#0d4388", activeforeground="white",
            relief="raised", bd=2, padx=14, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.update_btn = ttk.Button(header, text="UPDATE", command=self.check_for_updates)
        self.finish_batch_btn = ttk.Button(
            header, text="UPDATE LEARNING", command=self._finish_import_batch, state="disabled")
        self.update_btn.pack(side="right", padx=(8, 0), ipadx=12, ipady=6)
        self.finish_batch_btn.pack(side="right", padx=(8, 0), ipadx=10, ipady=6)
        self.prepare_offline_btn=ttk.Button(
            header,text="PREPARE OFFLINE MODELS",
            command=self._prepare_offline_models)
        self.prepare_offline_btn.pack(side="right",padx=(8,0),ipadx=8,ipady=6)
        self.settings_nav_btn=tk.Button(
            header,text="SETTINGS",command=lambda:self._show_main_view("settings"),
            bg="#1456a8",fg="white",activebackground="#0d4388",
            activeforeground="white",relief="raised",bd=2,padx=14,pady=8,
            font=("Segoe UI",10,"bold"),cursor="hand2")
        self.settings_nav_btn.pack(side="right",padx=(6,0))
        self.select_files_nav_btn.pack(side="right", padx=(6, 0))
        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(8, 7))

        self.view_container = ttk.Frame(main)
        self.view_container.pack(fill="both", expand=True)
        self.review_tab = ttk.Frame(self.view_container, padding=8)
        self.completed_tab = ttk.Frame(self.view_container, padding=8)
        self.settings_tab = ttk.Frame(self.view_container, padding=14)
        for view in (self.review_tab, self.completed_tab, self.settings_tab):
            view.place(relx=0, rely=0, relwidth=1, relheight=1)

        # The selected year stays internal at the established default of 08.
        # Coworkers only choose the folder and run/review the files.
        self.year_var = tk.StringVar(value="08")
        self.auto_label_var.set(True)
        self.auto_threshold_var.set("90")

        folder_bar = ttk.Frame(self.review_tab)
        folder_bar.pack(fill="x", pady=(0, 8))
        self.choose_folder_btn = ttk.Button(
            folder_bar, text="SELECT FILES", command=self.choose_folder)
        self.choose_folder_btn.pack(side="left", padx=(0, 10))
        self.folder_label = ttk.Label(folder_bar, text="No folder selected")
        self.folder_label.pack(side="left", fill="x", expand=True)

        action_bar = ttk.Frame(self.review_tab)
        action_bar.pack(fill="x", pady=(0, 7))
        self.scan_btn = ttk.Button(action_bar, text="SCAN", command=self.scan, state="disabled")
        self.scan_btn.pack(side="left", padx=(0, 5), ipadx=8, ipady=3)
        self.pause_btn = ttk.Button(
            action_bar, text="PAUSE", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=5, ipadx=8, ipady=3)
        self.restart_btn = ttk.Button(
            action_bar, text="STOP", command=self.stop_or_restart_scan, state="disabled")
        self.restart_btn.pack(side="left", padx=5, ipadx=8, ipady=3)
        self.review_btn = ttk.Button(
            action_bar, text="REVIEW SELECTED", command=self.review_selected, state="disabled")
        self.review_btn.pack(side="left",padx=5,ipadx=8,ipady=3)
        self.review_all_btn = self.review_btn
        self.rescan_btn = ttk.Button(
            action_bar, text="RESCAN SELECTED", command=self.rescan_selected, state="disabled")
        self.rescan_btn.pack(side="left", padx=5, ipadx=5, ipady=3)
        self.refresh_root_btn = ttk.Button(
            action_bar, text="REFRESH", command=self.refresh_root_folder)
        self.refresh_root_btn.pack(side="left", padx=5, ipadx=8, ipady=3)
        self.clear_root_btn = ttk.Button(
            action_bar, text="CLEAR", command=self.clear_scan_screen)
        self.clear_root_btn.pack(side="left", padx=5, ipadx=8, ipady=3)
        self.work_log_btn=ttk.Button(action_bar,text="EXPORT WORK LOG",command=self._diag_export)
        self.work_log_btn.pack(side="left",padx=5,ipadx=5,ipady=3)
        self.send_selected_btn=ttk.Button(
            action_bar,text="SEND ALL SELECTED",command=self._send_all_selected,
            state="disabled")
        self.send_selected_btn.pack(side="left",padx=5,ipadx=8,ipady=3)
        self.scan_file_count_label = ttk.Label(action_bar, text="0 of 0 selected")
        self.scan_file_count_label.pack(side="right", padx=(8, 0))

        scan_progress_row=ttk.Frame(self.review_tab)
        scan_progress_row.pack(fill="x",pady=(0,8))
        self.scan_progress_label=ttk.Label(
            scan_progress_row,text="SCAN READY — 0 of 0 PDF files",
            width=38,anchor="w")
        self.scan_progress_label.pack(side="left",padx=(0,10))
        self.progress=ttk.Progressbar(scan_progress_row,mode="determinate",
                                      maximum=1,value=0)
        self.progress.pack(side="left",fill="x",expand=True)
        self.scan_progress_pct=ttk.Label(scan_progress_row,text="0%",width=6,anchor="e")
        self.scan_progress_pct.pack(side="right",padx=(10,0))
        pane = ttk.Frame(self.review_tab)
        pane.pack(fill="both", expand=True)
        self._scan_preview_photos={}
        self._scan_preview_widgets={}
        self._scan_preview_keys={}
        self._scan_preview_refresh_id=None
        ttk.Style().configure("MFCase.Scan.Treeview",rowheight=74)
        cols = ("scan_select", "file", "preview", "candidate", "score", "workflow", "status")
        self.tree = ttk.Treeview(pane, columns=cols, show="headings",
                                 style="MFCase.Scan.Treeview",selectmode="extended")
        self.tree.heading("scan_select", text="☑ ALL", command=self._toggle_scan_select_all)
        self.tree.column("scan_select", width=76, minwidth=65, stretch=False, anchor="center")
        self.tree.heading("file", text="PDF FILE")
        self.tree.column("file",width=350,minwidth=175,stretch=True)
        self.tree.heading("preview",text="CASE NUMBER THUMBNAIL")
        self.tree.column("preview",width=230,minwidth=210,stretch=False,anchor="center")
        self.tree.heading("candidate", text="CASE NUMBER")
        self.tree.column("candidate", width=135, minwidth=110, stretch=False, anchor="center")
        self.tree.heading("score", text="Score")
        self.tree.column("score", width=75, minwidth=65, stretch=False, anchor="center")
        self.tree.heading("workflow",text="STATUS")
        self.tree.column("workflow",width=114,minwidth=98,stretch=False,anchor="center")
        self.tree.heading("status", text="OCR DETAILS")
        self.tree.column("status", width=320, minwidth=140, stretch=True)
        # Make OCR-generated case-number suggestions visible in the main queue.
        self.tree["displaycolumns"] = ("scan_select", "file", "preview", "candidate", "score", "workflow", "status")
        sy = ttk.Scrollbar(pane, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=lambda first,last:(
             sy.set(first,last),self._schedule_scan_preview_refresh()))
        self.tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_scan_checkbox_click)
        self.tree.bind("<Double-1>", self._unified_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._sync_scan_selection)
        self.tree.bind("<Configure>",lambda e:self._schedule_scan_preview_refresh(),add="+")
        self.tree.bind("<KeyRelease>",lambda e:self._schedule_scan_preview_refresh(),add="+")
        # Ctrl+A works from the list or its surrounding controls in SELECT FILES.
        self.root.bind("<Control-a>", self._select_all_for_rescan, add="+")

        # The scan progressbar is always visible above the list.
        self.status = ttk.Label(self.review_tab, text="Choose a folder, then click SCAN.")
        self.status.pack(anchor="w")

        # Internal status labels remain available to the existing diagnostics,
        # but are not placed on the coworker screen.
        self.learning_stats_label = ttk.Label(self.review_tab, text="")
        self.model_status_label = ttk.Label(self.review_tab, text="")
        self.learning_type_stats_label = ttk.Label(self.review_tab, text="")
        self.auto_eligibility_label = ttk.Label(self.review_tab, text="")

        completed_header = ttk.Frame(self.completed_tab)
        completed_header.pack(fill="x", pady=(0, 5))
        ttk.Label(completed_header, text="Ready for Review / Export",
                  font=("Segoe UI", 12, "bold")).pack(side="left")
        self.export_btn = ttk.Button(
            completed_header, text="SEND ALL SELECTED", command=self.export, state="disabled")
        self.export_btn.pack(side="right", padx=(8, 0))
        self.completed_refresh_btn = ttk.Button(
            completed_header, text="REFRESH", command=self.refresh_completed_list)
        self.completed_refresh_btn.pack(side="right", padx=(8, 0))
        ttk.Label(
            self.completed_tab,
            text="Enter a corrected case number in CHANGE and click Save, then SEND TO KURT. "
                 "Manually approved files remain here for correction and sending. "
                 "Automatic matches send after scan and sequence validation. "
                 "Use checkboxes to send specific files; without a selection, SEND includes all approved files.",
            wraplength=1100).pack(anchor="w", pady=(0, 5))
        cpane = ttk.Frame(self.completed_tab)
        cpane.pack(fill="both", expand=True)
        ccols = ("case", "edit_case", "file", "original_path", "status", "unverify", "select")
        style = ttk.Style(self.root)
        try:
            one_inch = max(72, int(self.root.winfo_fpixels("1i")))
            extra_half_inch = int(self.root.winfo_fpixels("0.5i"))
        except Exception:
            one_inch = 80
            extra_half_inch = 48
        style.configure("Completed.Treeview", rowheight=one_inch)
        self.completed_tree = ttk.Treeview(
            cpane, columns=ccols, show=("tree", "headings"),
            selectmode="extended", style="Completed.Treeview")
        self.completed_tree.heading("#0", text="IMAGE")
        image_width = 210 + extra_half_inch
        self.completed_tree.column("#0", width=image_width, minwidth=image_width,
                                   stretch=False, anchor="center")
        for col, title, width, min_width in [
            ("case", "CASE #", 130, 105),
            ("edit_case", "CHANGE", 210, 185),
            ("file", "ORIGINAL NAME", 240, 160),
            ("original_path", "ORIGINAL PATH", 220, 120),
            ("status", "QUEUE STATUS", 185, 140),
            ("unverify", "UNVERIFY", 94, 82),
            ("select", "☐ SELECT ALL", 112, 96)]:
            self.completed_tree.heading(col, text=title)
            self.completed_tree.column(col, width=width, minwidth=min_width,
                                       anchor="center", stretch=col in ("file", "original_path", "status"))
        self.completed_tree.heading("select", command=self._toggle_completed_select_all)
        self.completed_tree.bind("<Button-1>", self._on_completed_click)
        self.completed_tree.bind("<Double-1>", self.on_completed_double_click)
        self.completed_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_completed_count())
        self.completed_tree.bind("<MouseWheel>", self._completed_mousewheel)
        self.completed_tree.bind("<Button-4>", lambda e: self._scroll_completed(-3))
        self.completed_tree.bind("<Button-5>", lambda e: self._scroll_completed(3))
        self.completed_tree.bind("<Configure>", lambda _e: self._schedule_completed_layout())
        csy = ttk.Scrollbar(cpane, orient="vertical", command=self._completed_scrollbar_command)
        self.completed_scrollbar = csy
        self.completed_tree.configure(yscrollcommand=self._completed_scrollbar_update)
        self.completed_tree.pack(side="left", fill="both", expand=True)
        csy.pack(side="right", fill="y")
        # Keep the rightmost selection column accessible on narrower monitors.
        self.completed_xscrollbar = ttk.Scrollbar(
            self.completed_tab, orient="horizontal", command=self._completed_xscroll_command)
        self.completed_tree.configure(xscrollcommand=self._completed_xscrollbar_update)
        self.completed_xscrollbar.pack(fill="x")
        cbar = ttk.Frame(self.completed_tab)
        cbar.pack(fill="x", pady=(10, 0))
        self.completed_count = ttk.Label(cbar, text="0 files ready for export")
        self.completed_count.pack(side="left")
        self.review_completed_btn = ttk.Button(
            cbar, text="Review / Approve Selected", command=self.review_completed_selected, state="disabled")
        self.review_completed_btn.pack(side="right")
        self.unverify_selected_btn = ttk.Button(
            cbar, text="UNVERIFY SELECTED", command=self._unverify_completed_selected,
            state="disabled")
        self.unverify_selected_btn.pack(side="right", padx=(0, 8))
        # Transfer outcomes must be visible while the user is on COMPLETED;
        # the generic status label lives on a different tab.
        self.completed_send_status = ttk.Label(
            self.completed_tab, text="Approved files are ready for SEND TO KURT.",
            wraplength=1100)
        self.completed_send_status.pack(anchor="w", pady=(5, 0))
        ttk.Label(self.settings_tab,text="SETTINGS",
                  font=("Segoe UI",16,"bold")).pack(anchor="w",pady=(0,14))
        ttk.Label(self.settings_tab,text="Application version: V5.3.0",
                  font=("Segoe UI",11,"bold")).pack(anchor="w",pady=4)
        ttk.Label(self.settings_tab,
                  text="PDF recognition runs locally after models are installed. Shared learning uses the restricted network drive. GitHub is used for application updates, not case records.",
                  wraplength=900,justify="left").pack(anchor="w",pady=9)
        self.connection_status_label=ttk.Label(
            self.settings_tab,text="Connection status not checked",
            wraplength=1000,justify="left")
        self.connection_status_label.pack(anchor="w",pady=(5,14))
        ttk.Button(self.settings_tab,text="CHECK OFFLINE / ONLINE STATUS",
                   command=self._check_connection_status).pack(anchor="w",pady=5)
        ttk.Button(self.settings_tab,text="INSTRUCTIONS / HOW TO USE",
                   command=self._open_instructions).pack(anchor="w",pady=5)
        ttk.Button(self.settings_tab,text="EXPORT WORK LOG",
                   command=self._diag_export).pack(anchor="w",pady=5)
        ttk.Label(self.settings_tab,
                  text="Full OCR logs remain on the authorized workstation and restricted shared learning drive. They are never automatically uploaded to the public GitHub repository.",
                  wraplength=900,justify="left").pack(anchor="w",pady=10)
        self._show_main_view("select")

    def _show_main_view(self, view_name):
        """Only SCAN and SETTINGS are public UI surfaces."""
        if view_name=="settings":
            self._active_main_view="settings"
            self.settings_tab.lift()
            active,inactive=self.settings_nav_btn,self.select_files_nav_btn
            self._check_connection_status()
        else:
            self._active_main_view="select"
            self.review_tab.lift()
            active,inactive=self.select_files_nav_btn,self.settings_nav_btn
        active.configure(bg="#0d4388",relief="sunken")
        inactive.configure(bg="#1456a8",relief="raised")

    def _startup_notice(self, text):
        label = getattr(self, "_startup_phase_label", None)
        if label is not None:
            try:
                if label.winfo_exists():
                    label.configure(text=text)
            except tk.TclError:
                pass

    def _close_startup_notice(self):
        dialog = self._startup_dialog
        self._startup_dialog = None
        self._startup_phase_label = None
        bar = self._startup_progressbar
        self._startup_progressbar = None
        if bar is not None:
            try:
                bar.stop()
            except tk.TclError:
                pass
        if dialog is not None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass


    def _begin_offline_startup(self):
        """Initialize from user-local disks. No HTTP or SMB is accessed."""
        if self._startup_ready or self._loading_jobs:
            return
        try:
            self._startup_learning_pending=False
            directory=Path(os.environ.get("LOCALAPPDATA") or
                           str(Path.home()/".local"/"share"))/"MFCase"/"learning"
            directory.mkdir(parents=True,exist_ok=True)
            self.learning_dir=directory
            self._load_learning_data(refresh_ui=True)
            self._diag_event("offline_startup",
                model_downloads_allowed=False,software_updates_automatic=False,
                learning_updates_automatic=False,diagnostic_sync=False)
            self.status.config(text="Offline startup: loading cached OCR models. "
                 "No internet or shared drive connection is required.")
            self._begin_model_preload()
        except Exception as exc:
            self._fail_startup(str(exc))

    def _begin_startup_updates(self):
        """Legacy entry point is offline-only; no background update checks."""
        self._begin_offline_startup()

    def _retry_startup(self):
        if self._startup_ready or self.update_busy or self._loading_jobs:
            return
        self._startup_error = None
        self._startup_learning_pending = False
        self._offline_prepare_busy = False
        self._auto_learning_started = False
        self.learning_dir = None
        self.reader = None
        self.trocr_processor = None
        self.trocr_model = None
        self.trocr_error = ""
        self._startup_retry_btn.config(state="disabled")
        self._startup_learning_check()

    def _startup_learning_check(self):
        """Load shared learning on launch; train pending changes only while idle.

        Training and scanning are deliberately serialized: both use the OCR
        model/checkpoints, and concurrent runs can corrupt validation or exhaust
        RAM. The automatic training thread is limited to two CPU workers.
        """
        if self._learning_connection_source == "startup":
            return
        self._startup_notice("Connecting to shared learning; the initial load may take a while…")
        self._learning_connection_source = "startup"
        self._loader_start("learning", "Loading shared learning…")
        learning = Path(r"\\172.16.1.231\MicroficheNew\2008") / ".case_ocr_learning"
        threading.Thread(target=self._learning_connect_worker,
                         args=(learning, None), name="MFCase-StartupLearning", daemon=True).start()

    def _startup_software_complete(self,error=None):
        self._startup_auto_update=False
        self._loader_finish("startup")
        self._begin_offline_startup()

    def _start_automatic_learning_if_ready(self):
        """Never train or synchronize automatically at application startup."""
        self._startup_learning_pending=False
        return False

    def _read_update_config(self):
        try:
            cfg = json.loads(self.update_config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Update configuration could not be read: {exc}")
        repo = str(cfg.get("repository", "")).strip()
        asset_name = str(cfg.get("asset_name", "CaseNumberOCR_Update.zip")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RuntimeError(
                "GitHub updater is not configured yet. Create the dedicated CaseNumberOCR-Updates repository, "
                "then set 'repository' in update_config.json to OWNER/REPOSITORY.")
        return repo, asset_name

    @staticmethod
    def _version_tuple(value):
        parts = re.findall(r"\d+", str(value))
        return tuple(int(part) for part in parts[:4]) if parts else (0,)

    def check_for_updates(self, automatic=False):
        if self.update_busy:
            return
        self._startup_auto_update = bool(automatic)
        try:
            repo, asset_name = self._read_update_config()
        except Exception as exc:
            if automatic:
                self._startup_software_complete(str(exc))
            else:
                messagebox.showinfo("Updater setup", str(exc), parent=self.root)
            return
        self.update_busy = True
        self.update_btn.config(state="disabled", text="CHECKING…")
        threading.Thread(
            target=self._update_check_worker,
            args=(repo, asset_name), daemon=True).start()

    def _update_check_worker(self, repo, asset_name):
        try:
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            request = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "MF-CaseNumberOCR-Updater/5.0.10"})
            with urllib.request.urlopen(request, timeout=25) as response:
                release = json.loads(response.read().decode("utf-8"))
            tag = str(release.get("tag_name") or release.get("name") or "")
            assets = release.get("assets") or []
            asset = next((item for item in assets if item.get("name") == asset_name), None)
            if self._version_tuple(tag) <= self._version_tuple(self.app_version):
                self.queue.put(("update_check_result", tag, None, None))
                return
            if not asset:
                raise RuntimeError(
                    f"GitHub has release {tag}, but it does not contain the required update asset '{asset_name}'. "
                    "Attach the update ZIP to the published release and try again.")
            payload = {
                "version": tag,
                "download_url": asset.get("browser_download_url"),
                "digest": asset.get("digest"),
                "asset_name": asset_name,
                "html_url": release.get("html_url", "")}
            if not payload["download_url"]:
                raise RuntimeError("The GitHub release asset has no download URL.")
            self.queue.put(("update_check_result", tag, payload, None))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                detail = "GitHub repository or latest release was not found. Create the dedicated repository and publish a release first."
            else:
                detail = f"GitHub returned HTTP {exc.code}: {exc.reason}"
            self.queue.put(("update_check_result", "", None, detail))
        except Exception as exc:
            self.queue.put(("update_check_result", "", None, str(exc)))

    def _download_update(self, release):
        if self.update_busy:
            return
        self.update_busy = True
        self.update_btn.config(state="disabled", text="DOWNLOADING…")
        threading.Thread(target=self._update_download_worker, args=(release,), daemon=True).start()

    def _update_download_worker(self, release):
        try:
            temp_path = Path(tempfile.gettempdir()) / f"MF_CaseNumberOCR_Update_{os.getpid()}.zip"
            request = urllib.request.Request(
                release["download_url"],
                headers={"User-Agent": "MF-CaseNumberOCR-Updater/5.0.10"})
            digest = hashlib.sha256()
            with urllib.request.urlopen(request, timeout=90) as response, temp_path.open("wb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
            actual_digest = digest.hexdigest()
            expected = str(release.get("digest") or "")
            if expected.startswith("sha256:") and actual_digest.lower() != expected.split(":", 1)[1].lower():
                raise RuntimeError("The downloaded update failed GitHub's SHA-256 digest check. No files were changed.")
            with zipfile.ZipFile(temp_path, "r") as archive:
                bad = archive.testzip()
                if bad:
                    raise RuntimeError(f"The update ZIP failed its integrity check at {bad}.")
                allowed = {"case_number_ocr.py", "updater_apply.py", "RUN_CASE_OCR.bat", "MF.ico", "Launch_Case_OCR.vbs", "README.txt", "update_config.json", "CHANGELOG_V5_0_2.txt", "CHANGELOG_V5_0_3.txt", "CHANGELOG_V5_0_4.txt"}
                found = {Path(name).name for name in archive.namelist() if not name.endswith("/")}
                if not {"case_number_ocr.py", "updater_apply.py"}.issubset(found):
                    raise RuntimeError("The update ZIP does not contain the expected application files.")
                for name in archive.namelist():
                    if name.endswith("/"):
                        continue
                    path = Path(name)
                    if (len(path.parts) != 1 or path.name != name or
                            (path.name not in allowed and not re.fullmatch(
                                r"CHANGELOG_V[0-9]+(?:_[0-9]+){1,3}\.txt",path.name))):
                        raise RuntimeError(f"The update ZIP contains an unexpected path: {name}")
            self.queue.put(("update_download_ready", str(temp_path), release.get("version", "new version")))
        except Exception as exc:
            self.queue.put(("update_error", str(exc)))

    def _launch_update_installer(self, archive_path):
        helper = self.app_dir / "updater_apply.py"
        if not helper.is_file():
            if self._startup_auto_update:
                self._startup_software_complete("updater_apply.py is missing")
            else:
                messagebox.showerror("Updater unavailable", "updater_apply.py is missing from the application folder.")
            return
        try:
            staged_helper = Path(tempfile.gettempdir()) / f"mf_case_ocr_apply_{os.getpid()}.py"
            shutil.copy2(helper, staged_helper)
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            self._log("Launching external updater for %s from %s", archive_path, self.app_version)
            subprocess.Popen([
                sys.executable, str(staged_helper),
                "--pid", str(os.getpid()),
                "--app-dir", str(self.app_dir),
                "--zip", str(archive_path)],
                cwd=str(self.app_dir), creationflags=flags,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.root.destroy()
        except Exception as exc:
            if self._startup_auto_update:
                self._startup_software_complete(str(exc))
            else:
                messagebox.showerror("Could not start updater", str(exc), parent=self.root)

    @staticmethod
    def _natural_pdf_sort_key(path):
        """Sort filenames so Report 2 comes before Report 10 for sequence review."""
        return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                     for part in re.split(r"(\d+)", Path(path).name))

    def _maximize_window(self):
        """Open the application in the operating system's maximized state."""
        try:
            self.root.state("zoomed")
        except tk.TclError:
            # Fallback for Tk builds/platforms that do not expose the Windows
            # zoomed state. Keep the regular window decorations enabled.
            try:
                width = self.root.winfo_screenwidth()
                height = self.root.winfo_screenheight()
                self.root.geometry(f"{width}x{height}+0+0")
            except tk.TclError:
                pass

    def _update_scan_file_count(self):
        label = getattr(self, "scan_file_count_label", None)
        tree = getattr(self, "tree", None)
        if label is None or tree is None:
            return
        try:
            visible = set(tree.get_children(""))
            checked = len(visible & self.scan_checked_iids)
            label.config(text=f"{checked:,} of {len(visible):,} selected")
            self._refresh_scan_checkbox_heading()
        except (tk.TclError, AttributeError):
            pass


    @staticmethod
    def _workflow_status(record):
        if record.get("exported"):
            return "SENT"
        if record.get("confirmed") and record.get("manual_approved"):
            return "COMPLETED"
        if record.get("status")=="Not scanned":
            return "NOT SCANNED"
        return "REVIEW"


    def _scan_preview_cache_file(self, record):
        import hashlib as _hashlib
        base=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"preview_cache"
        name=str(record.get("path") or "")
        try:
            stamp=Path(name).stat().st_mtime_ns
        except OSError:
            stamp=0
        key=(name,stamp,str(record.get("candidate") or ""),str(record.get("confirmed") or ""))
        token=_hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:25]
        return base/(token+".png")

    def _make_scan_preview(self,iid,record):
        from PIL import Image,ImageDraw,ImageFont,ImageTk
        cache_path=self._scan_preview_cache_file(record)
        snippet=record.get("snippet")
        existing=self._scan_preview_keys.get(iid)
        key=(str(cache_path),id(snippet))
        if existing==key and iid in self._scan_preview_photos:
            return self._scan_preview_photos[iid]
        tile=None
        if snippet and len(snippet)>1 and snippet[1] is not None:
            try:
                page=Image.fromarray(snippet[1]).convert("RGB")
                candidates=snippet[4] if len(snippet)>4 and isinstance(snippet[4],list) else []
                case=str(record.get("confirmed") or record.get("candidate") or "")
                def same_case(value):
                    try:return tuple(map(int,str(value).split("-",1)))==tuple(map(int,case.split("-",1)))
                    except (ValueError,TypeError):return False
                chosen=next((h for h in candidates if h.get("rect") and same_case(h.get("number"))),None)
                rect=(record.get("manual_crop_rect") if record.get("manual_crop_origin")=="drawn"
                      else chosen.get("rect") if chosen else None)
                rect=rect or snippet[3] or next((h.get("rect") for h in candidates if h.get("rect")),None)
                if rect:
                    x0,y0,x1,y1=map(int,map(float,rect))
                    w=max(10,x1-x0);h=max(10,y1-y0)
                    # Include the label to the LEFT of the number.
                    left=min(210,max(35,int(w*1.7)))
                    box=(max(0,x0-left),max(0,y0-max(11,int(h*.4))),
                         min(page.width,x1+max(12,int(w*.14))),
                         min(page.height,y1+max(11,int(h*.4))))
                else:
                    side=int(round(3*72*2.2))
                    box=(max(0,page.width-side),0,page.width,min(page.height,side))
                crop=page.crop(box)
                crop.thumbnail((216,64),Image.Resampling.LANCZOS)
                tile=Image.new("RGB",(222,68),"white")
                tile.paste(crop,((222-crop.width)//2,(68-crop.height)//2))
            except Exception as exc:
                self._log("Case preview crop failed: %s",exc)
        if tile is None and cache_path.is_file():
            try:
                tile=Image.open(cache_path).convert("RGB")
                tile.thumbnail((222,68),Image.Resampling.LANCZOS)
            except Exception:
                tile=None
        if tile is None:
            tile=Image.new("RGB",(222,68),"white")
            draw=ImageDraw.Draw(tile)
            msg="Scan for preview" if not record.get("candidate") else "Crop unavailable"
            draw.text((9,25),msg,fill="#555555")
        ImageDraw.Draw(tile).rectangle((0,0,221,67),outline="#999999",width=1)
        if snippet and snippet[1] is not None:
            try:
                cache_path.parent.mkdir(parents=True,exist_ok=True)
                tile.save(cache_path)
            except OSError:
                pass
        photo=ImageTk.PhotoImage(tile)
        self._scan_preview_photos[iid]=photo
        self._scan_preview_keys[iid]=key
        return photo

    def _schedule_scan_preview_refresh(self):
        if not hasattr(self,"tree") or self._scan_preview_refresh_id is not None:
            return
        try:
            self._scan_preview_refresh_id=self.root.after_idle(self._refresh_scan_previews)
        except tk.TclError:
            self._scan_preview_refresh_id=None

    def _refresh_scan_previews(self):
        self._scan_preview_refresh_id=None
        if not hasattr(self,"tree") or not self.tree.winfo_exists():
            return
        visible=set()
        height=max(self.tree.winfo_height(),75)
        for y in range(0,height+74,36):
            iid=self.tree.identify_row(y)
            if iid and iid in self.records and iid not in visible:
                visible.add(iid)
                bbox=self.tree.bbox(iid,"preview")
                if not bbox:
                    continue
                x,top,w,h=bbox
                photo=self._make_scan_preview(iid,self.records[iid])
                widget=self._scan_preview_widgets.get(iid)
                if widget is None:
                    widget=tk.Label(self.tree,borderwidth=0,highlightthickness=0,
                                    cursor="hand2")
                    widget.bind("<Button-1>",lambda e,key=iid:self._select_preview_row(key))
                    widget.bind("<Double-1>",lambda e,key=iid:self._open_preview_review(key))
                    self._scan_preview_widgets[iid]=widget
                widget.configure(image=photo)
                width=min(222,max(0,w-6));image_height=min(68,max(0,h-4))
                if width>0 and image_height>0:
                    widget.place(x=x+(w-width)//2,y=top+(h-image_height)//2,
                                 width=width,height=image_height)
        for iid,widget in list(self._scan_preview_widgets.items()):
            if iid not in visible or not self.tree.exists(iid):
                try:widget.place_forget()
                except tk.TclError:pass
            if not self.tree.exists(iid):
                try:widget.destroy()
                except tk.TclError:pass
                self._scan_preview_widgets.pop(iid,None)
                self._scan_preview_photos.pop(iid,None)
                self._scan_preview_keys.pop(iid,None)

    def _select_preview_row(self,iid):
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self._refresh_review_controls()

    def _open_preview_review(self,iid):
        self._select_preview_row(iid)
        if iid in self._review_ready_ids():
            self.review_selected()

    def _insert_scan_row(self, iid, record, candidate_override=None, score_override=None):
        """Keep case number and PDF visible until explicit user transfer."""
        if not hasattr(self,"tree"):return
        if self.folder and Path(record["path"]).parent != Path(self.folder):
            if self.tree.exists(iid):
                self.tree.delete(iid)
            return
        values=("☑" if iid in self.scan_checked_iids else "☐",
                record["path"].name,"",
                (record.get("confirmed") if record.get("manual_approved")
                 and record.get("confirmed") else record.get("candidate",""))
                if candidate_override is None else candidate_override,
                (f'{float(record.get("score") or 0):.1f}%'
                 if record.get("score") else "—") if score_override is None else score_override,
                self._workflow_status(record),
                record.get("status","Not scanned"))
        if self.tree.exists(iid):
            self.tree.item(iid,values=values)
        else:
            self.tree.insert("","end",iid=iid,values=values)
        if iid in self.scan_checked_iids and iid not in self.tree.selection():
            self.tree.selection_add(iid)

        btn=getattr(self,"send_selected_btn",None)
        if btn is not None:
            allowed=(not self.busy and not self.export_busy and not self.trocr_training
                     and any(self._eligible_for_kurt(x,self.records[x])
                             for x in self.tree.selection() if x in self.records))
            btn.config(state="normal" if allowed else "disabled")
        self._schedule_scan_preview_refresh()

    def _send_all_selected(self):
        """Send only selected, approved, unsent PDFs; no misleading send-all."""
        if self.busy or self.trocr_training or self.export_busy:
            self.status.config(text="Wait until scanning or training finishes before sending.")
            return
        chosen=[iid for iid in self.tree.selection()
                if iid in self.records and
                self._eligible_for_kurt(iid,self.records[iid],automatic=False)]
        if not chosen:
            messagebox.showinfo("Nothing approved",
                "Select one or more COMPLETED files. REVIEW and SENT files will not be sent.")
            return
        self.export(selected_iids=chosen,automatic=False)

    def _unified_double_click(self,event):
        """Only double-click may reopen COMPLETED / SENT; explicitly confirm."""
        iid=self.tree.identify_row(event.y)
        if not iid or iid not in self.records:return
        rec=self.records[iid]
        state=self._workflow_status(rec)
        if state in ("COMPLETED","SENT"):
            if not messagebox.askyesno(
                "Reopen verified file",
                f"{rec['path'].name} is {state}.\n\n"
                "Reopen it for manual review? This can change its case number. "
                "A SENT file will be moved back into the root folder first. "
                "No changes will occur if you choose No."):
                return
            if state=="SENT":
                current=Path(rec["path"])
                if not current.is_file():
                    messagebox.showerror("Missing sent PDF","The sent PDF no longer exists.")
                    return
                intended=rec.get("original_name") or current.name
                original=self.folder / Path(intended).name
                if original.exists():
                    messagebox.showerror("Filename collision",
                       f"Cannot restore {intended}; a file already exists in the root. "
                       "No existing file was replaced.")
                    return
                if current.parent.resolve()!=(self.folder/"COMPLETED").resolve():
                    messagebox.showerror("Unsafe location",
                       "This file is not in the selected folder's COMPLETED directory.")
                    return
                try:
                    os.replace(current,original)
                except OSError as exc:
                    messagebox.showerror("Restore failed",str(exc))
                    return
                rec["previous_export_name"]=rec.get("export_name") or current.name
                rec["path"]=original
                rec["exported"]=False
                rec["export_name"]=""
            rec["confirmed"]=""
            rec["auto_labeled"]=False
            rec["manual_approved"]=False
            rec["sequence_inferred"]=False
            rec["sequence_excluded"]=True
            rec["send_after_manual_approval"]=False
            rec["status"]="Reopened for manual review"
            rec["export_error"]=""
            self.scan_checked_iids.add(iid)
            if self.completed_tree.exists(iid):
                self.completed_tree.delete(iid)
            rec["completed_iid"]=None
            self._insert_scan_row(iid,rec)
            self._save_state()
        elif state=="NOT SCANNED":
            messagebox.showinfo("Scan required",
                                "Run SCAN before opening the verification window.")
            return
        self.review_queue=[iid]
        self._show_review_item(0)

    def _open_instructions(self):
        win=tk.Toplevel(self.root)
        win.title("MFCase — Instructions")
        win.geometry("800x600")
        win.transient(self.root)
        frame=ttk.Frame(win,padding=18)
        frame.pack(fill="both",expand=True)
        text=tk.Text(frame,wrap="word",font=("Segoe UI",10))
        text.pack(fill="both",expand=True)
        text.insert("1.0",
            "M.F. CASE NUMBER OCR — HOW TO USE\n\n"
            "1. SELECT FILES chooses a folder. The root PDFs and the root/COMPLETED "
            "subfolder are displayed together; no other subfolders are scanned.\n\n"
            "2. SCAN analyzes selected, not-yet-scanned files in natural filename order. "
            "OCR examines the top-right 3 by 3 inches and prioritizes labeled "
            "INCIDENT NO. or CASE NUMBER fields.\n\n"
            "3. REVIEW means an uncertain OCR reading needs a person. Double-click "
            "a REVIEW row, inspect its case, optionally draw a crop box, choose "
            "Handwritten or Printed if known, and press SAVE AND SEND. SKIP leaves "
            "the file for later.\n\n"
            "4. COMPLETED means an approved PDF is ready to transfer. Select "
            "COMPLETED rows and click SEND ALL SELECTED.\n\n"
            "5. SENT means the renamed PDF has actually been moved to the "
            "COMPLETED subfolder. The program never creates duplicate sent PDFs. "
            "Only double-clicking followed by confirmation permits reopening "
            "COMPLETED or SENT. For SENT, the PDF moves back to root without "
            "overwriting an existing filename.\n\n"
            "6. Learning examples use the manually drawn crop when provided; "
            "otherwise the selected OCR box. The confirmed case is the training "
            "answer. Category selection is optional.\n\n"
            "7. OFFLINE: after downloading OCR models, PDF recognition runs "
            "locally. Shared learning and centralized logs require the "
            "authorized network drive; updates require GitHub internet access. "
            "The current startup policy may prevent scanning until shared "
            "learning and OCR models are available.\n\n"
            "8. EXPORT WORK LOG produces a diagnostic ZIP for troubleshooting. "
            "Do not upload sensitive case material to the public GitHub repo. "
            "Use the restricted shared learning drive for coworker diagnostics.\n")
        text.configure(state="disabled")
        ttk.Button(frame,text="CLOSE",command=win.destroy).pack(anchor="e",pady=8)

    def _check_connection_status(self):
        """Report LOCAL state without HTTP, SMB probes or other network I/O."""
        label=getattr(self,"connection_status_label",None)
        if label is None:
            return
        models=bool(self.reader is not None and self.trocr_model is not None)
        learning=bool(self.learning_dir and
                      Path(self.learning_dir).is_dir())
        label.config(text=(
            f"OCR: {'OFFLINE READY' if models else 'LOCAL MODELS NOT READY'}   |   "
            f"Learning: {'LOCAL' if learning else 'NOT READY'}   |   "
            "Internet: NOT CONTACTED   |   "
            "Updates: MANUAL BUTTONS ONLY   |   "
            "Work logs: LOCAL ONLY"))
    def _obsolete_insert_scan_row(self, iid, record, candidate_override=None, score_override=None):
        """Insert/update a single scan row with an independent checkbox."""
        if not hasattr(self, "tree"):
            return
        values = (
            "☑" if iid in self.scan_checked_iids else "☐",
            record["path"].name,
            record.get("candidate", "") if candidate_override is None else candidate_override,
            (f'{float(record.get("score") or 0):.1f}%' if record.get("score") else "—")
                if score_override is None else score_override,
            record.get("status", "Not scanned"))
        if self.tree.exists(iid):
            self.tree.item(iid, values=values)
        else:
            self.tree.insert("", "end", iid=iid, values=values)
        if iid in self.scan_checked_iids and iid not in self.tree.selection():
            self.tree.selection_add(iid)

    def _sync_scan_selection(self, event=None):
        selected = set(self.tree.selection())
        for iid in self.tree.get_children(""):
            self._set_scan_checkbox(iid, iid in selected)
        self._refresh_scan_checkbox_heading()
        self._update_scan_file_count()
        self._refresh_scan_button()
        self._refresh_rescan_button()
        self._refresh_review_controls()
        self._save_state()

    def _refresh_scan_checkbox_heading(self):
        if not hasattr(self, "tree"):
            return
        visible = set(self.tree.get_children(""))
        all_selected = bool(visible) and visible.issubset(self.scan_checked_iids)
        self.tree.heading("scan_select", text="☑ ALL" if all_selected else "☐ ALL")

    def _set_scan_checkbox(self, iid, checked):
        if iid not in self.records:
            return
        self.records[iid]["scan_selected"] = bool(checked)
        if checked:
            self.scan_checked_iids.add(iid)
        else:
            self.scan_checked_iids.discard(iid)
        if self.tree.exists(iid):
            self.tree.set(iid, "scan_select", "☑" if checked else "☐")
            highlighted = iid in self.tree.selection()
            if checked and not highlighted:
                self.tree.selection_add(iid)
            elif not checked and highlighted:
                self.tree.selection_remove(iid)

    def _toggle_scan_select_all(self):
        if self.busy or self.trocr_training:
            return
        visible = set(self.tree.get_children(""))
        make_checked = not (bool(visible) and visible.issubset(self.scan_checked_iids))
        for iid in visible:
            self._set_scan_checkbox(iid, make_checked)
        self._refresh_scan_checkbox_heading()
        self._update_scan_file_count()
        self._refresh_scan_button()
        self._save_state()

    def _on_scan_checkbox_click(self, event):
        if (self.tree.identify_region(event.x, event.y) == "cell"
                and self.tree.identify_column(event.x) == "#1"):
            if not self.busy and not self.trocr_training:
                iid = self.tree.identify_row(event.y)
                if iid in self.records and self.tree.exists(iid):
                    self._set_scan_checkbox(iid, iid not in self.scan_checked_iids)
                    self._refresh_scan_checkbox_heading()
                    self._update_scan_file_count()
                    self._refresh_scan_button()
                    self._save_state()
            return "break"

    def _select_all_for_rescan(self, event=None):
        """Ctrl+A highlights all eligible root-level PDF rows for RESCAN SELECTED.

        Do not override Ctrl+A in text-entry widgets or the COMPLETED view.
        Highlighting does not alter scan checkboxes, original files, or records.
        """
        if getattr(self, "_active_main_view", "select") != "select":
            return None
        widget = getattr(event, "widget", None)
        if widget is not None and isinstance(widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, tk.Spinbox)):
            return None
        if not self._startup_ready or self.busy or self.trocr_training or self.export_busy:
            return "break"
        selected = tuple(iid for iid in self.tree.get_children("") if self._rescan_eligible(iid))
        if selected:
            self.tree.selection_set(selected)
            self.tree.focus(selected[0])
            self._refresh_rescan_button()
            self.status.config(text=f"{len(selected):,} unresolved PDF(s) highlighted. Click RESCAN SELECTED to rescan.")
        else:
            self.tree.selection_remove(self.tree.selection())
            self._refresh_rescan_button()
            self.status.config(text="No eligible unresolved PDFs to rescan.")
        return "break"

    def _rescan_eligible(self, iid):
        """Only unresolved PDFs still in the selected root are eligible.

        Completed, approved, exported and BLANKS records must not be altered by
        a targeted rescan, even if a stale UI selection contains their IDs.
        """
        rec = self.records.get(iid)
        if not rec or not self.folder or not self.tree.exists(iid):
            return False
        if (rec.get("confirmed") or rec.get("exported")
                or rec.get("manual_approved") or rec.get("auto_labeled")
                or rec.get("status") == "Moved to BLANKS"):
            return False
        path = Path(rec.get("path", ""))
        try:
            return (path.is_file() and path.suffix.lower() == ".pdf"
                    and path.parent.resolve() == self.folder.resolve())
        except OSError:
            return False

    def _refresh_rescan_button(self):
        btn = getattr(self, "rescan_btn", None)
        if btn is None or not hasattr(self, "tree"):
            return
        allowed = (self._startup_ready and not self.busy
                   and not self.trocr_training and not self.export_busy
                   and any(self._rescan_eligible(iid) for iid in self.tree.selection()))
        btn.config(state="normal" if allowed else "disabled")

    def rescan_selected(self):
        """Rescan ONLY highlighted, unresolved rows; retain all original PDFs.

        This never resets completed files and never touches learning examples.
        Checkbox state governs ordinary SCAN; highlight state governs RESCAN.
        """
        if not self._startup_ready or self.busy or self.trocr_training or self.export_busy:
            return
        selected = [iid for iid in self.tree.selection() if self._rescan_eligible(iid)]
        if not selected:
            messagebox.showinfo("Rescan files", "Highlight one or more unresolved files in the scan list first.\nUse Ctrl or Shift to select multiple rows.")
            return
        if not messagebox.askyesno(
                "Rescan selected files",
                f"Rescan {len(selected):,} highlighted PDF(s)?\n\n"
                "Previous OCR suggestions for only these files will be replaced. "
                "Other files, completed approvals, originals, and shared learning will be preserved."):
            return
        for iid in selected:
            rec = self.records[iid]
            rec.update({
                "candidate": "", "confirmed": "", "status": "Not scanned",
                "snippet": None, "preview_meta": {}, "manual_crop_rect": None,
                "handwritten": False, "score": 0.0, "engines": [],
                "engine_agreement": False, "auto_labeled": False,
                "manual_approved": False, "scan_source": "",
                "scan_ambiguous": False, "sequence_inferred": False,
                "sequence_conflict": False, "sequence_anchors": [],
                "sequence_suggestion": "", "sequence_excluded": False,
                "export_error": "", "scan_selected": True
            })
            self.scan_checked_iids.add(iid)
            self._insert_scan_row(iid, rec)
        self._save_state()
        self._update_scan_file_count()
        self._log("Targeted rescan requested for %d selected PDF(s)", len(selected))
        self.scan(only_iids=set(selected))

    def _refresh_scan_button(self):
        if not hasattr(self, "scan_btn") or self.busy or self.trocr_training:
            return
        if not self._startup_ready:
            self.scan_btn.config(state="disabled")
            return
        candidates = any(
            iid in self.scan_checked_iids and self.tree.exists(iid)
            and not rec.get("confirmed") and not rec.get("exported")
            and rec.get("status") != "Moved to BLANKS"
            and (rec.get("status") == "Not scanned"
                 or str(rec.get("status", "")).startswith("Scan error:"))
            and Path(rec["path"]).is_file()
            for iid, rec in self.records.items())
        self.scan_btn.config(state="normal" if candidates else "disabled")
        self._refresh_rescan_button()
        button=getattr(self,"send_selected_btn",None)
        if button is not None:
            allowed=(self._startup_ready and not self.busy and
                    not self.export_busy and not self.trocr_training and
                    any(self._eligible_for_kurt(iid,self.records[iid])
                        for iid in self.tree.selection() if iid in self.records))
            button.config(state="normal" if allowed else "disabled")

    def clear_scan_screen(self):
        """Clear only visible scan rows; never remove PDF files or saved learning."""
        if self.busy or self.trocr_training or self.export_busy:
            self.status.config(text="Clear is available after scanning, learning, or sending finishes.")
            return
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self._refresh_scan_checkbox_heading()
        self._update_scan_file_count()
        self._refresh_scan_button()
        self.status.config(text="Scan list cleared. No PDFs, approvals, or learning were deleted. Click REFRESH to restore it.")


    def _refresh_timeout(self,generation):
        """Never keep the Tk loading overlay indefinitely on an SMB delay.

        The worker may still finish and update the list later. Existing saved
        decisions remain intact; the UI stays usable during slow network I/O.
        """
        self._folder_load_timeout_id=None
        if generation!=self._folder_load_generation:
            return
        self._loader_finish("rolls")
        self.refresh_root_btn.config(state="normal")
        self.choose_folder_btn.config(state="normal")
        self.status.config(
            text="Folder refresh is taking longer than expected. Current PDFs "
                 "remain visible; delayed network results will load when ready.")
        self._diag_event("folder_refresh_wait",
            outcome="overlay_released_while_network_worker_pending",
            generation=generation)

    def refresh_root_folder(self):
        """Refresh direct root PDFs only, retaining all saved manual decisions."""
        if not self._startup_ready or self.busy or self.trocr_training or self.export_busy:
            return
        if "rolls" in self._loading_jobs:
            self.status.config(text="Folder refresh is already in progress.")
            return
        if not self.folder or not self.folder.is_dir():
            messagebox.showinfo("Choose a folder","Choose a folder first.")
            return
        self._save_state()
        self._folder_load_generation+=1
        generation=self._folder_load_generation
        self.refresh_root_btn.config(state="disabled")
        self._loader_start("rolls","Refreshing root-folder PDFs…")
        if self._folder_load_timeout_id is not None:
            self.root.after_cancel(self._folder_load_timeout_id)
        self._folder_load_timeout_id=self.root.after(
            16000,lambda generation=generation:self._refresh_timeout(generation))
        threading.Thread(target=self._folder_load_worker,
                         args=(self.folder,generation),daemon=True).start()

    def choose_folder(self):
        if not self._startup_ready or self._loading_jobs:
            return
        if self.busy or self.trocr_training or self.export_busy:
            messagebox.showinfo(
                "Processing in progress",
                "Pause or finish the current scan/training before changing folders.")
            return
        folder = filedialog.askdirectory(title="Select folder containing scanned PDFs")
        if not folder:
            return
        self.folder = Path(folder)
        self.folder_label.config(text=str(self.folder))
        self.log_path = self.folder / ".case_ocr_log.txt"
        # Folder I/O and shared learning access both run off the Tk thread.
        # The indicator stays animated until BOTH tasks report completion.
        self._folder_load_generation += 1
        generation = self._folder_load_generation
        self.scan_btn.config(state="disabled")
        self.choose_folder_btn.config(state="disabled")
        self.refresh_root_btn.config(state="disabled")
        self._loader_start("rolls", "Loading PDFs from the selected root folder…")
        self.status.config(text="Loading PDF rolls (existing shared learning remains loaded)…")
        threading.Thread(target=self._folder_load_worker, args=(self.folder, generation),
                         daemon=True, name="MFCase-LoadRolls").start()

    def _save_state(self):
        if not self.folder:
            return
        payload = {"version": 2, "updated_at": datetime.now().isoformat(timespec="seconds"), "files": {}}
        for rec in self.records.values():
            path = rec.get("path")
            if not path:
                continue
            payload["files"][str(rec.get("original_name") or path.name)] = {
                "candidate": rec.get("candidate", ""),
                "confirmed": rec.get("confirmed", ""),
                "status": rec.get("status", ""),
                "handwritten": bool(rec.get("handwritten", False)),
                "score": float(rec.get("score", 0.0) or 0.0),
                "engines": rec.get("engines", []),
                "engine_agreement": bool(rec.get("engine_agreement", False)),
                "auto_labeled": bool(rec.get("auto_labeled", False)),
                "manual_approved": bool(rec.get("manual_approved", False)),
                "send_after_manual_approval": bool(rec.get("send_after_manual_approval", False)),
                "exported": bool(rec.get("exported", False)),
                "export_name": rec.get("export_name", ""),
                "original_name": rec.get("original_name",""),
                "original_path": rec.get("original_path",""),
                "preview_meta": (
                    {"page_num": rec["snippet"][0], "label": rec["snippet"][2],
                     "rect": list(rec["snippet"][3]) if rec["snippet"][3] else None,
                     "candidates": rec["snippet"][4] if len(rec["snippet"]) > 4 else []}
                    if rec.get("snippet") else rec.get("preview_meta", {})
                ),
                "manual_crop_rect": list(rec["manual_crop_rect"]) if rec.get("manual_crop_rect") else None,
                "manual_crop_origin": rec.get("manual_crop_origin",""),
                "training_type": rec.get("training_type", "unsure"),
                "scan_source": rec.get("scan_source", ""),
                "scan_ambiguous": bool(rec.get("scan_ambiguous", False)),
                "sequence_inferred": bool(rec.get("sequence_inferred", False)),
                "sequence_conflict": bool(rec.get("sequence_conflict", False)),
                "sequence_anchors": list(rec.get("sequence_anchors", [])),
                "sequence_suggestion": rec.get("sequence_suggestion", ""),
                "sequence_excluded": bool(rec.get("sequence_excluded", False)),
                "scan_selected": bool(rec.get("scan_selected", True)),
                "learning_pending": bool(rec.get("learning_pending", False)),
                "export_error": str(rec.get("export_error", ""))
            }
        target = self.folder / ".case_ocr_state.json"
        temp = target.with_suffix(".json.tmp")
        try:
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp, target)
            return True
        except Exception:
            logging.exception("Failed to save queue state")
            return False


    def _diag_event(self, event, **details):
        """Append an analysis-ready JSON record without interrupting OCR."""
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            filepath=getattr(self,"_diag_file",None)
            if filepath is None:
                return
            payload={"schema":"mfcase.diagnostic.v1",
                     "utc":_dt.now(_tz.utc).isoformat(timespec="milliseconds"),
                     "run_id":getattr(self,"_diag_run_id",None),
                     "version":getattr(self,"app_version","unknown"),
                     "event":event}
            if getattr(self,"_diag_current_file",None):
                payload["file"]=self._diag_current_file
            payload.update(details)
            with self._diag_lock:
                with open(filepath,"a",encoding="utf-8",newline="\n") as handle:
                    handle.write(_json.dumps(payload,ensure_ascii=False,default=str,
                                             separators=(",",":"))+"\n")
        except Exception as exc:
            # Diagnostics must never block evidence processing.
            try: logging.warning("Diagnostic logging failed: %s",exc)
            except Exception: pass


    def _diag_sync_shared(self):
        """Never transmit diagnostic logs to SMB or the public internet."""
        return None

    def _diag_start_run(self):
        """Create a fresh, time-stamped run in local-only diagnostic storage."""
        try:
            import uuid
            from datetime import datetime as _dt, timezone as _tz
            directory=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"diagnostics"
            directory.mkdir(parents=True,exist_ok=True)
            run_id=_dt.now(_tz.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            self._diag_run_id=run_id
            self._diag_file=directory/("mfcase-"+run_id+".jsonl")
            self._diag_current_file=None
            self._diag_event("run_start",
                 selected_files=[Path(x[1]["path"]).name for x in self.scan_items],
                 scan_folder=str(self.folder),
                 year_prefix=self.scan_year_prefix,
                 scan_region_inches=[3,3],
                 printed_auto_threshold=100.0,handwritten_threshold=95.0,
                 auto_label_enabled=bool(self.auto_label_var.get()),
                 application_version=self.app_version)
            # Keep a bounded history on the workstation (not on the shared share).
            for excess in sorted(directory.glob("mfcase-*.jsonl"),reverse=True)[25:]:
                try: excess.unlink()
                except OSError: pass
        except Exception as exc:
            self._diag_file=None
            self._log("Could not initialize detailed diagnostic log: %s",exc)

    @staticmethod
    def _diag_hits(hits):
        return [{
            "number":str(h.get("number") or ""),
            "score":round(float(h.get("score") or 0),3),
            "bbox":list(h.get("rect")) if h.get("rect") is not None else None,
            "engines":list(h.get("engines") or []),
            "label_type":h.get("label_type"),
            "label_context":bool(h.get("label_context")),
            "learned_location":bool(h.get("learned_location"))
        } for h in (hits or [])[:100]]

    def _diag_export(self):
        """User-controlled ZIP export. No automatic upload or transmission."""
        import zipfile as _zipfile
        from datetime import datetime as _dt
        from tkinter import filedialog as _filedialog, messagebox as _messagebox
        active=getattr(self,"_diag_file",None)
        directory=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"diagnostics"
        logs=sorted(directory.glob("mfcase-*.jsonl"),reverse=True)[:10] if directory.is_dir() else []
        if active and Path(active).exists() and Path(active) not in logs:
            logs.insert(0,Path(active))
        if not logs:
            _messagebox.showinfo("Work log","No recorded scans yet. Run a scan first.")
            return
        suggested="MFCase_Work_Log_"+_dt.now().strftime("%Y%m%d_%H%M%S")+".zip"
        target=_filedialog.asksaveasfilename(
            title="Export MFCase OCR work log",defaultextension=".zip",
            initialfile=suggested,filetypes=[("ZIP archive","*.zip")])
        if not target:
            return
        notes=(
            "MFCase OCR diagnostic work log, schema mfcase.diagnostic.v1\n"
            "Each JSONL line is a standalone JSON event. Filter on event/file/run_id.\n"
            "Events: run_start, ocr_label_pass, field_ocr_pass, candidate_pass, "
            "ocr_candidates, candidate_filter, ocr_decision, row_decision, "
            "sequence_decision, transfer_result, run_end.\n"
            "bbox is [left,top,right,bottom] in rendered page pixels.\n"
            "score is the raw OCR engine score, not a statistical guarantee.\n"
            "ignored contains candidate reads excluded and an explicit reason.\n"
            "Do not share externally without checking department data policies.\n"
            "Contains filenames, possible identifiers and OCR text in the 3-inch zone.\n"
            "No original PDFs or images are included.\n")
        try:
            with _zipfile.ZipFile(target,"w",compression=_zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("HOW_TO_READ.txt",notes)
                for path in logs:
                    archive.write(path,arcname=path.name)
                if self.log_path and Path(self.log_path).is_file():
                    archive.write(self.log_path,arcname="existing_ocr_log.txt")
            _messagebox.showinfo("Work log saved",
                 "Diagnostic ZIP saved. You can upload it here for a complete "
                 "file-by-file OCR decision review.\n\n"+str(target))
        except Exception as exc:
            _messagebox.showerror("Work log export failed",str(exc))


    def _scan_progress_update(self,done=None,phase="SCANNING"):
        """Non-blocking Tk progress display reflecting actual PDF scan work."""
        total=len(getattr(self,"scan_items",[]) or [])
        if done is None:
            try:done=int(float(self.progress["value"]))
            except (ValueError,TypeError,tk.TclError):done=0
        done=max(0,min(total,int(done)))
        self.progress.config(maximum=max(1,total),value=done)
        pct=int(round(100*done/total)) if total else 0
        label=getattr(self,"scan_progress_label",None)
        if label is not None:
            label.configure(text=f"{phase} — {done} of {total} PDF files")
        pct_label=getattr(self,"scan_progress_pct",None)
        if pct_label is not None:
            pct_label.configure(text=f"{pct}%")
        self._diag_event("scan_progress",done=done,total=total,
                         percentage=pct,phase=phase)



    @staticmethod
    def _is_corner_case_rect(rect,image):
        """Require the physical upper-right handwritten case-number zone."""
        if rect is None or image is None:
            return False
        try:
            h,w=image.shape[:2]
            x0,y0,x1,y1=map(float,rect)
            return (x0>=0 and y0>=0 and x1>x0 and y1>y0
                    and (x0+x1)/2>=w*.82
                    and y0<=h*.24 and x1<=w+6)
        except (TypeError,ValueError,AttributeError):
            return False

    def _prefer_case_margin(self,hits,kind,image):
        if kind not in ("header","fallback") or not hits:
            return hits
        strong_margin=[h for h in hits if
            self._is_corner_case_rect(h.get("rect"),image)]
        if not strong_margin:
            self._diag_event("margin_priority",source=kind,
                outcome="no_corner_case_read",
                candidates=self._diag_hits(hits))
            return hits
        # Only variants in the same PHYSICAL case-number zone are compared.
        # Any separate number to its left is outside the intended case field
        # and must not create a false conflict.
        ignored=[h for h in hits if h not in strong_margin]
        self._diag_event("margin_priority",source=kind,
            outcome="right_margin_priority",kept=self._diag_hits(strong_margin),
            ignored=[{"candidate":x,"reason":"outside_upper_right_case_field"}
                     for x in self._diag_hits(ignored)])
        return strong_margin

    def _resolve_ocr_alternates(self,hits,kind,best):
        """Keep real conflicting case fields; suppress unsupported variants.

        The selected reading and its ORIGINAL (non-enhanced) OCR box receive
        precedence. Enhanced/TrOCR-only variants over the same physical box
        cannot veto a primary >=95% read (100% for unlabelled fallback).
        Distinct independently detected >=95% cases in one field still require
        manual review. All discarded alternatives are recorded with reasons.
        """
        if not hits:
            return hits
        key=lambda h:tuple(int(v) for v in str(h.get("number") or "").split("-",1))
        primary_score=float(best.get("score") or 0)
        minimum=95.0
        if primary_score<minimum or not best.get("rect"):
            return hits
        rect=tuple(float(x) for x in best["rect"])
        x0,y0,x1,y1=rect
        cw=max(1.0,x1-x0);ch=max(1.0,y1-y0)
        cx=(x0+x1)/2.0;cy=(y0+y1)/2.0
        p_eng={str(v) for v in best.get("engines") or []}
        p_raw=("EasyOCR" in p_eng or any(
            v.startswith("EasyOCR-") and "enhanced" not in v.lower() for v in p_eng))
        def raw_engine(item):
            return any(str(e)=="EasyOCR" or (
                str(e).startswith("EasyOCR-") and
                "enhanced" not in str(e).lower())
                for e in (item.get("engines") or []))
        retained=[]
        dismissed=[]
        for h in hits:
            reason=None
            if key(h)==key(best):
                retained.append(h)
                continue
            score=float(h.get("score") or 0)
            r=h.get("rect")
            if not r:
                reason="no_independent_location"
            else:
                ax0,ay0,ax1,ay1=map(float,r)
                acx=(ax0+ax1)/2.0;acy=(ay0+ay1)/2.0
                aw=max(1.,ax1-ax0);ah=max(1.,ay1-ay0)
                dx=abs(acx-cx);dy=abs(acy-cy)
                same_location=(
                    dx<=max(16.,min(cw,aw)*.65) and
                    dy<=max(12.,min(ch,ah)*.85))
                if score<95.0 and primary_score-score>=3.0:
                    reason="weak_secondary_ocr_variant"
                elif same_location:
                    # One physical number, different interpretations across
                    # contrast-enhanced and TrOCR passes. The independent
                    # original EasyOCR detection is the primary evidence.
                    if p_raw and not raw_engine(h):
                        reason="enhanced_or_trocr_variant_of_primary_box"
                    elif score<primary_score-3.0:
                        reason="substantially_weaker_same_box_read"
                    # Near-equal, independently detected raw reads remain a
                    # manual conflict (don't mask true ambiguity).
                else:
                    # Separate geometry within the 3x3 zone. Unanchored text
                    # away from the selected upper-right case box is not a
                    # second reading of THAT field.
                    if not h.get("label_context") and not h.get("label_type"):
                        reason="different_unlabeled_location_not_case_field"
                    elif not raw_engine(h) and p_raw:
                        reason="separate_enhanced_only_detection"
            if reason:
                dismissed.append({"candidate":self._diag_hits([h])[0],
                                  "reason":reason})
            else:
                retained.append(h)
        self._diag_event("case_conflict_resolution",source=kind,
            primary=self._diag_hits([best])[0],
            retained=self._diag_hits(retained),ignored=dismissed)
        return retained

    def _diag_field_filter(self,hits,source):
        """Prefer a uniquely perfect read from a labeled case field.

        Ignore obvious non-case strings, mismatched-year strings and lower
        confidence alternate OCR interpretations; audit all discarded reads.
        A distinct competing 100% same-year case remains a manual conflict.
        """
        filtered=[]
        ignored=[]
        expected=REQUIRED_CASE_YEAR
        for h in hits:
            number=str(h.get("number") or "").strip()
            match=re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,7})",number)
            if not match:
                ignored.append({"candidate":self._diag_hits([h])[0],"reason":"not_a_complete_case_number"})
                continue
            if match.group(1)!=REQUIRED_CASE_YEAR:
                ignored.append({"candidate":self._diag_hits([h])[0],
                                "reason":"required_literal_08_prefix_missing"})
                continue
            filtered.append(h)
        if source in ("incident","case") and filtered:
            perfect=[h for h in filtered if float(h.get("score") or 0)>=95.0]
            if perfect:
                groups={(int(str(h["number"]).split("-")[0]),
                         int(str(h["number"]).split("-")[1])) for h in perfect}
                if len(groups)==1:
                    for h in filtered:
                        if h not in perfect:
                            ignored.append({"candidate":self._diag_hits([h])[0],
                                            "reason":"below_unique_95pct_label_read"})
                    filtered=perfect
        self._diag_event("candidate_filter",source=source,
              kept=self._diag_hits(filtered),ignored=ignored,
              expected_year=expected)
        return filtered

    def _log(self, message, *args):
        try:
            logging.info(message, *args)
        except Exception:
            pass

    def _year_prefix(self):
        """This release processes only 2008 case numbers written as 08-NNN."""
        if self.year_var.get().strip()!=REQUIRED_CASE_YEAR:
            self.year_var.set(REQUIRED_CASE_YEAR)
        return REQUIRED_CASE_YEAR

    def _learning_example_id(self, example):
        existing = str(example.get("example_id") or "").strip()
        if existing:
            return existing
        image = str(example.get("image") or "").strip()
        source = str(example.get("source_pdf") or "").strip()
        confirmed = str(example.get("confirmed_case_number") or "").strip()
        raw = f"{image}|{source}|{confirmed}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest() if image and confirmed else ""

    def _merge_legacy_learning_data(self, legacy_dir):
        """Merge a prior folder-local library into the central library without deleting it."""
        if not self.learning_dir or not legacy_dir or not legacy_dir.is_dir():
            return
        try:
            if legacy_dir.resolve() == self.learning_dir.resolve():
                return
            central_db = self.learning_dir / "learning_examples_v3.json"
            local_db = next(
                (legacy_dir / name for name in (
                    "learning_examples_v3.json",
                    "learning_examples_v2.json",
                    "learning_examples.json")
                 if (legacy_dir / name).is_file()),
                None)
            if local_db is None:
                return

            central_source = next(
                (self.learning_dir / name for name in (
                    "learning_examples_v3.json",
                    "learning_examples_v2.json",
                    "learning_examples.json")
                 if (self.learning_dir / name).is_file()),
                None)
            central = (
                json.loads(central_source.read_text(encoding="utf-8"))
                if central_source else {})
            local = json.loads(local_db.read_text(encoding="utf-8"))
            central_examples = list(central.get("examples", []))
            known = {
                self._learning_example_id(ex)
                for ex in central_examples
                if self._learning_example_id(ex)
            }

            copied_images = 0
            for example in local.get("examples", []):
                example = dict(example)
                rel = str(example.get("image") or "").strip()
                if rel:
                    source_image = (legacy_dir / rel).resolve()
                    target_image = (self.learning_dir / rel).resolve()
                    try:
                        source_image.relative_to(legacy_dir.resolve())
                        target_image.relative_to(self.learning_dir.resolve())
                    except ValueError:
                        example["image"] = None
                    else:
                        if source_image.is_file() and not target_image.exists():
                            target_image.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source_image, target_image)
                            copied_images += 1
                        if not target_image.is_file():
                            example["image"] = None
                example_id = self._learning_example_id(example)
                if example_id and example_id not in known:
                    example["example_id"] = example_id
                    central_examples.append(example)
                    known.add(example_id)

            central["examples"] = central_examples
            corrections = dict(central.get("corrections", {}))
            corrections.update(local.get("corrections", {}))
            central["corrections"] = corrections

            regions = list(central.get("field_regions", []))
            region_keys = {
                (tuple(r.get("rect_norm", [])), r.get("source_pdf"),
                 r.get("confirmed_case_number"))
                for r in regions
            }
            for region in local.get("field_regions", []):
                key = (tuple(region.get("rect_norm", [])), region.get("source_pdf"),
                       region.get("confirmed_case_number"))
                if key not in region_keys:
                    regions.append(region)
                    region_keys.add(key)
            central["field_regions"] = regions

            for key in (
                "trocr_v2_trained_count", "trocr_v2_attempted_count",
                "trocr_v2_validation", "training_type_validation",
                "last_training_summary"
            ):
                if key not in central and key in local:
                    central[key] = local[key]
            for key in ("trocr_v3_attempted_ids", "trocr_v3_trained_ids"):
                central[key] = sorted(set(central.get(key, []) or []) |
                                      set(local.get(key, []) or []))

            # Keep the central active model if one already exists. Copy a legacy
            # checkpoint only when the central share has no corresponding model.
            for model_name in ("trocr_v2_finetuned", "trocr_v2_previous"):
                local_model = legacy_dir / model_name
                central_model = self.learning_dir / model_name
                if local_model.is_dir() and not central_model.exists():
                    shutil.copytree(local_model, central_model)

            temp = central_db.with_suffix(".json.tmp")
            temp.write_text(json.dumps(central, indent=2), encoding="utf-8")
            os.replace(temp, central_db)
            self._log(
                "Merged legacy learning data from %s into central library: %s examples, %s image crops copied",
                legacy_dir, len(central_examples), copied_images)
        except Exception as exc:
            # The original local library is never deleted or modified.
            self._log("Legacy learning merge failed; original data preserved at %s: %s",
                      legacy_dir, exc)

    def _pending_learning_examples(self):
        """Return confirmed image crops that have not yet been used for training."""
        if not self.learning_dir:
            return []
        attempted = set(self.learning_data.get("trocr_v3_attempted_ids", []))
        pending = []
        for example in self.learning_data.get("examples", []):
            if (not example.get("image") or not CASE_RE.fullmatch(
                    str(example.get("confirmed_case_number") or ""))):
                continue
            example_id = self._learning_example_id(example)
            if not example_id or example_id in attempted:
                continue
            image_path = (self.learning_dir / str(example["image"])).resolve()
            try:
                image_path.relative_to(self.learning_dir.resolve())
            except ValueError:
                continue
            if image_path.is_file():
                item = dict(example)
                item["example_id"] = example_id
                pending.append(item)
        return pending

    @staticmethod
    def _even_sample(items, limit):
        """Select a deterministic, evenly spaced replay sample."""
        items = list(items or [])
        limit = max(0, int(limit))
        if limit == 0 or not items:
            return []
        if len(items) <= limit:
            return items
        if limit == 1:
            return [items[len(items) // 2]]
        indexes = [round(i * (len(items) - 1) / (limit - 1)) for i in range(limit)]
        return [items[index] for index in indexes]

    def _refresh_learning_button_state(self):
        """Keep UPDATE LEARNING enabled only when enough new confirmed crops exist."""
        button = getattr(self, "finish_batch_btn", None)
        if button is None:
            return
        if not self.learning_dir or self.busy or self.trocr_training:
            button.config(state="disabled")
            return
        attempted = bool(self.learning_data.get("trocr_v3_attempted_ids", []))
        required = 5 if attempted else 10
        pending = len(self._pending_learning_examples())
        button.config(
            state="normal" if pending >= required else "disabled",
            text=f"UPDATE LEARNING (ALL {pending} new; min {required})")

    def _learning_connect_worker(self, learning_dir, legacy_dir):
        """Connect/load/synchronize the central library without blocking Tk."""
        try:
            learning_dir.mkdir(parents=True, exist_ok=True)
            if not learning_dir.is_dir():
                raise OSError("The centralized learning location is not a directory.")
            self.learning_dir = learning_dir
            self._merge_legacy_learning_data(legacy_dir)
            self._load_learning_data(refresh_ui=False)
            self.queue.put(("learning_connect_result", True, "Shared learning library connected."))
        except Exception as exc:
            logging.exception("Background connection to shared learning library failed")
            self.learning_dir = None
            self.learning_data = {"examples": [], "corrections": {},
                                  "trocr_v2_trained_count": 0,
                                  "trocr_v2_attempted_count": 0,
                                  "trocr_v2_validation": {}, "field_regions": [],
                                  "training_type_validation": {}, "last_training_summary": {},
                                  "trocr_v3_attempted_ids": [], "trocr_v3_trained_ids": []}
            self.queue.put(("learning_connect_result", False, str(exc)))

    @contextmanager
    def _shared_learning_write_lock(self, timeout=20):
        """Atomic SMB-compatible lock directory: never overwrite another worker's lock."""
        if not self.learning_dir:
            raise RuntimeError("Shared learning directory is unavailable")
        lock_dir = self.learning_dir / ".mfcase_write_lock"
        started = time.monotonic()
        while True:
            try:
                lock_dir.mkdir()
                break
            except FileExistsError:
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Shared learning is being updated by another workstation. Retry after it finishes; the lock was not removed automatically.")
                time.sleep(0.2)
        try:
            (lock_dir / "owner.txt").write_text(f"{os.getpid()} {datetime.now().isoformat()}\n", encoding="utf-8")
            yield
        finally:
            try:
                (lock_dir / "owner.txt").unlink(missing_ok=True)
                lock_dir.rmdir()
            except OSError:
                self._log("Shared learning write lock cleanup needs attention: %s", lock_dir)

    def _persist_learning_data(self, append_example=None, strict_revision=False):
        """Serialize network writes, merge new samples, and retain a last-good DB backup."""
        with self._shared_learning_write_lock():
            db = self.learning_dir / "learning_examples_v3.json"
            previous = json.loads(db.read_text(encoding="utf-8")) if db.is_file() else {}
            if not isinstance(previous, dict):
                raise ValueError("Existing network learning DB is not a JSON object")
            if strict_revision and self._learning_revision is not None:
                db_revision = (db.name, db.stat().st_mtime_ns, db.stat().st_size) if db.exists() else None
                if db_revision != self._learning_revision:
                    raise RuntimeError("Shared learning changed on another computer; reload before changing or deleting existing examples")
            if append_example is not None:
                latest_examples = previous.get("examples", [])
                if not isinstance(latest_examples, list):
                    raise ValueError("Shared examples are malformed; refusing to overwrite")
                ids = {self._learning_example_id(ex) for ex in latest_examples}
                if self._learning_example_id(append_example) not in ids:
                    latest_examples.append(append_example)
                self.learning_data["examples"] = latest_examples
                latest_regions = previous.get("field_regions", [])
                local_regions = self.learning_data.get("field_regions", [])
                seen = {json.dumps(region, sort_keys=True) for region in latest_regions}
                for region in local_regions:
                    key = json.dumps(region, sort_keys=True)
                    if key not in seen:
                        latest_regions.append(region)
                        seen.add(key)
                self.learning_data["field_regions"] = latest_regions
                self.learning_data["corrections"] = {**previous.get("corrections", {}), **self.learning_data.get("corrections", {})}
                for field in ("trocr_v3_attempted_ids", "trocr_v3_trained_ids"):
                    self.learning_data[field] = sorted(set(previous.get(field, [])) | set(self.learning_data.get(field, [])))
            if db.is_file():
                backup = db.with_name("learning_examples_v3.last_good.json")
                shutil.copy2(db, backup)
            temp = db.with_name(db.name + f".{os.getpid()}.tmp")
            try:
                temp.write_text(json.dumps(self.learning_data, indent=2), encoding="utf-8")
                os.replace(temp, db)
            finally:
                temp.unlink(missing_ok=True)
            self._learning_revision = (db.name, db.stat().st_mtime_ns, db.stat().st_size)
            self._sync_learning_type_databases()

    def _load_learning_data(self, refresh_ui=True):
        if not self.learning_dir:
            self.learning_data = {
                "examples": [], "corrections": {},
                "trocr_v2_trained_count": 0, "trocr_v2_attempted_count": 0,
                "trocr_v2_validation": {}, "field_regions": [],
                "training_type_validation": {}, "last_training_summary": {},
                "trocr_v3_attempted_ids": [], "trocr_v3_trained_ids": []
            }
            if refresh_ui:
                self._refresh_learning_stats()
            return
        db_v3 = self.learning_dir / "learning_examples_v3.json"
        db_v2 = self.learning_dir / "learning_examples_v2.json"
        legacy_db = self.learning_dir / "learning_examples.json"
        try:
            source_db = db_v3 if db_v3.exists() else (db_v2 if db_v2.exists() else legacy_db)
            loaded = json.loads(source_db.read_text(encoding="utf-8")) if source_db.exists() else {}
            if not isinstance(loaded, dict) or not isinstance(loaded.get("examples", []), list) or not isinstance(loaded.get("corrections", {}), dict):
                raise ValueError("Shared learning database has invalid structure")
            self.learning_data = {
                "examples": loaded.get("examples", []),
                "corrections": loaded.get("corrections", {}),
                "trocr_v2_trained_count": int(loaded.get("trocr_v2_trained_count", 0)),
                "trocr_v2_attempted_count": int(loaded.get("trocr_v2_attempted_count", 0)),
                "trocr_v2_validation": loaded.get("trocr_v2_validation", {}),
                "field_regions": loaded.get("field_regions", []),
                "training_type_validation": loaded.get("training_type_validation", {}),
                "last_training_summary": loaded.get("last_training_summary", {}),
                "trocr_v3_attempted_ids": list(loaded.get("trocr_v3_attempted_ids", [])),
                "trocr_v3_trained_ids": list(loaded.get("trocr_v3_trained_ids", []))
            }
            usable = []
            for example in self.learning_data["examples"]:
                example_id = self._learning_example_id(example)
                if example_id:
                    example["example_id"] = example_id
                    usable.append(example)
            attempted = set(self.learning_data["trocr_v3_attempted_ids"])
            trained = set(self.learning_data["trocr_v3_trained_ids"])
            legacy_attempted = max(
                self.learning_data["trocr_v2_attempted_count"],
                self.learning_data["trocr_v2_trained_count"])
            # Old versions tracked counts, not IDs. Mark the previously attempted
            # examples as used so the first V3.3.6 update does not re-run 200+ crops.
            if not attempted and legacy_attempted:
                attempted.update(self._learning_example_id(ex) for ex in usable[:legacy_attempted])
                attempted.discard("")
            if not attempted and usable and (
                self.learning_data.get("trocr_v2_validation") or
                self.learning_data.get("last_training_summary")
            ):
                attempted.update(self._learning_example_id(ex) for ex in usable)
                attempted.discard("")
            if not trained and self.learning_data["trocr_v2_trained_count"]:
                trained.update(self._learning_example_id(ex) for ex in usable[
                    :self.learning_data["trocr_v2_trained_count"]])
                trained.discard("")
            self.learning_data["trocr_v3_attempted_ids"] = sorted(attempted)
            self.learning_data["trocr_v3_trained_ids"] = sorted(trained)
            # A read must never rewrite the network DB: it can race with other workstations.
            self._learning_revision = (source_db.name, source_db.stat().st_mtime_ns, source_db.stat().st_size) if source_db.exists() else None
        except Exception as exc:
            logging.exception("Shared learning database could not be read; prior state preserved")
            raise RuntimeError(f"Shared learning is unreadable; scanning is blocked to protect corrections: {exc}") from exc
        if refresh_ui:
            self._refresh_learning_stats()

    @staticmethod
    def _normalized_training_type(value):
        value = str(value or "unsure").strip().lower()
        return value if value in {"handwritten", "printed"} else "unsure"

    def _sync_learning_type_databases(self):
        """Write independently inspectable category libraries from the V3 master set."""
        if not self.learning_dir:
            return
        examples = self.learning_data.get("examples", [])
        for category in ("handwritten", "printed", "unsure"):
            category_examples = [
                dict(ex) for ex in examples
                if self._normalized_training_type(ex.get("training_type")) == category]
            payload = {
                "category": category,
                "count": len(category_examples),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "examples": category_examples
            }
            target = self.learning_dir / f"learning_{category}_examples.json"
            temp = target.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp, target)

    def _show_learning_inspector(self):
        """Show what was saved, categorized, and actually tested during training."""
        if not self.learning_dir:
            messagebox.showinfo("Learning data", "Choose the source PDF folder first.")
            return
        win = tk.Toplevel(self.root)
        win.title("M.F. Learning Data Inspector")
        win.geometry("1050x650")
        win.transient(self.root)
        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)
        counts = {kind: sum(1 for ex in self.learning_data.get("examples", [])
                           if self._normalized_training_type(ex.get("training_type")) == kind
                           and ex.get("image"))
                  for kind in ("handwritten", "printed", "unsure")}
        ttk.Label(outer, text="Saved learning examples", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(outer, text=(f"Handwritten: {counts['handwritten']}   •   Printed: {counts['printed']}   •   "
                               f"Unclassified: {counts['unsure']}"),
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(3, 8))
        validation = self.learning_data.get("trocr_v2_validation", {})
        if validation:
            status_text = (f"Last model test: baseline {validation.get('baseline_accuracy_pct', 0):.1f}% → "
                           f"{validation.get('accuracy_pct', 0):.1f}% on held-out examples; "
                           f"{validation.get('training_examples', 0)} training examples; "
                           f"{validation.get('epochs_completed', 0)} passes; "
                           f"model {'activated' if validation.get('saved_as_active') else 'not replaced'}.")
        else:
            status_text = "No completed model validation run is recorded yet. Saving crops is not the same as updating model weights."
        ttk.Label(outer, text=status_text, wraplength=1000).pack(anchor="w", pady=(0, 8))
        ttk.Label(outer, text="Each row below is a saved confirmed crop. The category is chosen in Review; the OCR score is independent of this label.",
                  wraplength=1000).pack(anchor="w", pady=(0, 8))
        pane = ttk.Frame(outer); pane.pack(fill="both", expand=True)
        cols = ("type", "ocr", "confirmed", "source", "image")
        tree = ttk.Treeview(pane, columns=cols, show="headings", height=16)
        for col, title, width in (("type", "Learning library", 120), ("ocr", "Original OCR", 130),
                                  ("confirmed", "Confirmed answer", 140), ("source", "Source PDF", 310),
                                  ("image", "Saved crop", 250)):
            tree.heading(col, text=title); tree.column(col, width=width, anchor="w")
        sy = ttk.Scrollbar(pane, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sy.set)
        tree.pack(side="left", fill="both", expand=True); sy.pack(side="right", fill="y")
        for index, ex in enumerate(reversed(self.learning_data.get("examples", []))):
            if not ex.get("image"):
                continue
            category = self._normalized_training_type(ex.get("training_type"))
            tree.insert("", "end", iid=str(index), values=(category, ex.get("ocr_candidate", ""),
                        ex.get("confirmed_case_number", ""), ex.get("source_pdf", ""), ex.get("image", "")))
        bottom = ttk.Frame(outer); bottom.pack(fill="x", pady=(8, 0))
        def open_learning_folder():
            try:
                if os.name == "nt":
                    os.startfile(str(self.learning_dir))
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(self.learning_dir)])
                else:
                    subprocess.Popen(["xdg-open", str(self.learning_dir)])
            except Exception as exc:
                messagebox.showerror("Open learning folder", str(exc), parent=win)
        ttk.Button(bottom, text="Open Learning Folder", command=open_learning_folder).pack(side="left")
        ttk.Button(bottom, text="Close", command=win.destroy).pack(side="right")

    def _learning_accuracy(self):
        by_source = {}
        for index, example in enumerate(self.learning_data.get("examples", [])):
            if not example.get("ocr_candidate") or not example.get("confirmed_case_number"):
                continue
            source = example.get("source_pdf") or example.get("image") or f"legacy-{index}"
            by_source[source] = example
        examples = list(by_source.values())
        if not examples:
            return 0, 0
        exact = sum(
            1 for e in examples
            if e.get("ocr_candidate") == e.get("confirmed_case_number"))
        return exact, len(examples)

    def _refresh_learning_stats(self):
        if not hasattr(self, "learning_stats_label"):
            return
        exact, total = self._learning_accuracy()
        if total:
            pct = 100.0 * exact / total
            self.learning_stats_label.config(
                text=f"Verified exact-match rate: {pct:.1f}% ({exact}/{total})")
        else:
            self.learning_stats_label.config(
                text="Verified exact-match rate: no confirmed OCR predictions yet")
        self._refresh_auto_eligibility_status()
        self._refresh_learning_button_state()
        if hasattr(self, "learning_type_stats_label"):
            by_type = {kind: sum(1 for ex in self.learning_data.get("examples", [])
                                 if self._normalized_training_type(ex.get("training_type")) == kind
                                 and ex.get("image"))
                       for kind in ("handwritten", "printed", "unsure")}
            self.learning_type_stats_label.config(
                text=(f"Learning examples: handwritten {by_type['handwritten']} • "
                      f"printed {by_type['printed']} • unsure {by_type['unsure']}"))
        if hasattr(self, "model_status_label"):
            validation = self.learning_data.get("trocr_v2_validation", {})
            detail = ""
            if validation:
                detail = (
                    f" | held-out TrOCR exact match: "
                    f"{validation.get('accuracy_pct', 0):.1f}% "
                    f"({validation.get('correct', 0)}/{validation.get('total', 0)})")
            trained = self.learning_data.get("trocr_v2_trained_count", 0)
            self.model_status_label.config(
                text=f"TrOCR: {'fine-tuned' if trained else 'base model'}"
                     f" • {trained} training samples{detail}")

    def _get_auto_threshold(self):
        """Require >=95% for an isolated unambiguous case-field OCR read."""
        return 95.0

    def _save_learning_example(self, record, confirmed_number):
        confirmed_number=normalize_case_number(confirmed_number)
        if not CASE_RE.fullmatch(confirmed_number):
            raise ValueError("Learning accepts only 08- followed by 1–7 case digits.")
        if not self.learning_dir:
            raise OSError("The centralized learning share is unavailable; reconnect to the network before saving learning examples.")
        # Prefer the rectangle drawn by the reviewer; otherwise use the OCR rectangle.
        training_type = self._normalized_training_type(record.get("training_type", "unsure"))
        examples_dir = self.learning_dir / "examples_v3" / training_type
        examples_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        image_rel = None
        snippet = record.get("snippet")
        # Keep two rectangles conceptually separate: the full green field box
        # teaches the app where INCIDENT NO. / CASE NUMBER and the number live, while the
        # neural OCR crop should contain the number itself whenever possible.
        field_rect = record.get("manual_crop_rect") or (
            snippet[3] if snippet and record.get("scan_source") == "incident" else None)
        training_rect = None
        candidates = snippet[4] if snippet and len(snippet) > 4 and isinstance(snippet[4], list) else []
        selected_candidate = next((c for c in candidates
                                   if str(c.get("number") or "").strip() == str(confirmed_number)
                                   and c.get("rect")), None)
        if (record.get("manual_crop_rect") and field_rect and
                (record.get("manual_crop_origin")=="drawn" or not selected_candidate)):
            training_rect=tuple(field_rect)
        elif selected_candidate:
            training_rect=selected_candidate.get("rect")
        elif field_rect:
            # If the reviewer drew around the label plus the number, locate the
            # label inside the selected field and trim it from the character crop.
            # Keep field_rect intact below for spatial learning.
            training_rect = tuple(field_rect)
            if self.reader is not None and snippet and snippet[1] is not None:
                try:
                    page_array = snippet[1]
                    ph, pw = page_array.shape[:2]
                    # Learn from the literal INCIDENT NO. field wherever it
                    # appears, rather than restricting crops to the top-right.
                    field_labels = self._find_case_field_label_regions(page_array)
                    for lx0, ly0, lx1, ly1, _field_kind in sorted(
                            field_labels, key=lambda v: v[4] != "incident"):
                        abs_label = (lx0, ly0, lx1, ly1)
                        fx0, fy0, fx1, fy1 = [float(v) for v in field_rect]
                        intersects = (abs_label[0] < fx1 and abs_label[2] > fx0 and
                                      abs_label[1] < fy1 and abs_label[3] > fy0)
                        if intersects:
                            number_x0 = max(fx0, abs_label[2] + 2)
                            number_y0 = max(fy0, abs_label[1] - 10)
                            number_y1 = min(fy1, abs_label[3] + 15)
                            if fx1 - number_x0 >= 35 and number_y1 - number_y0 >= 10:
                                training_rect = (number_x0, number_y0, fx1, number_y1)
                                break
                except Exception as exc:
                    self._log("Could not trim case-field label from training crop: %s", exc)
        else:
            training_rect = snippet[3] if snippet else None
        crop_origin=("manual-field" if record.get("manual_crop_rect") and
                     (record.get("manual_crop_origin")=="drawn" or not selected_candidate)
                     else "candidate-number-box" if selected_candidate else "ocr")
        if snippet and snippet[1] is not None and training_rect:
            from PIL import Image
            image = Image.fromarray(snippet[1]).convert("RGB")
            x0, y0, x1, y1 = [int(v) for v in training_rect]
            # A modest border preserves the first/last digit strokes without
            # adding the unrelated case-field caption to the training target.
            pad_x = max(8, int((x1 - x0) * 0.04))
            pad_y = max(5, int((y1 - y0) * 0.12))
            x0 -= pad_x; x1 += pad_x; y0 -= pad_y; y1 += pad_y
            x0 = max(0, min(image.width, x0))
            x1 = max(0, min(image.width, x1))
            y0 = max(0, min(image.height, y0))
            y1 = max(0, min(image.height, y1))
            if x1 <= x0 or y1 <= y0:
                raise ValueError("The selected crop box is empty. Draw a box around the case number.")
            crop = image.crop((x0, y0, x1, y1))
            if crop.width < 35 or crop.height < 10:
                raise ValueError("The selected crop is too small. Draw a larger box around the case-field label (INCIDENT NO. or CASE NUMBER) and the complete case number.")
            # Avoid nearly full-page crops: they teach the line recognizer the wrong task.
            if crop.width >= image.width * 0.85 and crop.height >= image.height * 0.35:
                raise ValueError("The crop covers too much of the page. Draw a tighter box around the case number.")
            crop_path = examples_dir / f"{stamp}.png"
            crop.save(crop_path)
            image_rel = f"examples_v3/{training_type}/{stamp}.png"

        candidate = str(record.get("candidate") or "").strip()
        # Remember where the reviewer selected/drew the field inside the same
        # top-right 3 x 3 inch search window. These normalized zones guide future
        # OCR passes; the full top-right search remains as a safety net.
        if snippet and snippet[1] is not None and field_rect:
            page_h, page_w = snippet[1].shape[:2]
            search_w = min(page_w, int(round(3 * 72 * 2.2)))
            search_h = min(page_h, int(round(3 * 72 * 2.2)))
            search_x = page_w - search_w
            x0, y0, x1, y1 = [float(v) for v in field_rect]
            x0 = max(float(search_x), min(float(page_w), x0))
            x1 = max(float(search_x), min(float(page_w), x1))
            y0 = max(0.0, min(float(search_h), y0))
            y1 = max(0.0, min(float(search_h), y1))
            if x1 > x0 and y1 > y0 and search_w > 0 and search_h > 0:
                region = {
                    "rect_norm": [
                        round((x0 - search_x) / search_w, 5),
                        round(y0 / search_h, 5),
                        round((x1 - search_x) / search_w, 5),
                        round(y1 / search_h, 5)],
                    "confirmed_case_number": confirmed_number,
                    "source_pdf": record.get("path").name if record.get("path") else "",
                    "saved_at": datetime.now().isoformat(timespec="seconds")
                }
                with self.data_lock:
                    regions = self.learning_data.setdefault("field_regions", [])
                    regions.append(region)
                    # Bound the spatial memory file while preserving recent variety.
                    self.learning_data["field_regions"] = regions[-200:]

        example = {
            "image": image_rel,
            "crop_origin": crop_origin if image_rel else "none",
            "training_type": training_type,
            "ocr_candidate": candidate,
            "confirmed_case_number": confirmed_number,
            "source_pdf": record.get("path").name if record.get("path") else "",
            "ocr_score": float(record.get("score", 0.0) or 0.0),
            "engine_agreement": bool(record.get("engine_agreement", False)),
            "engines": record.get("engines", []),
            "confirmed_at": datetime.now().isoformat(timespec="seconds")
        }
        example["example_id"] = self._learning_example_id(example)
        with self.data_lock:
            self.learning_data.setdefault("examples", []).append(example)
            self._persist_learning_data(append_example=example)
            (self.folder / ".case_ocr_settings.json").write_text(
                json.dumps({"year": REQUIRED_CASE_YEAR}, indent=2), encoding="utf-8")
        self._diag_event("learning_saved",file=Path(record["path"]).name,
               confirmed_case_number=confirmed_number,
               crop_origin=crop_origin,training_type=training_type,
               image_saved=bool(image_rel))
        self._diag_sync_shared()
        self._refresh_learning_stats()
        self._refresh_learning_button_state()
        self._save_state()
        # Deliberately do not start training here. Training is deferred until the
        # user finishes the current import/review batch from the main window.

    def _auto_label_validation_examples(self, threshold, current_year):
        """Return distinct, manually verified reports in the existing calibration group."""
        validation_by_source = {}
        for example in self.learning_data.get("examples", []):
            source = example.get("source_pdf")
            if not (source and example.get("engine_agreement")
                    and float(example.get("ocr_score", 0.0) or 0.0) >= threshold
                    and example.get("ocr_candidate")
                    and example.get("confirmed_case_number")):
                continue
            if (not CASE_RE.fullmatch(str(example.get("confirmed_case_number") or ""))
                    or not CASE_RE.fullmatch(str(example.get("ocr_candidate") or ""))):
                continue
            validation_by_source[source] = example
        return list(validation_by_source.values())

    @staticmethod
    def _upper_error_bound(errors, total, confidence=0.95):
        """One-sided exact Clopper-Pearson upper confidence bound for error rate."""
        if total <= 0 or errors < 0 or errors >= total:
            return 1.0
        alpha = 1.0 - confidence
        if errors == 0:
            return 1.0 - alpha ** (1.0 / total)

        def binomial_cdf(k, n, p):
            # P(X <= k) for X~Binomial(n,p), evaluated recursively for stability.
            if p <= 0.0:
                return 1.0
            if p >= 1.0:
                return 0.0
            term = (1.0 - p) ** n
            terms = [term]
            for i in range(k):
                term *= ((n - i) / (i + 1.0)) * (p / (1.0 - p))
                terms.append(term)
            return min(1.0, math.fsum(terms))

        lo, hi = 0.0, 1.0
        for _ in range(70):
            mid = (lo + hi) / 2.0
            if binomial_cdf(errors, total, mid) > alpha:
                lo = mid
            else:
                hi = mid
        return hi

    def _auto_label_calibration_stats(self):
        try:
            threshold = self._get_auto_threshold()
            current_year = self.scan_year_prefix or self._year_prefix()
        except Exception:
            return 0, 0, 0.0, 1.0, None
        validation = self._auto_label_validation_examples(threshold, current_year)
        total = len(validation)
        correct = sum(
            1 for example in validation
            if example.get("ocr_candidate") == example.get("confirmed_case_number"))
        errors = total - correct
        pct = (100.0 * correct / total) if total else 0.0
        upper = self._upper_error_bound(errors, total, 0.95)
        return total, correct, pct, upper, threshold

    def _refresh_auto_eligibility_status(self):
        label = getattr(self, "auto_eligibility_label", None)
        if label is None:
            return
        threshold = self._get_auto_threshold()
        year = getattr(self, "scan_year_prefix", "") or (
            self.year_var.get().strip()[-2:] if hasattr(self, "year_var") else "08")
        if self.auto_label_var.get():
            message = (f"Auto-confirm is ON: valid case numbers at {threshold:.0f}%+ "
                       f"confidence for year {year or '08'} are automatically confirmed and queued for export.")
        else:
            message = (f"Auto-confirm is OFF. Turn it on to automatically confirm valid "
                       f"case numbers at {threshold:.0f}%+ confidence.")
        label.config(text=message)

    def _auto_label_block_reason(self, score, engine_agreement, candidate):
        """Require enabled auto-confirm, valid year, and >=95% OCR confidence."""
        if not self.auto_label_var.get():
            return "Auto-confirm is turned off."
        normalized = normalize_case_number(candidate)
        if not CASE_RE.fullmatch(normalized):
            return "Case number must start with 08- followed by 1–7 case digits."
        observed_year=normalized.split("-",1)[0]
        if observed_year!=REQUIRED_CASE_YEAR:
            return "Case number must start with the literal 08- prefix."
        try:
            observed_score = float(score or 0.0)
        except Exception:
            observed_score = 0.0
        threshold = self._get_auto_threshold()
        if observed_score < threshold:
            return f"OCR confidence {observed_score:.1f}% is below the {threshold:.0f}% threshold."
        return None

    def _auto_label_is_calibrated(self, score, engine_agreement, candidate, distinct_count=1):
        return self._auto_label_block_reason(score, engine_agreement, candidate) is None

    def _sequence_case_number(self, record):
        """Return an independent anchored OCR case for sequence comparison.

        95% labeled/header or 100% uniquely localized 3x3 fallback can anchor.
        Manual approvals can anchor. An inferred value cannot anchor.
        """
        if (not record or record.get("sequence_inferred") or
                record.get("sequence_excluded") or
                str(record.get("status") or "").startswith("Unverified") or
                record.get("scan_ambiguous") or record.get("sequence_conflict")):
            return None
        manually_verified=(bool(record.get("manual_approved")) and
                           not bool(record.get("auto_labeled")))
        case=normalize_case_number(
            record.get("confirmed") if manually_verified else record.get("candidate"))
        if not CASE_RE.fullmatch(case):
            return None
        if not case.startswith(REQUIRED_CASE_YEAR+"-"):
            return None
        if manually_verified:
            return case
        score=float(record.get("score") or 0)
        source=record.get("scan_source")
        if source=="fallback":
            snip=record.get("snippet")
            if (score<95.0 or not isinstance(snip,(tuple,list))
                    or len(snip)<4 or snip[3] is None
                    or not self._is_corner_case_rect(snip[3],snip[1])):
                return None
        elif source not in ("incident","case","header"):
            return None
        elif score<95.0:
            return None
        return case

    @staticmethod
    def _initial_review_reading(record, candidate_values, sequence_values, sequence_message):
        """Use the scan first; a two-sided suggestion may prefill a blank read."""
        existing = record.get("confirmed")
        if existing:
            return str(existing)
        candidate = record.get("candidate")
        if candidate:
            return str(candidate)
        return str(sequence_values[0]) if len(sequence_values) == 1 else ""

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
            if not path or str(rec.get("status") or "") in ("Not scanned","Scanning"):
                continue
            # An already-sent strong OCR read remains an independent anchor.
            case=self._sequence_case_number(rec)
            order.append((len(order),iid,case))
        for position,(_number,iid,_case) in enumerate(order):
            indexed[iid]=(order,position)
        return indexed

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

    def _sequence_hint_for_iid(self, iid):
        neighbors = self._sequence_neighbors(iid)
        if not neighbors:
            return [], ""
        left, right = neighbors
        if left[1] == right[1]:
            return [left[1]], (
                f"Both adjacent files read {left[1]} across consecutive original "
                "filenames. Sequence suggestion only; verify against this PDF.")
        return [], (
            f"Sequential filenames have different verified case numbers "
            f"({left[1]} / {right[1]}). Scan takes precedence; review this PDF manually.")

    def _apply_sequence_consensus(self):
        """Secondary before/after rule over contiguous *scan positions*.

        If two adjacent independent OCR anchors in sorted scan order match,
        approve every unverified middle item, even with a weak incorrect OCR
        case (e.g. 08-64 at 75.3% surrounded by 08-44 at 100%).
        Strong, contradictory reads, manual decisions and OCR conflicts are
        NEVER silently overwritten. Export occurs after the scan is complete.
        """
        if not self.auto_label_var.get():
            return 0
        order=[]
        for iid,_item in (getattr(self,"scan_items",[]) or []):
            record=self.records.get(iid)
            if not record:
                continue
            # Keep real sequence slots, even if a scan cannot be read; do not
            # require adjacent source filenames or ascending numeric suffixes.
            order.append((iid,record))
        anchors=[]
        for index,(iid,record) in enumerate(order):
            number=self._sequence_case_number(record)
            if number:
                anchors.append((index,iid,number))
        self._diag_event("sequence_anchor_snapshot",order=[
            {"index":pos,"file":Path(rec["path"]).name if rec.get("path") else iid,
             "candidate":rec.get("candidate"),"score":rec.get("score"),
             "source":rec.get("scan_source"),"anchor":(
                 next((case for pos2,aid,case in anchors if pos2==pos),None))}
            for pos,(iid,rec) in enumerate(order)])
        updated=0
        for previous,following in zip(anchors,anchors[1:]):
            left_pos,left_iid,left_case=previous
            right_pos,right_iid,right_case=following
            if right_pos<=left_pos+1:
                continue
            numeric=lambda val:tuple(map(int,str(val).split("-",1)))
            if numeric(left_case)!=numeric(right_case):
                self._diag_event("sequence_range_rejected",reason="different_anchor_cases",
                    first=left_case,last=right_case,
                    from_file=Path(self.records[left_iid]["path"]).name,
                    to_file=Path(self.records[right_iid]["path"]).name)
                continue
            consensus=left_case
            first_name=Path(self.records[left_iid]["path"]).name
            last_name=Path(self.records[right_iid]["path"]).name
            for _pos in range(left_pos+1,right_pos):
                iid,rec=order[_pos]
                path=rec.get("path")
                if (not path or not Path(path).is_file() or
                        rec.get("exported") or rec.get("sequence_excluded") or
                        rec.get("manual_approved") or
                        rec.get("scan_ambiguous") or rec.get("sequence_conflict") or
                        rec.get("status") in ("Not scanned","Scanning","Moved to BLANKS") or
                        str(rec.get("status") or "").startswith("Scan error:")):
                    continue
                original=normalize_case_number(rec.get("candidate"))
                score=float(rec.get("score") or 0)
                valid=bool(CASE_RE.fullmatch(original))
                other_year=bool(re.fullmatch(r"\d{1,2}-\d{1,7}",original))
                if (other_year and not valid and score>=95.0):
                    rec["status"]=(
                        f"{score:.1f}% OCR {original} lacks required 08- prefix; MANUAL REVIEW")
                    self._diag_event("sequence_range_rejected",
                        file=Path(path).name,reason="confident_non_08_year",
                        original=original,score=score)
                    self._insert_scan_row(iid,rec)
                    continue
                if (valid and score>=95.0 and
                    numeric(original)!=numeric(consensus)):
                    self._diag_event("sequence_range_rejected",
                        file=Path(path).name,reason="high_confidence_conflicting_middle",
                        original=original,score=score,expected=consensus,
                        first=first_name,last=last_name)
                    rec["status"]=(f"{score:.1f}% OCR {original} conflicts with "
                        f"matching range {consensus}; manual review required")
                    self._insert_scan_row(iid,rec)
                    continue
                block=self._auto_label_block_reason(100.0,False,consensus)
                if block is not None:
                    self._diag_event("sequence_range_rejected",
                        file=Path(path).name,reason=block)
                    continue
                rec["sequence_original_candidate"]=original
                rec["sequence_original_score"]=score
                rec["sequence_original_ambiguous"]=False
                rec["sequence_anchors"]=[left_iid,right_iid]
                rec["candidate"]=consensus
                rec["confirmed"]=consensus
                rec["sequence_inferred"]=True
                rec["sequence_excluded"]=False
                rec["sequence_conflict"]=False
                rec["scan_ambiguous"]=False
                rec["auto_labeled"]=True
                rec["manual_approved"]=True
                rec["status"]=(
                    f"Range-confirmed {consensus} between {first_name} and {last_name}"
                    + (f"; original OCR {original} at {score:.1f}%"
                       if original else "; original OCR unreadable"))
                self._diag_event("approval_decision",file=Path(path).name,
                    outcome="auto_approved",rule="matching_boundary_scan_range",
                    case=consensus,first=first_name,last=last_name,
                    original_candidate=original,original_score=score)
                self._move_to_completed(iid,batch=True)
                updated+=1
        if updated:
            self._refresh_review_controls()
            self._update_completed_count()
            self._save_state()
        self._diag_event("sequence_range_summary",anchors=len(anchors),
                         inferred=updated,scanned=len(order))
        self._schedule_auto_export()
        return updated

    def _configure_trocr_token_ids(self, model, processor):
        """
        VisionEncoderDecoderModel uses the OUTER composite config when shifting
        labels during training. Some Transformers releases/checkpoints do not
        expose pad_token_id on that config even though the tokenizer knows it.
        Copy the tokenizer's IDs onto the model and generation configs explicitly.
        """
        tokenizer = processor.tokenizer
        pad_token_id = getattr(tokenizer, "pad_token_id", None)
        if pad_token_id is None:
            raise RuntimeError(
                "TrOCR tokenizer has no pad_token_id. The handwriting model cannot be fine-tuned safely.")

        config = model.config
        config.pad_token_id = int(pad_token_id)

        eos_token_id = getattr(tokenizer, "eos_token_id", None)
        if eos_token_id is not None:
            config.eos_token_id = int(eos_token_id)

        decoder_start_token_id = getattr(config, "decoder_start_token_id", None)
        if decoder_start_token_id is None:
            for attr in ("bos_token_id", "cls_token_id", "eos_token_id"):
                candidate = getattr(tokenizer, attr, None)
                if candidate is not None:
                    decoder_start_token_id = candidate
                    break
        if decoder_start_token_id is None:
            decoder_config = getattr(config, "decoder", None)
            decoder_start_token_id = getattr(decoder_config, "bos_token_id", None)
        if decoder_start_token_id is None:
            raise RuntimeError(
                "TrOCR model has no decoder_start_token_id. Cannot prepare decoder inputs for training.")
        config.decoder_start_token_id = int(decoder_start_token_id)

        decoder_config = getattr(config, "decoder", None)
        if decoder_config is not None:
            decoder_config.pad_token_id = int(pad_token_id)
            if eos_token_id is not None:
                decoder_config.eos_token_id = int(eos_token_id)

        generation_config = getattr(model, "generation_config", None)
        if generation_config is not None:
            generation_config.pad_token_id = int(pad_token_id)
            generation_config.decoder_start_token_id = int(decoder_start_token_id)
            if eos_token_id is not None:
                generation_config.eos_token_id = int(eos_token_id)

        return int(pad_token_id), int(decoder_start_token_id)

    def _local_model_source(self):
        """Use only already-local checkpoints; no network discovery/copy."""
        roots=[]
        if self.learning_dir is not None:
            roots.extend([Path(self.learning_dir)/"trocr_v2_finetuned",
                          Path(self.learning_dir)/"trocr_v2_previous"])
        local_root=(Path(os.environ.get("LOCALAPPDATA") or
                    str(Path.home()/".local"/"share"))/"MFCaseOCR"/"models")
        if local_root.is_dir():
            old=sorted(
                [p for p in local_root.glob("trocr-*")
                 if (p/".mfcase_complete").is_file() and
                    (p/"config.json").is_file()],
                key=lambda p:p.stat().st_mtime,reverse=True)
            roots.extend(old)
            roots.append(local_root/"offline-base")
        for path in roots:
            if (path/"config.json").is_file():
                self._cached_model_dir=path
                return path
        return None


    def _prepare_offline_models(self):
        if self.busy or self.trocr_training or self.update_busy or self._offline_prepare_busy:
            messagebox.showinfo("Preparing offline models",
                "Finish scanning, training or updates before preparing models.")
            return
        if not messagebox.askyesno("Prepare offline models",
            "This action connects to the public model providers to DOWNLOAD "
            "the EasyOCR and TrOCR program models only. It never uploads "
            "PDFs, case numbers, learning images or logs. Model files may "
            "require over 1 GB of disk space. Continue?"):
            return
        self._offline_prepare_busy=True
        self.prepare_offline_btn.config(state="disabled",text="DOWNLOADING MODELS…")
        self.status.config(text="Downloading public OCR models by explicit request; "
                                "no case files are transmitted.")
        threading.Thread(target=self._prepare_offline_models_worker,
            daemon=True,name="MFCase-ExplicitOfflineModelDownload").start()

    def _prepare_offline_models_worker(self):
        try:
            import easyocr
            from transformers import TrOCRProcessor,VisionEncoderDecoderModel
            # Only PUBLIC model asset downloads, never content from PDFs.
            easyocr.Reader(["en"],gpu=False,download_enabled=True,verbose=False)
            name="microsoft/trocr-base-handwritten"
            processor=TrOCRProcessor.from_pretrained(name,local_files_only=False)
            model=VisionEncoderDecoderModel.from_pretrained(name,local_files_only=False)
            folder=(Path(os.environ.get("LOCALAPPDATA") or
                  str(Path.home()/".local"/"share"))/
                  "MFCaseOCR"/"models"/"offline-base")
            folder.mkdir(parents=True,exist_ok=True)
            # Save completely before marking the checkpoint available offline.
            model.save_pretrained(str(folder),safe_serialization=True)
            processor.save_pretrained(str(folder))
            (folder/".mfcase_complete").write_text("offline-base-v1",encoding="ascii")
            self.queue.put(("offline_models_ready",str(folder)))
        except Exception as exc:
            self.queue.put(("offline_models_error",str(exc)))

    def _preload_models_worker(self):
        try:
            if pymupdf is None or easyocr is None or np is None:
                raise RuntimeError("Required OCR packages are missing. Run SETUP.bat to repair them.")
            try:
                import torch
                gpu = bool(torch.cuda.is_available())
            except Exception:
                gpu = False
            try:
                reader = easyocr.Reader(["en"], gpu=gpu, verbose=False,
                                        download_enabled=False)
            except Exception:
                reader = easyocr.Reader(["en"], gpu=False, verbose=False,
                                        download_enabled=False)
                gpu = False
            self.reader = reader
            self.queue.put(("model_status", f"EasyOCR ready ({'GPU' if gpu else 'CPU'}); loading handwriting model…"))
            self._load_trocr()
            if self.trocr_model is None or self.trocr_processor is None:
                raise RuntimeError("Handwriting recognition model did not initialize")
            self.queue.put(("startup_models_ready",))
        except Exception as exc:
            logging.exception("Startup OCR preload failed")
            self.queue.put(("startup_models_error", str(exc)))

    def _begin_model_preload(self):
        if not self.learning_dir or self._startup_ready:
            return
        self._startup_notice("Loading OCR engines and trained recognition model before scanning…")
        self._loader_start("preload", "Loading recognition models before enabling the application…")
        threading.Thread(target=self._preload_models_worker, daemon=True,
                         name="MFCase-PreloadModels").start()

    def _complete_startup(self):
        self._startup_ready = True
        self._startup_error = None
        self._loader_finish("preload")
        self._close_startup_notice()
        self.choose_folder_btn.config(state="normal")
        self.refresh_root_btn.config(state="normal" if self.folder else "disabled")
        self._refresh_scan_button()
        self.status.config(text="OFFLINE READY — local OCR/learning loaded. "
                                "Click UPDATE or UPDATE LEARNING manually when desired.")

    def _fail_startup(self, reason):
        self._startup_ready = False
        self._startup_error = reason
        self._loader_finish("preload")
        self.choose_folder_btn.config(state="disabled")
        self.refresh_root_btn.config(state="disabled")
        self.scan_btn.config(state="disabled")
        self._startup_notice("Startup blocked: " + reason + "  Check network/setup, then click RETRY STARTUP.")
        if getattr(self, "_startup_retry_btn", None) is not None:
            self._startup_retry_btn.config(state="normal")
        self.status.config(text="OFFLINE MODELS NOT READY — "+reason+
                            ". Click PREPARE OFFLINE MODELS once while online.")
        self._log("Startup blocked: %s", reason)

    def _load_trocr(self):
        if self.trocr_model is not None:
            return
        self.queue.put(("model_load_start", "Loading handwriting recognition and learned model…"))
        try:
            import torch
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    
            self.torch_device = "cuda" if torch.cuda.is_available() else "cpu"
            model_dir = self._local_model_source()
            if model_dir and (model_dir / "config.json").exists():
                model_source = str(model_dir)
                processor = TrOCRProcessor.from_pretrained(model_source, local_files_only=True)
                model = VisionEncoderDecoderModel.from_pretrained(model_source, local_files_only=True)
                model_status = "fine-tuned local model"
            else:
                model_source = "microsoft/trocr-base-handwritten"
                processor = TrOCRProcessor.from_pretrained(
                    model_source,local_files_only=True)
                model = VisionEncoderDecoderModel.from_pretrained(
                    model_source,local_files_only=True)
                model_status = "Microsoft base handwriting model"
            model.to(self.torch_device)
            self._configure_trocr_token_ids(model, processor)
            model.eval()
            self.trocr_processor = processor
            self.trocr_model = model
            self.trocr_error = ""
            self._log("Loaded %s on %s", model_status, self.torch_device)
            try:
                self.queue.put(("model_status", f"TrOCR: {model_status} • {self.torch_device.upper()}"))
            except Exception:
                pass
        finally:
            self.queue.put(("model_load_done",))

    def _trocr_read(self, image):
        return self._trocr_read_scored(image)[0]

    def _trocr_read_scored(self, image):
        if self.trocr_error:
            return "", 0.0
        try:
            from PIL import Image
            import torch
            self._load_trocr()
            pil = Image.fromarray(image).convert("RGB") if isinstance(image, np.ndarray) else image.convert("RGB")
            pixels = self.trocr_processor(images=pil, return_tensors="pt").pixel_values.to(self.torch_device)
            with torch.inference_mode():
                output = self.trocr_model.generate(
                    pixels, max_new_tokens=24, num_beams=2,
                    early_stopping=True, return_dict_in_generate=True, output_scores=True)
            text = self.trocr_processor.batch_decode(
                output.sequences, skip_special_tokens=True)[0].strip()
            transitions = self.trocr_model.compute_transition_scores(
                output.sequences, output.scores,
                beam_indices=getattr(output, "beam_indices", None),
                normalize_logits=True)
            tokens = output.sequences[:, -transitions.shape[1]:]
            special = self.trocr_processor.tokenizer.all_special_ids
            valid = torch.ones_like(tokens, dtype=torch.bool)
            for token_id in special:
                valid &= tokens != token_id
            scores = transitions[valid]
            confidence = float(scores.mean().exp().item() * 100.0) if scores.numel() else 0.0
            return text, confidence
        except Exception as exc:
            if not self.trocr_error:
                self.trocr_error = str(exc)
                self._log("TrOCR unavailable: %s", exc)
                try:
                    self.queue.put(("engine_warning", f"TrOCR unavailable; EasyOCR will continue. Details: {exc}"))
                except Exception:
                    pass
            return "", 0.0

    def _open_training_progress(self):
        if self.training_popup is not None:
            try:
                if self.training_popup.winfo_exists():
                    self.training_popup.lift()
                    return
            except Exception:
                pass
        popup = tk.Toplevel(self.root)
        popup.title("Update Learning")
        popup.geometry("560x220")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.protocol("WM_DELETE_WINDOW", self._training_popup_close_requested)
        self.training_popup = popup
        frame = ttk.Frame(popup, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Updating learning", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.training_stage_label = ttk.Label(frame, text="Starting...", wraplength=510)
        self.training_stage_label.pack(anchor="w", pady=(12, 8))
        self.training_progressbar = ttk.Progressbar(
            frame, mode="determinate", maximum=100, value=0)
        self.training_progressbar.pack(fill="x", pady=(0, 5))
        self.training_percent_label = ttk.Label(
            frame, text="0%", font=("Segoe UI", 10, "bold"))
        self.training_percent_label.pack(anchor="e")
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(8, 0))
        self.training_pause_button = ttk.Button(
            bottom, text="PAUSE", command=self._toggle_learning_pause)
        self.training_pause_button.pack(side="left")
        self.training_stop_button = ttk.Button(
            bottom, text="STOP", command=self._stop_learning_update)
        self.training_stop_button.pack(side="left", padx=(8, 0))
        self.training_close_button = ttk.Button(
            bottom, text="CLOSE", state="disabled", command=self._close_training_popup)
        self.training_close_button.pack(side="right")
        popup.update_idletasks()

    def _toggle_learning_pause(self):
        if not self.trocr_training:
            return
        if self.learning_pause_event.is_set():
            self.learning_pause_event.clear()
            if self.training_pause_button:
                self.training_pause_button.config(text="RESUME")
            if self.training_stage_label:
                self.training_stage_label.config(text="Learning paused.")
        else:
            self.learning_pause_event.set()
            if self.training_pause_button:
                self.training_pause_button.config(text="PAUSE")
            if self.training_stage_label:
                self.training_stage_label.config(text="Resuming learning...")

    def _stop_learning_update(self):
        if not self.trocr_training:
            return
        self.learning_stop_event.set()
        self.learning_pause_event.set()
        if self.training_stop_button:
            self.training_stop_button.config(text="STOPPING...", state="disabled")
        if self.training_pause_button:
            self.training_pause_button.config(state="disabled")
        if self.training_stage_label:
            self.training_stage_label.config(text="Stopping after the current step. Saved corrections will be kept.")

    def _wait_for_learning_control(self):
        while not self.learning_pause_event.wait(timeout=0.2):
            if self.learning_stop_event.is_set():
                raise InterruptedError("Learning update stopped by user.")
        if self.learning_stop_event.is_set():
            raise InterruptedError("Learning update stopped by user.")

    def _training_popup_close_requested(self):
        if not self.trocr_training:
            self._close_training_popup()

    def _close_training_popup(self):
        popup = self.training_popup
        self.training_popup = None
        self.training_progressbar = None
        self.training_percent_label = None
        self.training_stage_label = None
        self.training_close_button = None
        self.training_pause_button = None
        self.training_stop_button = None
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass

    def _update_training_progress(self, percent, stage, finished=False):
        if finished:
            self._diag_event("learning_update_finished",
                             percent=percent,stage=stage)
            threading.Thread(target=self._diag_sync_shared,
                             daemon=True).start()
        percent = max(0, min(100, int(round(percent))))
        popup = self.training_popup
        if popup is None:
            return
        try:
            if not popup.winfo_exists():
                return
            self.training_progressbar["value"] = percent
            self.training_percent_label.config(text=f"{percent}%")
            self.training_stage_label.config(text=stage)
            if finished:
                if self.training_pause_button:
                    self.training_pause_button.config(state="disabled")
                if self.training_stop_button:
                    self.training_stop_button.config(state="disabled")
                self.training_close_button.config(state="normal")
                popup.protocol("WM_DELETE_WINDOW", self._close_training_popup)
        except Exception:
            pass

    def _maybe_train_trocr(self, automatic=False):
        if self.trocr_training or not self.learning_dir:
            return False
        usable = [
            dict(ex) for ex in self.learning_data.get("examples", [])
            if ex.get("image") and CASE_RE.fullmatch(
                str(ex.get("confirmed_case_number") or ""))
        ]
        attempted_ids = set(self.learning_data.get("trocr_v3_attempted_ids", []))
        pending = []
        previously_used = []
        for ex in usable:
            ex["example_id"] = self._learning_example_id(ex)
            if not ex["example_id"]:
                continue
            (pending if ex["example_id"] not in attempted_ids else previously_used).append(ex)

        first_update = not attempted_ids
        minimum_new = 10 if first_update else 5
        if len(pending) < minimum_new:
            return False

        # Process ALL pending corrections in one update. The training worker
        # loads individual crops only when needed, avoiding image-memory spikes.
        selected_new = pending
        replay = self._even_sample(previously_used, 16)
        examples = selected_new + replay
        if len(examples) < 10:
            return False
        new_ids = [ex["example_id"] for ex in selected_new]

        self.trocr_training = True
        self._learning_auto_run = bool(automatic)
        self.learning_pause_event.set()
        self.learning_stop_event.clear()
        if hasattr(self, "status"):
            self.status.config(
                text=f"Updating ALL {len(selected_new)} new examples and {len(replay)} reference examples.")
        self.scan_btn.config(state="disabled")
        self.finish_batch_btn.config(state="disabled")
        if not automatic:
            self._open_training_progress()
        self._update_training_progress(0, "Preparing the new examples...")
        training_dir = Path(self.learning_dir)
        threading.Thread(
            target=self._train_trocr_worker,
            args=(training_dir, examples, new_ids),
            daemon=True).start()
        return True

    def _finish_import_batch(self):
        if self.busy or self.trocr_training:
            messagebox.showinfo(
                "Processing in progress",
                "Finish or stop the scan before updating learning.")
            return
        if not self.learning_dir:
            messagebox.showinfo("No folder selected", "Choose the PDF folder first.")
            return
        pending = self._pending_learning_examples()
        attempted = set(self.learning_data.get("trocr_v3_attempted_ids", []))
        required = 5 if attempted else 10
        if len(pending) < required:
            messagebox.showinfo(
                "More confirmed examples needed",
                f"{len(pending)} new confirmed example(s) are ready. "
                f"At least {required} are needed for the next learning update. "
                "Your corrections are already saved.")
            return
        if not self._maybe_train_trocr():
            messagebox.showinfo(
                "Learning update not started",
                "There are not enough usable new examples yet. Your corrections are saved.")
            return

    def _normalize_case_text(self, value):
        value = str(value or "").replace("—", "-").replace("–", "-")
        value = re.sub(r"\s+", "", value)
        return value.upper()

    def _train_trocr_worker(self, learning_dir, examples, new_ids):
        candidate_dir = None

        def report(percent, stage):
            # Worker thread communicates with Tk only through the thread-safe queue.
            try:
                self.queue.put(("training_progress", percent, stage))
            except Exception:
                pass

        try:
            import torch
            from PIL import Image
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            if self._learning_auto_run:
                # Auto-training runs in the worker thread with restrained CPU
                # parallelism; avoid saturating a patrol workstation at startup.
                try:
                    torch.set_num_threads(min(2, max(1, (os.cpu_count() or 2) // 4)))
                except RuntimeError:
                    pass
                if os.name == "nt":
                    try:
                        import ctypes
                        kernel = ctypes.windll.kernel32
                        kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)
                    except Exception:
                        pass

            report(2, "Preparing and checking the saved training crops...")
            usable = []
            total_examples = max(1, len(examples))
            for index, ex in enumerate(examples, 1):
                self._wait_for_learning_control()
                image_path = learning_dir / ex["image"]
                if image_path.is_file():
                    try:
                        # Verify now, but don't retain the decompressed pixels.
                        # Later, each training step opens only its own crop.
                        with Image.open(image_path) as im:
                            im.verify()
                        usable.append((image_path, ex["confirmed_case_number"]))
                    except Exception:
                        self._log("Could not load training crop %s", image_path)
                if index == total_examples or index % 5 == 0:
                    report(
                        2 + 5 * index / total_examples,
                        f"Checking training crops: {index}/{total_examples}")
            if len(usable) < 10:
                raise RuntimeError(
                    f"Only {len(usable)} usable case-number crops; at least 10 are required.")

            # Deterministic held-out split: every fifth item is validation-only for this run.
            train_items = [item for idx, item in enumerate(usable) if idx % 5 != 0]
            validation_items = [item for idx, item in enumerate(usable) if idx % 5 == 0]
            if len(validation_items) < 2 or len(train_items) < 8:
                raise RuntimeError("Not enough examples for a meaningful train/validation split.")

            device = "cuda" if torch.cuda.is_available() else "cpu"
            base_name = "microsoft/trocr-base-handwritten"
            active_dir = learning_dir / "trocr_v2_finetuned"
            recovery_dir = learning_dir / "trocr_v2_previous"
            baseline_dir = active_dir if (active_dir / "config.json").exists() else (
                recovery_dir if (recovery_dir / "config.json").exists() else None)

            report(
                10,
                "Loading TrOCR model and tokenizer. The first load may take longer while model files are downloaded...")
            if baseline_dir is not None:
                processor = TrOCRProcessor.from_pretrained(str(baseline_dir), local_files_only=True)
                base_model = VisionEncoderDecoderModel.from_pretrained(
                    str(baseline_dir), local_files_only=True).to(device)
                baseline_source = (
                    "current fine-tuned model" if baseline_dir == active_dir
                    else "previous recovery model")
            else:
                local_model=self._local_model_source()
                source=str(local_model) if local_model else base_name
                processor = TrOCRProcessor.from_pretrained(
                    source,local_files_only=True)
                base_model = VisionEncoderDecoderModel.from_pretrained(
                    source,local_files_only=True).to(device)
                baseline_source = "Microsoft base model"

            pad_token_id, decoder_start_token_id = self._configure_trocr_token_ids(
                base_model, processor)
            base_model.eval()
            report(22, f"Model loaded on {device.upper()}. Testing the current model...")

            def evaluate(model, start_percent, end_percent, stage_name):
                model.eval()
                correct = 0
                total_validation = len(validation_items)
                for index, (image_path, target) in enumerate(validation_items, 1):
                    self._wait_for_learning_control()
                    with Image.open(image_path) as image:
                        pixels = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
                    with torch.inference_mode():
                        output = model.generate(pixels, max_new_tokens=24, num_beams=2)
                    predicted = processor.batch_decode(output, skip_special_tokens=True)[0]
                    if self._normalize_case_text(predicted) == self._normalize_case_text(target):
                        correct += 1
                    progress = start_percent + (
                        (end_percent - start_percent) * index / total_validation)
                    report(
                        progress,
                        f"{stage_name}: example {index}/{total_validation} "
                        f"({correct} exact matches so far)")
                return correct

            baseline_correct = evaluate(
                base_model, 24, 34, "Testing existing model")

            model = base_model
            optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5)
            best_correct = baseline_correct
            best_state = None
            best_epoch = 0
            epochs_completed = 0
            report(
                36,
                f"Fine-tuning TrOCR with all {len(train_items)} training examples per pass; "
                f"validation is checked after each of up to 2 epochs on {device.upper()}...")

            # Keep a CPU copy of the best checkpoint seen so far. This lets us
            # discard a later epoch if it harms validation performance.
            for epoch in range(2):
                self._wait_for_learning_control()
                model.train()
                train_progress_start, train_progress_end = ((36, 52) if epoch == 0 else (65, 81))
                for example_index, (image_path, label) in enumerate(train_items, 1):
                    self._wait_for_learning_control()
                    with Image.open(image_path) as image:
                        pixels = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
                    labels = processor.tokenizer(
                        label, return_tensors="pt", padding="max_length",
                        max_length=20, truncation=True).input_ids.to(device)
                    labels[labels == pad_token_id] = -100
                    loss = model(pixel_values=pixels, labels=labels).loss
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                    optimizer.zero_grad(set_to_none=True)
                    last_loss = float(loss.detach().cpu().item())
                    progress = train_progress_start + (
                        (train_progress_end - train_progress_start) *
                        example_index / len(train_items))
                    report(
                        progress,
                        f"Fine-tuning epoch {epoch + 1}/2 • "
                        f"example {example_index}/{len(train_items)} • loss {last_loss:.4f}")

                epochs_completed = epoch + 1
                eval_start, eval_end = ((55, 65) if epoch == 0 else (78, 90))
                epoch_correct = evaluate(
                    model, eval_start, eval_end,
                    f"Validation after epoch {epoch + 1}/2")

                if epoch_correct > best_correct:
                    best_correct = epoch_correct
                    best_epoch = epoch + 1
                    best_state = {
                        name: tensor.detach().cpu().clone()
                        for name, tensor in model.state_dict().items()
                    }
                    report(
                        eval_end,
                        f"Epoch {epoch + 1} improved validation ({best_correct}/"
                        f"{len(validation_items)}). Keeping this checkpoint as best so far...")
                else:
                    if best_state is not None:
                        model.load_state_dict(best_state)
                    report(
                        eval_end,
                        f"Epoch {epoch + 1} did not improve validation. "
                        f"Stopping and retaining the best checkpoint...")
                    break

            if best_state is not None:
                model.load_state_dict(best_state)
            tuned_correct = best_correct

            baseline_accuracy = 100.0 * baseline_correct / len(validation_items)
            tuned_accuracy = 100.0 * tuned_correct / len(validation_items)
            improved = tuned_correct > baseline_correct
            validation_report = {
                "baseline_accuracy_pct": baseline_accuracy,
                "accuracy_pct": tuned_accuracy,
                "correct": tuned_correct,
                "total": len(validation_items),
                "training_examples": len(train_items),
                "epochs_completed": epochs_completed,
                "best_epoch": best_epoch,
                "early_stopped": epochs_completed < 2,
                "evaluated_at": datetime.now().isoformat(timespec="seconds"),
                "saved_as_active": improved,
                "baseline_source": baseline_source
            }

            self._wait_for_learning_control()
            if improved:
                report(94, "Validation improved. Saving and activating the updated model...")
                candidate_dir = learning_dir / f"trocr_candidate_{int(time.time())}"
                candidate_dir.mkdir(parents=True, exist_ok=False)
                model.eval()
                model.save_pretrained(str(candidate_dir))
                processor.save_pretrained(str(candidate_dir))
                previous_dir = learning_dir / "trocr_v2_previous"
                if active_dir.exists():
                    if previous_dir.exists():
                        shutil.rmtree(previous_dir)
                    os.replace(active_dir, previous_dir)
                os.replace(candidate_dir, active_dir)
                candidate_dir = None
                active_text = "New TrOCR model passed the held-out exact-match comparison and was activated."
            else:
                report(94, "Validation did not improve. Keeping the existing model...")
                active_text = (
                    "TrOCR training did not improve held-out exact matches; the existing model was kept.")

            db = learning_dir / "learning_examples_v3.json"
            with self._shared_learning_write_lock():
                latest = json.loads(db.read_text(encoding="utf-8")) if db.exists() else {}
                latest["trocr_v2_validation"] = validation_report
                latest["last_training_summary"] = {
                    "examples_total": len(usable),
                    "new_examples": len(new_ids),
                    "reference_examples": max(0, len(examples) - len(new_ids)),
                    "examples_handwritten": sum(1 for ex in examples if self._normalized_training_type(ex.get("training_type")) == "handwritten"),
                    "examples_printed": sum(1 for ex in examples if self._normalized_training_type(ex.get("training_type")) == "printed"),
                    "examples_unsure": sum(1 for ex in examples if self._normalized_training_type(ex.get("training_type")) == "unsure"),
                    "updated_at": validation_report.get("evaluated_at"),
                    "model_activated": bool(improved)
                }
                attempted_ids = set(latest.get("trocr_v3_attempted_ids", []))
                attempted_ids.update(new_ids)
                latest["trocr_v3_attempted_ids"] = sorted(attempted_ids)
                latest["trocr_v2_attempted_count"] = len(attempted_ids)
                if improved:
                    trained_ids = set(latest.get("trocr_v3_trained_ids", []))
                    trained_ids.update(new_ids)
                    latest["trocr_v3_trained_ids"] = sorted(trained_ids)
                    latest["trocr_v2_trained_count"] = len(trained_ids)
                if db.is_file():
                    shutil.copy2(db, db.with_name("learning_examples_v3.last_good.json"))
                temp = db.with_name(db.name + f".{os.getpid()}.tmp")
                temp.write_text(json.dumps(latest, indent=2), encoding="utf-8")
                os.replace(temp, db)

            final_message = (
                f"{active_text} Held-out exact match: {tuned_correct}/{len(validation_items)} "
                f"({tuned_accuracy:.1f}%), baseline {baseline_correct}/{len(validation_items)}. "
                f"Training passes completed: {epochs_completed}/2; best pass: {best_epoch or 'none'}.")
            report(100, final_message)
            self.queue.put(("learning_status", final_message))
        except InterruptedError as exc:
            self._log("TrOCR learning update stopped: %s", exc)
            self.queue.put(("learning_stopped", "Learning update stopped. All saved corrections were kept; no incomplete model was activated."))
        except Exception as exc:
            self._log("TrOCR training/validation failed: %s", exc)
            error_message = f"TrOCR update failed; existing model retained: {exc}"
            report(100, error_message)
            self.queue.put(("learning_status", error_message))
        finally:
            if candidate_dir and candidate_dir.exists():
                shutil.rmtree(candidate_dir, ignore_errors=True)

    def scan(self, only_iids=None):
        if not self._startup_ready or not self.learning_dir or self.reader is None or self.trocr_model is None:
            self.status.config(text="Scanning is locked until shared learning and all OCR models are ready.")
            return
        if self.busy or self.trocr_training or self.export_busy or not self.records:
            return
        self.scan_year_prefix = self._year_prefix()
        if pymupdf is None or easyocr is None or np is None:
            messagebox.showerror(
                "Setup required",
                "Run SETUP.bat first. Python 3.11, PyMuPDF, EasyOCR and NumPy must be installed.")
            return
        try:
            settings_path = self.folder / ".case_ocr_settings.json"
            settings_path.write_text(
                json.dumps({"year": REQUIRED_CASE_YEAR}, indent=2),
                encoding="utf-8")
        except Exception:
            self._log("Could not save case-year setting")
        self.scan_items = [
            (iid, rec) for iid, rec in self.records.items()
            if iid in self.scan_checked_iids
            and (only_iids is None or iid in only_iids)
            and self.tree.exists(iid)
            and not rec.get("confirmed")
            and not rec.get("exported")
            and rec.get("status") != "Moved to BLANKS"
            and (rec.get("status") == "Not scanned"
                 or str(rec.get("status", "")).startswith("Scan error:"))
            and Path(rec["path"]).is_file()
            and Path(rec["path"]).parent.resolve() == self.folder.resolve()
        ]
        self.scan_items.sort(key=lambda pair: self._natural_pdf_sort_key(pair[1]["path"]))
        if not self.scan_items:
            self.status.config(text="No checked, unscanned files to scan. Select files or click REFRESH.")
            self.restart_btn.config(text="RESTART", state="normal")
            self._refresh_scan_button()
            return
        self._run_outcomes = {"total_selected": len(self.scan_items)}
        self.busy = True
        self.paused = False
        self.stop_event.clear()
        self.pause_event.set()
        self.pause_btn.config(text="PAUSE", state="normal")
        self.restart_btn.config(text="STOP", state="normal")
        self.scan_btn.config(state="disabled")
        self._refresh_rescan_button()
        self._refresh_review_controls()
        self._update_completed_count()
        self._scan_progress_update(0,"SCANNING")
        self.status.config(text=f"Scanning {len(self.scan_items)} file(s)...")
        self._diag_start_run()
        self._diag_event("sequence_order",mode="natural_filename_scan_queue",
             ordered_files=[Path(rec["path"]).name for iid,rec in self.scan_items],
             minimum_independent_matches=3,
             filenames_need_consecutive_numbers=False)
        self._log("Scan started in %s with year filter %s", self.folder, self.scan_year_prefix or "ALL")
        if self.reader is None:
            self._loader_start("ocr", "Loading OCR engines…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            try:
                import torch
                gpu_available = bool(torch.cuda.is_available())
            except Exception:
                gpu_available = False
            reader_gpu = gpu_available
            if self.reader is None:
                try:
                    self.reader = easyocr.Reader(
                ["en"],gpu=reader_gpu,verbose=False,download_enabled=False)
                except Exception:
                    self._log("EasyOCR GPU initialization failed; retrying on CPU")
                    reader_gpu = False
                    self.reader = easyocr.Reader(["en"], gpu=False, verbose=False,
                                                  download_enabled=False)
            self.queue.put(("engine_status", f"EasyOCR initialized • {'GPU' if reader_gpu else 'CPU'}"))
            records_snapshot = list(self.scan_items)
            total = len(records_snapshot)
            processed = 0
            stopped = False
            for idx, (iid, rec) in enumerate(records_snapshot, 1):
                if self.stop_event.is_set():
                    stopped = True
                    break
                while not self.pause_event.wait(timeout=0.2):
                    if self.stop_event.is_set():
                        break
                if self.stop_event.is_set():
                    stopped = True
                    break
                source_path = Path(rec["path"])
                processed = idx
                try:
                    if not source_path.is_file():
                        raise FileNotFoundError(f"Source PDF no longer exists: {source_path.name}")
                    if self._pdf_is_blank(source_path):
                        self._diag_event("blank_kept_in_root",
                            file=source_path.name,
                            reason="explicit_user_send_required")
                        self.queue.put((
                            "row", iid, "", None,
                            "Blank PDF — retained in root for manual review",
                            False, 0.0, [], False, idx))
                        continue
                    self._diag_current_file=source_path.name
                    result = self._ocr_pdf(source_path)
                    candidate, snippet, note, handwritten, score, engines, agreement = result
                    self.queue.put((
                        "row", iid, candidate, snippet, note, handwritten,
                        score, engines, agreement, idx))
                except Exception as exc:
                    self._diag_event("scan_error",file=source_path.name,error=repr(exc))
                    self._log("Scan failed for %s: %s", source_path, exc)
                    self.queue.put((
                        "row", iid, "", None, f"Scan error: {exc}",
                        False, 0.0, [], False, idx))
            if self.stop_event.is_set():
                stopped = True
            self.queue.put(("stopped", processed) if stopped else ("done",))
        except Exception as exc:
            self._log("Fatal scan initialization error: %s", exc)
            self.queue.put(("fatal", str(exc)))

    def toggle_pause(self):
        if not self.busy:
            return
        if self.paused:
            self.paused = False
            self.pause_event.set()
            self.pause_btn.config(text="PAUSE")
            self.status.config(text="Scanning...")
        else:
            self.paused = True
            self.pause_event.clear()
            self.pause_btn.config(text="RESUME")
            self.status.config(text="Scan paused.")

    def stop_or_restart_scan(self):
        if self.busy:
            self.stop_event.set()
            self.pause_event.set()
            self.paused = False
            self.pause_btn.config(state="disabled", text="PAUSE")
            self.restart_btn.config(text="STOPPING...", state="disabled")
            self.status.config(text="Stopping after the current file...")
            return
        self.scan()

    def restart_scan(self):
        if self.busy or self.trocr_training:
            messagebox.showinfo("Processing in progress", "Finish the current scan or model update before restarting.")
            return
        if not self.folder:
            return
        if not messagebox.askyesno(
            "Restart scan",
            "Clear detected candidates and user confirmations for PDFs still in the root folder, then scan them again?"):
            return

        # Re-enumerate the root folder so PDFs already moved to BLANKS are not resurrected.
        previous_records = dict(self.records)
        for tree in (self.tree, self.completed_tree):
            for item in tree.get_children():
                tree.delete(item)
        for iid in list(self.completed_edit_widgets):
            self._close_completed_edit(iid)
        self.completed_checked_iids.clear()
        self.completed_edit_drafts.clear()
        self.completed_photo_refs.clear()
        self.scan_checked_iids.clear()
        self.records = {}
        pdfs = sorted(
            (p for p in self.folder.iterdir()
             if p.is_file() and p.suffix.lower() == ".pdf"),
            key=self._natural_pdf_sort_key)
        for pdf in pdfs:
            iid = f"pdf::{pdf.name}"
            previous = previous_records.get(iid, {})
            if previous.get("exported"):
                previous["path"] = pdf
                previous["snippet"] = None
                previous["completed_iid"] = None
                self.records[iid] = previous
                continue
            self.records[iid] = {
                "path": pdf, "candidate": "", "confirmed": "", "status": "Not scanned",
                "snippet": None, "handwritten": False, "score": 0.0,
                "engines": [], "engine_agreement": False, "auto_labeled": False,
                "manual_approved": False, "completed_iid": None, "exported": False,
                "export_name": ""
            }
            self.records[iid]["scan_selected"] = bool(previous.get("scan_selected", True))
            if self.records[iid]["scan_selected"]:
                self.scan_checked_iids.add(iid)
            else:
                self.scan_checked_iids.discard(iid)
            self._insert_scan_row(iid, self.records[iid])
        self._save_state()
        self._update_completed_count()
        self._refresh_scan_button()
        self._update_scan_file_count()
        self._refresh_review_controls()
        self.finish_batch_btn.config(state="disabled")
        self.scan()

    def _pdf_is_blank(self, path):
        """
        Check the single report page before OCR. If a PDF unexpectedly has
        multiple pages, do not classify/move it automatically as blank.
        """
        doc = pymupdf.open(path)
        try:
            if len(doc) != 1:
                return False
            page = doc[0]
            if page.get_text("text").strip():
                return False
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(1.0, 1.0),
                colorspace=pymupdf.csGRAY, alpha=False)
            gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width)
            h, w = gray.shape
            y0, y1 = int(h * 0.04), max(int(h * 0.96), 1)
            x0, x1 = int(w * 0.04), max(int(w * 0.96), 1)
            content = gray[y0:y1, x0:x1]
            dark_fraction = float(np.count_nonzero(content < 245)) / float(content.size)
            return dark_fraction <= 0.00002
        finally:
            doc.close()

    def _get_learned_field_regions(self, width, height, limit=3):
        """Return a few distinct reviewer-confirmed field locations."""
        recent = self.learning_data.get("field_regions", [])[-200:]
        selected = []
        centers = []
        for item in reversed(recent):
            rect = item.get("rect_norm") or []
            if len(rect) != 4:
                continue
            try:
                x0, y0, x1, y1 = [float(v) for v in rect]
            except (TypeError, ValueError):
                continue
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                continue
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            # Keep the most recent examples that represent distinct field zones.
            if any(((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 < 0.09
                   for px, py in centers):
                continue
            selected.append((int(x0 * width), int(y0 * height),
                             min(width, int(x1 * width)),
                             min(height, int(y1 * height))))
            centers.append((cx, cy))
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _merge_case_hits(primary, additional, learned=False):
        """Merge OCR passes without losing each recognized value's best box."""
        merged = {item["number"]: dict(item) for item in primary}
        for hit in additional:
            number = hit.get("number")
            if not number:
                continue
            candidate = dict(hit)
            if learned:
                candidate["learned_location"] = True
            if number not in merged:
                merged[number] = candidate
                continue
            current = merged[number]
            current_engines = list(current.get("engines") or [])
            had_easyocr_box = any(e.startswith("EasyOCR") for e in current_engines)
            candidate_has_easyocr = any(
                e.startswith("EasyOCR") for e in (candidate.get("engines") or []))
            for engine in candidate.get("engines") or []:
                if engine not in current_engines:
                    current_engines.append(engine)
            current["engines"] = current_engines
            current["score"] = max(float(current.get("score") or 0),
                                   float(candidate.get("score") or 0))
            if candidate.get("label_type"):
                priority = {"incident": 4, "case": 4, "report": 2, "sys": 1}
                old = current.get("label_type")
                if old is None or priority[candidate["label_type"]] > priority[old]:
                    current["label_type"] = candidate["label_type"]
                    current["label_distance"] = candidate.get("label_distance", 0.0)
            if candidate.get("learned_location"):
                current["learned_location"] = True
            # Prefer a specific OCR detection box over a wide TrOCR region box.
            if candidate.get("rect") and (
                not current.get("rect") or
                (candidate_has_easyocr and not had_easyocr_box)):
                current["rect"] = candidate["rect"]
        return list(merged.values())

    def _find_case_field_label_regions(self, image):
        """Detect INCIDENT NO. without case sensitivity, including split labels.

        Coordinates end at the printed label, NOT an attached '08' in a single
        OCR box: the full case number is searched immediately to its RIGHT.
        """
        if self.reader is None:
            return []
        try:
            detections = self.reader.readtext(image, detail=1, paragraph=False)
        except Exception as exc:
            self._log("Case label detection failed: %s", exc)
            return []
        self._diag_event("ocr_raw_detections",scope="3x3_label",
            detections=[{"text":str(d[1])[:120],
                         "confidence":round(float(d[2] or 0)*100,3),
                         "bbox":[[round(float(p[0]),2),round(float(p[1]),2)]
                                 for p in d[0]]} for d in detections[:150]])
        parsed = []
        for bbox, text, confidence in detections:
            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]
            parsed.append((min(xs), min(ys), max(xs), max(ys), str(text), float(confidence or 0)))
        found = []
        for x0, y0, x1, y1, text, score in parsed:
            match = CASE_FIELD_LABEL_RE.search(text)
            if match:
                end = x0 + (x1-x0)*(match.end()/max(1,len(text)))
                label_kind = "incident" if "incident" in match.group(0).lower() else "case"
                found.append((x0,y0,min(x1,end),y1,label_kind,score))
        # Join INCIDENT + NO. fragments on the same row. If a digit is attached
        # to NO. it must not become part of the label rectangle.
        for left in parsed:
            if not re.search(r"\b(?:INCIDENT|CASE)\b", left[4], re.I):
                continue
            for right in parsed:
                if right is left or right[0] < left[2] - 5:
                    continue
                center_delta = abs((left[1]+left[3])/2 - (right[1]+right[3])/2)
                max_gap = max(70, (left[3]-left[1]) * 4.5)
                if right[0]-left[2] > max_gap or center_delta > max(22, (left[3]-left[1])*0.8):
                    continue
                joined = left[4].strip() + " " + right[4].strip()
                match = CASE_FIELD_LABEL_RE.search(joined)
                if match:
                    end = right[0] + (right[2]-right[0]) * (
                        max(0, match.end()-len(left[4])-1) / max(1, len(right[4])))
                    found.append((left[0], min(left[1],right[1]),
                                  min(right[2], max(right[0],end)),
                                  max(left[3],right[3]),
                                   "incident" if "incident" in match.group(0).lower() else "case",
                                  min(left[5],right[5])))
        # Prefer incident labels, compact boxes, and deduplicate within one line.
        result = []
        for item in sorted(found, key=lambda a: (
                0 if a[4]=="incident" else 1,
                (a[2]-a[0])*(a[3]-a[1]), -a[5])):
            if any(item[4]==r[4] and abs(item[0]-r[0])<30
                   and abs((item[1]+item[3])/2 - (r[1]+r[3])/2)<20
                   for r in result):
                continue
            result.append(item[:5])
        return result[:12]

    @staticmethod
    def _incident_hits_from_detections(detections, label_rect, x_offset, y_offset,
                                       engine="EasyOCR-incident"):
        """Read a complete number to the right, joining separate OCR boxes.

        The algorithm never stops at the year '08', never invents a hyphen, and
        never pulls an OFFENSE CODE number from a different field. A candidate's
        bounding rectangle includes its final digit.
        """
        lx0, ly0, lx1, ly1 = label_rect
        cy = (ly0+ly1)/2
        lh = max(10.0,ly1-ly0)
        raw = []
        for bbox, text, confidence in detections:
            xs = [float(p[0]) / x_offset[0] + x_offset[1] for p in bbox]
            ys = [float(p[1]) / y_offset[0] + y_offset[1] for p in bbox]
            x0,y0,x1,y1 = min(xs),min(ys),max(xs),max(ys)
            if abs((y0+y1)/2-cy) > max(15,lh*0.65):
                continue
            txt = str(text or "").strip().replace("—","-").replace("–","-")
            match = CASE_FIELD_LABEL_RE.search(txt)
            if match:
                suffix = txt[match.end():].strip()
                if not suffix:
                    continue
                # OCR may include label and number inside one wide box.
                x0 = x0 + (x1-x0)*match.end()/max(1,len(txt))
                txt = suffix
            if x1 < lx1-9 or x0 < lx1-max(15,lh*0.6):
                continue
            if x0-lx1 > max(340,lh*12):
                continue
            raw.append((x0,y0,x1,y1,txt,float(confidence or 0)))
        raw.sort(key=lambda r:(r[0],r[1]))
        numeric=[]
        for item in raw:
            # Any other label between INCIDENT NO. and the digits is a stop.
            if re.search(r"[A-Za-z]",item[4]):
                if not numeric:
                    return []
                break
            if not re.fullmatch(r"[\d\s\-.,:]+",item[4]):
                if not numeric:
                    return []
                break
            if numeric and item[0]-numeric[-1][2] > max(35,lh*1.7):
                break
            if not numeric and item[0]-lx1 > max(210,lh*8):
                return []
            numeric.append(item)
        if not numeric:
            return []
        joined = "".join(r[4].strip() for r in numeric).replace(" ","")
        matches=list(INCIDENT_NUMBER_RE.finditer(joined))
        if not matches:
            return []
        # A second full number to the right is an ambiguity, not an excuse to
        # choose whichever has a higher detector confidence.
        values = list(dict.fromkeys(normalize_case_number(m.group(1)) for m in matches))
        left = int(min(r[0] for r in numeric))
        top = int(min(r[1] for r in numeric))
        right = int(max(r[2] for r in numeric))
        bottom = int(max(r[3] for r in numeric))
        score = max(0.0,min(100.0,min(r[5] for r in numeric)*100))
        return [{"number":v,"score":score,"rect":(left,top,right,bottom),
                 "engines":[engine],"label_type":"incident",
                 "label_context":True,"incident_label_context":True,
                 "case_field_label_context":True,"label_distance":0.0} for v in values]

    def _read_incident_to_right(self, page_image, label_rect):
        lx0,ly0,lx1,ly1 = label_rect
        h,w = page_image.shape[:2]
        label_h=max(10.0,ly1-ly0)
        pad_y=max(14,int(label_h*0.65))
        x0=max(0,int(lx0-12))
        x1=min(w,int(lx1+max(470,label_h*16)))
        y0=max(0,int(ly0-pad_y))
        y1=min(h,int(ly1+pad_y))
        if y1<=y0 or x1<=x0:
            return []
        cropped=page_image[y0:y1,x0:x1].copy()
        passes=[(cropped,1.0,"EasyOCR-incident")]
        try:
            from PIL import Image,ImageEnhance,ImageOps,ImageFilter
            pil=Image.fromarray(cropped).convert("RGB")
            pil=ImageOps.autocontrast(pil,cutoff=1)
            pil=ImageEnhance.Contrast(pil).enhance(1.35)
            pil=ImageEnhance.Sharpness(pil).enhance(1.2)
            doubled=pil.resize((pil.width*2,pil.height*2),Image.Resampling.LANCZOS)
            passes.append((np.asarray(doubled),2.0,"EasyOCR-incident-enhanced"))
        except Exception as exc:
            self._log("Could not enhance INCIDENT NO. field: %s",exc)
        hits=[]
        for image,scale,engine in passes:
            try:
                detected=self.reader.readtext(image,detail=1,paragraph=False)
                found=self._incident_hits_from_detections(
                    detected,label_rect,(scale,x0),(scale,y0),engine)
                hits=self._merge_case_hits(hits,found)
            except Exception as exc:
                self._log("INCIDENT NO. right-field pass failed: %s",exc)
        # TrOCR is a supporting read ONLY of the right-hand field, never a
        # search across the printed OFFENSE CODE or other page regions.
        if any(hit.get("score", 0) >= self._get_auto_threshold() for hit in hits):
            return hits
        try:
            num_x0=max(0,int(lx1+1))
            number_strip=page_image[max(0,int(ly0-pad_y)):min(h,int(ly1+pad_y)),
                                    num_x0:min(w,int(lx1+max(300,label_h*10)))]
            if number_strip.size:
                trocr,trocr_score=self._trocr_read_scored(number_strip)
                for match in INCIDENT_NUMBER_RE.finditer(trocr.replace("—","-").replace("–","-")):
                    hits=self._merge_case_hits(hits,[{
                        "number":normalize_case_number(match.group(1)),"score":trocr_score,
                        "rect":None,"engines":["TrOCR-incident"],
                        "label_type":"incident","label_context":True,
                        "incident_label_context":True,"label_distance":0.0}])
        except Exception as exc:
            self._log("TrOCR INCIDENT NO. field failed: %s",exc)
        return hits


    def _ocr_pdf(self,path,_search_inches=3):
        """Search 3x3 first; retry top-right 5x5 only if no 08- case found.

        A 100% labeled printed read qualifies for immediate transfer; an
        unlabeled handwritten candidate requires 95% AND two matching
        matching before-and-after neighbors rescue a blank middle page.
        An unreadable middle file can use the secondary neighbor fallback.
        """
        doc=pymupdf.open(path)
        try:
            if not len(doc):
                return ("",None,"Empty PDF — manual review required",False,0.0,[],False)
            results=[]
            pages=[]
            any_label=False
            dimension_px=int(round(_search_inches*72*2.2))
            for page_index,page in enumerate(doc):
                pix=page.get_pixmap(matrix=pymupdf.Matrix(2.2,2.2),alpha=False)
                image=np.frombuffer(pix.samples,dtype=np.uint8).reshape(
                    pix.height,pix.width,pix.n).copy()
                pages.append(image)
                height,width=image.shape[:2]
                side=min(height,dimension_px)
                crop_width=min(width,dimension_px)
                offset_x=width-crop_width
                region=image[:side,offset_x:].copy()
                # The main OCR detector is NEVER called on the full page.
                labels=self._find_case_field_label_regions(region)
                valid_labels=[x for x in labels if x[4] in ("incident","case")]
                self._diag_event("ocr_label_pass",page=page_index+1,
                     region=[offset_x,0,width,side],
                     labels=[{"bbox":list(x[:4]),"label":x[4]} for x in valid_labels])
                if not valid_labels:
                    try:
                        from PIL import Image,ImageEnhance,ImageOps
                        focused=ImageOps.autocontrast(Image.fromarray(region).convert("RGB"),cutoff=1)
                        focused=ImageEnhance.Contrast(focused).enhance(1.4)
                        doubled=focused.resize(
                            (focused.width*2,focused.height*2),Image.Resampling.BICUBIC)
                        detected=self._find_case_field_label_regions(np.asarray(doubled))
                        valid_labels=[(r[0]/2,r[1]/2,r[2]/2,r[3]/2,r[4])
                                      for r in detected if r[4] in ("incident","case")]
                    except Exception as exc:
                        self._log("Enhanced 3x3 label recognition failed: %s",exc)
                if valid_labels:
                    any_label=True
                    for label in valid_labels:
                        hits=self._read_incident_to_right(region,label[:4])
                        absolute=[]
                        for h in hits:
                            candidate=dict(h)
                            rect=candidate.get("rect")
                            if rect:
                                candidate["rect"]=(int(rect[0]+offset_x),int(rect[1]),
                                                   int(rect[2]+offset_x),int(rect[3]))
                            absolute.append(candidate)
                        self._diag_event("field_ocr_pass",page=page_index+1,
                             label={"bbox":list(label[:4]),"type":label[4]},
                             hits=self._diag_hits(absolute))
                        if absolute:
                            results.append((page_index,image,absolute,label[4]))
                else:
                    try:
                        hits=self._find_case_candidates(
                            region,x_offset=offset_x,y_offset=0,run_trocr=True,
                            case_pattern_override=re.compile(
                                r"(?<!\d)(08\s*-\s*\d{1,7})(?!\d)"))
                    except Exception as exc:
                        self._log("3x3 header OCR failed: %s",exc)
                        hits=[]
                    inside=[]
                    for h in hits:
                        rect=h.get("rect")
                        if not rect:
                            # A TrOCR-only, unlocalized guess must never
                            # trigger the 95% handwritten auto-approval rule.
                            continue
                        x0,y0,x1,y1=map(float,rect)
                        if (x0>=offset_x-4 and x1<=width+4 and
                                y0>=0 and y1<=side+4):
                            inside.append(h)
                    self._diag_event("candidate_pass",page=page_index+1,
                         source="unlabeled_3x3",all_hits=self._diag_hits(hits),
                         region_hits=self._diag_hits(inside))
                    # Focus on rightmost handwriting. This is a subset of
                    # the existing 3x3 OCR region, never a full-page read.
                    margin_x=int(round(crop_width*.55))
                    margin_h=min(side,int(round(1.5*72*2.2)))
                    margin_crop=region[:margin_h,margin_x:].copy()
                    if margin_crop.size:
                        try:
                            focused_hits=self._find_case_candidates(
                                margin_crop,x_offset=offset_x+margin_x,
                                y_offset=0,run_trocr=True,
                                case_pattern_override=re.compile(
                                    r"(?<!\d)(08\s*-\s*\d{1,7})(?!\d)"))
                            focused_valid=[]
                            for h in focused_hits:
                                rect=h.get("rect")
                                if rect and float(rect[0])>=offset_x+margin_x-4:
                                    focused_valid.append(h)
                            self._diag_event("handwritten_margin_pass",
                                page=page_index+1,
                                region=[offset_x+margin_x,0,width,margin_h],
                                hits=self._diag_hits(focused_valid))
                            inside=self._merge_case_hits(inside,focused_valid)
                        except Exception as exc:
                            self._log("Top-right margin OCR pass failed: %s",exc)
                    if inside:
                        upper=_mfcase_header_field_candidates(inside,image.shape)
                        if upper and max(float(h.get("score") or 0) for h in upper)>=95.0:
                            results.append((page_index,image,upper,"header"))
                        else:
                            results.append((page_index,image,inside,"fallback"))
            if any(r[3] in ("incident","case") for r in results):
                # A complete labeled read wins over any fallback or header.
                results=[r for r in results if r[3] in ("incident","case")]
            if not results:
                if _search_inches==3:
                    self._diag_event("ocr_second_pass",
                        reason="no_08_candidate_in_3x3",search_inches=5)
                    return self._ocr_pdf(path,_search_inches=5)
                index=max(0,len(pages)-1)
                why=("Case-number caption in 3x3 area but no complete adjacent "
                     "case number — MANUAL REVIEW" if any_label else
                     "No case number found in top-right 3x3 inches — MANUAL REVIEW")
                return ("",(index,pages[-1],"3x3 case field not read",None,[]),
                        why,False,0.0,[],False)
            def rank(entry):
                index,image,hits,kind=entry
                return ({"incident":3,"case":2,"header":1,"fallback":0}[kind],
                        max((float(h.get("score") or 0) for h in hits),default=0.0))
            index,image,hits,kind=max(results,key=rank)
            self._diag_event("ocr_candidates",page=index+1,source=kind,
                 all_candidates=self._diag_hits(hits),
                 per_page=[{"page":x[0]+1,"source":x[3],
                            "hits":self._diag_hits(x[2])} for x in results])
            hits=self._diag_field_filter(hits,kind)
            hits=self._prefer_case_margin(hits,kind,image)
            if not hits:
                if _search_inches==3:
                    self._diag_event("ocr_second_pass",
                        reason="all_3x3_candidates_fail_required_08_prefix",
                        search_inches=5)
                    return self._ocr_pdf(path,_search_inches=5)
                self._diag_event("ocr_decision",page=index+1,source=kind,
                     outcome="manual_review",reason="no_year_matching_case_candidate")
                return ("",(index,image,"3x3 case read rejected",None,[]),
                         "3x3 field has no valid case number matching selected year — MANUAL REVIEW",
                         False,0.0,[],False)
            best=_select_primary_case_hit(hits,kind,image.shape)
            hits=self._resolve_ocr_alternates(hits,kind,best)
            number=best["number"]
            engines=list(best.get("engines") or [])
            engine_names={e.split("-",1)[0] for e in engines}
            agree="EasyOCR" in engine_names and "TrOCR" in engine_names
            distinct={h["number"] for h in hits}
            across=set()
            for _page,_image,_hits,_source in results:
                if _source!=kind:
                    continue
                _filtered=self._diag_field_filter(_hits,kind)
                _filtered=self._prefer_case_margin(_filtered,kind,_image)
                if not _filtered:
                    continue
                _best=_select_primary_case_hit(_filtered,kind,_image.shape)
                across.update(h["number"] for h in
                    self._resolve_ocr_alternates(_filtered,kind,_best))
            numeric_keys=lambda values:{tuple(map(int, v.split("-",1))) for v in values}
            ambiguous=(len(numeric_keys(distinct))>1 or len(numeric_keys(across))>1)
            visuals=[]
            for h in hits:
                rect=h.get("rect")
                visuals.append({
                    "number":h["number"],
                    "rect":[int(v) for v in rect] if rect else None,
                    "score":float(h.get("score") or 0),
                    "engines":list(h.get("engines") or []),
                    "label_type":h.get("label_type"),
                    "label_distance":0.0,
                    "learned_location":False})
            self._diag_event("ocr_decision",page=index+1,source=kind,
                 primary=number,score=float(best.get("score") or 0),
                 ambiguous=ambiguous,conflicting=sorted(distinct),
                 engines=engines,selected_bbox=best.get("rect"),
                 outcome="manual_review" if ambiguous else "candidate_ready")
            if ambiguous:
                note=(f"{_search_inches}x{_search_inches} case field has conflicting OCR reads: "
                      + ", ".join(sorted(distinct))+" — MANUAL REVIEW")
            elif kind=="incident":
                note="Case field right-of-label full number — 3x3 labeled printed OCR"
            elif kind=="case":
                note="CASE NUMBER field full number — 3x3 labeled printed OCR"
            elif kind=="header":
                note="Upper-right header 3x3 handwritten candidate — verify 95% and matching neighbors"
            else:
                note=("Upper-right fallback in 3x3 — right-margin case box "
                      "takes priority over fax pagination; 95% required for "
                      "direct approval, otherwise before/after or REVIEW")
            if _search_inches==5:
                note=note.replace("3x3","5x5")
                note+=" • second-pass 5x5 after no 08- found in 3x3"
            if len(doc)>1:
                note+=f" • page {index+1} of {len(doc)}"
            return (number,(index,image,f"{number} — {kind}",
                    best.get("rect"),visuals),note,
                    "TrOCR" in engine_names,float(best.get("score") or 0),
                    engines,agree)
        finally:
            doc.close()

    def _find_case_candidates(self, image, x_offset=0, y_offset=0, run_trocr=True, case_pattern_override=None):
        """Find only case-number-shaped text, prioritizing nearby field labels."""
        year_prefix = REQUIRED_CASE_YEAR  # HARD GATE: no non-08 case enters OCR candidates.
        case_pattern = case_pattern_override or re.compile(
            r"(?<!\d)(08\s*-\s*\d{1,7})(?!\d)")
        detections = self.reader.readtext(image, detail=1, paragraph=False)
        candidates = {}
        parsed = []
        field_label_boxes = []

        def bbox_metrics(bbox):
            xs = [float(point[0]) for point in bbox]
            ys = [float(point[1]) for point in bbox]
            return min(xs), min(ys), max(xs), max(ys)

        def field_type(text):
            clean = re.sub(r"\s+", " ", str(text or "")).strip()
            if re.search(r"INCIDENT NO\.", clean, re.I):
                return "incident"
            if re.search(r"Case Number", clean, re.I):
                return "case"
            return None

        # Parse the first EasyOCR pass and identify labels that are visible as
        # their own text boxes or combined with the handwritten number.
        for bbox, raw_text, confidence in detections:
            text = str(raw_text).replace("—", "-").replace("–", "-")
            x0, y0, x1, y1 = bbox_metrics(bbox)
            parsed.append((bbox, text, confidence, (x0, y0, x1, y1)))
            label_kind = field_type(text)
            if label_kind:
                field_label_boxes.append((x0, y0, x1, y1, text, label_kind))

        # EasyOCR sometimes splits short labels such as "Sys." and "#" into
        # separate detections. Join adjacent nonnumeric fragments on one line
        # so that the label can still anchor the case number to its right.
        fragments = [item for item in parsed
                     if not any(ch.isdigit() for ch in item[1])]
        fragments.sort(key=lambda item: ((item[3][1] + item[3][3]) / 2.0, item[3][0]))
        for i, left in enumerate(fragments):
            lx0, ly0, lx1, ly1 = left[3]
            joined = left[1]
            jx1 = lx1
            jy0, jy1 = ly0, ly1
            for right in fragments[i + 1:i + 4]:
                rx0, ry0, rx1, ry1 = right[3]
                vertical_delta = abs((ry0 + ry1) / 2.0 - (jy0 + jy1) / 2.0)
                horizontal_gap = rx0 - jx1
                if vertical_delta > 24.0 or horizontal_gap < -4.0 or horizontal_gap > 55.0:
                    continue
                joined = joined + " " + right[1]
                jx1 = rx1
                jy0, jy1 = min(jy0, ry0), max(jy1, ry1)
                label_kind = field_type(joined)
                if label_kind:
                    field_label_boxes.append((lx0, jy0, jx1, jy1, joined, label_kind))

        def label_relation(rect, text):
            # A detection containing both the field label and number is itself
            # an anchored candidate. Otherwise, the number must be to its right
            # and on the same horizontal line as the label.
            own_label = field_type(text)
            if own_label:
                return own_label, 0.0
            if not field_label_boxes:
                return None, float("inf")
            x0, y0, x1, y1 = rect
            cy = (y0 + y1) / 2.0
            best_kind, best_distance = None, float("inf")
            priority = {"incident": 4, "case": 4, "report": 2, "sys": 1}
            for lx0, ly0, lx1, ly1, _label_text, kind in field_label_boxes:
                label_height = max(1.0, ly1 - ly0)
                vertical_delta = abs(cy - (ly0 + ly1) / 2.0)
                horizontal_gap = x0 - lx1
                max_gap = max(260.0, (lx1 - lx0) * 2.5)
                if (-14.0 <= horizontal_gap <= max_gap and
                        vertical_delta <= max(48.0, (y1 - y0 + label_height) * 0.85)):
                    distance = vertical_delta + max(0.0, horizontal_gap) * 0.12
                    if (best_kind is None or priority[kind] > priority[best_kind] or
                            (priority[kind] == priority[best_kind] and distance < best_distance)):
                        best_kind, best_distance = kind, distance
            return best_kind, best_distance

        def add_candidate(number, engine, score, rect, label_kind=None, label_distance=float("inf")):
            number=re.sub(r"\s+","",str(number or ""))
            if not CASE_RE.fullmatch(number) or not number.startswith("08-"):
                return
            entry = candidates.setdefault(number, {
                "number": number, "score": 0.0, "rect": rect,
                "engines": [], "label_context": False,
                "incident_label_context": False, "label_type": None,
                "label_distance": float("inf")})
            if engine not in entry["engines"]:
                entry["engines"].append(engine)
            previous_score = entry["score"]
            entry["score"] = max(previous_score, float(score or 0.0))
            if rect and (not entry.get("rect") or float(score or 0.0) > previous_score):
                entry["rect"] = rect
            if label_kind:
                priority = {"incident": 4, "case": 4, "report": 2, "sys": 1}
                old_kind = entry.get("label_type")
                if old_kind is None or priority[label_kind] > priority[old_kind]:
                    entry["label_type"] = label_kind
                entry["label_context"] = True
                entry["label_distance"] = min(entry["label_distance"], label_distance)
                if label_kind == "incident":
                    entry["incident_label_context"] = True

        combined_text = []
        for bbox, text, confidence, local_rect in parsed:
            combined_text.append(text)
            normalized = re.sub(r"\s*-\s*", "-", text)
            x0, y0, x1, y1 = local_rect
            rect = (
                max(0, int(x0) + x_offset), max(0, int(y0) + y_offset),
                int(x1) + x_offset, int(y1) + y_offset)
            label_kind, label_distance = label_relation(local_rect, text)

            # Standard case-number form remains the primary rule.
            strict_matches = list(case_pattern.finditer(normalized))
            for match in strict_matches:
                add_candidate(
                    re.sub(r"\s+", "", match.group(1)), "EasyOCR",
                    float(confidence) * 100.0, rect, label_kind, label_distance)

            digit_count = sum(ch.isdigit() for ch in text)
            likely_case_line = digit_count >= 2 or "-" in text
            # Fax pagination, e.g. "122 P.005/015", is not a case number.
            # Never let TrOCR hallucinations turn a slash into an 08- prefix.
            fax_pagination=bool(re.search(
                r"(?:\bP\s*[.:-]?\s*\d{1,4}\s*/\s*\d{1,4}\b|"
                r"\b\d{1,4}\s*/\s*\d{1,4}\b)",
                text,re.I) and not strict_matches)
            if (run_trocr and likely_case_line and not fax_pagination
                    and not (strict_matches and float(confidence)>=0.95)):
                try:
                    crop_x0 = max(0, int(x0) - 12)
                    crop_y0 = max(0, int(y0) - 12)
                    crop_x1 = min(image.shape[1], int(x1) + 12)
                    crop_y1 = min(image.shape[0], int(y1) + 12)
                    line_crop = image[crop_y0:crop_y1, crop_x0:crop_x1]
                    if line_crop.size:
                        trocr_line, trocr_score = self._trocr_read_scored(line_crop)
                        trocr_norm = re.sub(
                            r"\s*-\s*", "-",
                            trocr_line.replace("—", "-").replace("–", "-"))
                        for match in case_pattern.finditer(trocr_norm):
                            add_candidate(
                                re.sub(r"\s+", "", match.group(1)), "TrOCR",
                                trocr_score, rect, label_kind, label_distance)
                except Exception as exc:
                    self._log("TrOCR line recognition failed: %s", exc)

        # Re-read each OCR box that looks number-like as a separate enlarged
        # image. This is a genuine second OCR pass on the crop, not a numerical
        # score boost. The detector's returned confidence is retained as-is.
        try:
            from PIL import Image, ImageEnhance, ImageOps, ImageFilter
            enhanced_seen = set()
            for _bbox, raw_text, _raw_score, (bx0, by0, bx1, by1) in parsed:
                # No duplicate enhanced recognition for a strongly recognized
                # exact 08- candidate. Keep enhancement for uncertain reads.
                if (float(_raw_score)>=0.95 and
                    case_pattern.search(re.sub(r"\s*-\s*","-",str(raw_text)))):
                    continue
                if ("/" in str(raw_text) and
                    not case_pattern.search(str(raw_text))):
                    # Skip fax counters in the enhanced OCR pass too.
                    continue
                if sum(ch.isdigit() for ch in raw_text) < 2 and "-" not in raw_text:
                    continue
                cx0 = max(0, int(bx0) - 14); cy0 = max(0, int(by0) - 10)
                cx1 = min(image.shape[1], int(bx1) + 14); cy1 = min(image.shape[0], int(by1) + 10)
                if cx1 <= cx0 or cy1 <= cy0:
                    continue
                crop_key = (cx0, cy0, cx1, cy1)
                if crop_key in enhanced_seen:
                    continue
                enhanced_seen.add(crop_key)
                if len(enhanced_seen) > 12:
                    break
                crop_arr = image[cy0:cy1, cx0:cx1]
                if not crop_arr.size:
                    continue
                gray = Image.fromarray(crop_arr).convert("L")
                gray = ImageOps.autocontrast(gray, cutoff=1)
                gray = ImageEnhance.Contrast(gray).enhance(1.25)
                gray = ImageEnhance.Sharpness(gray).enhance(1.4)
                enlarged = gray.resize((max(1, gray.width * 3), max(1, gray.height * 3)),
                                       Image.Resampling.LANCZOS).convert("RGB")
                enhanced_reads = self.reader.readtext(np.asarray(enlarged), detail=1, paragraph=False)
                for ebbox, enhanced_text, enhanced_score in enhanced_reads:
                    normalized = re.sub(r"\s*-\s*", "-",
                                         str(enhanced_text).replace("—", "-").replace("–", "-"))
                    exs = [float(point[0]) for point in ebbox]
                    eys = [float(point[1]) for point in ebbox]
                    er = (cx0 + min(exs) / 3.0, cy0 + min(eys) / 3.0,
                          cx0 + max(exs) / 3.0, cy0 + max(eys) / 3.0)
                    absolute_rect = (int(er[0] + x_offset), int(er[1] + y_offset),
                                     int(er[2] + x_offset), int(er[3] + y_offset))
                    kind, distance = label_relation(er, str(enhanced_text))
                    for match in case_pattern.finditer(normalized):
                        add_candidate(re.sub(r"\s+", "", match.group(1)),
                                      "EasyOCR-enhanced", float(enhanced_score) * 100.0,
                                      absolute_rect, kind, distance)
        except Exception as exc:
            self._log("Enlarged candidate-crop OCR pass failed: %s", exc)

        # A second OCR pass is tightly anchored to any recognized case-number
        # field label (INCIDENT NO., CASE NO., REPORT NO., or the rarer SYS. #).
        # It enlarges only the label and the area to its right, not the whole page.
        if run_trocr and field_label_boxes:
            seen_regions = set()
            for lx0, ly0, lx1, ly1, _label_text, label_kind in field_label_boxes:
                region_x0 = max(0, int(lx0) - 8)
                region_x1 = min(image.shape[1], int(lx1 + max(300.0, (lx1 - lx0) * 2.5)))
                label_height = max(1.0, ly1 - ly0)
                vertical_pad = max(24, int(round(label_height * 1.4)))
                region_y0 = max(0, int(ly0) - vertical_pad)
                region_y1 = min(image.shape[0], int(ly1) + vertical_pad)
                region_key = (region_x0, region_y0, region_x1, region_y1)
                if region_key in seen_regions or region_x1 <= region_x0 or region_y1 <= region_y0:
                    continue
                seen_regions.add(region_key)
                anchored_crop = image[region_y0:region_y1, region_x0:region_x1]
                if not anchored_crop.size:
                    continue
                # Upscale the relatively small field strip before a second
                # EasyOCR pass; this can recover a faint final handwritten digit.
                try:
                    from PIL import Image, ImageEnhance
                    pil_crop = Image.fromarray(anchored_crop).convert("RGB")
                    enlarged = pil_crop.resize(
                        (max(1, pil_crop.width * 2), max(1, pil_crop.height * 2)),
                        Image.Resampling.BICUBIC)
                    enhanced = ImageEnhance.Contrast(enlarged).enhance(1.25)
                    enhanced = ImageEnhance.Brightness(enhanced).enhance(1.03)
                    anchor_detections = self.reader.readtext(
                        np.asarray(enhanced), detail=1, paragraph=False)
                    for bbox, raw_text, confidence in anchor_detections:
                        text = str(raw_text).replace("—", "-").replace("–", "-")
                        local = bbox_metrics(bbox)
                        ax0, ay0, ax1, ay1 = [v / 2.0 for v in local]
                        local_rect = (
                            region_x0 + ax0, region_y0 + ay0,
                            region_x0 + ax1, region_y0 + ay1)
                        absolute_rect = (
                            int(local_rect[0] + x_offset), int(local_rect[1] + y_offset),
                            int(local_rect[2] + x_offset), int(local_rect[3] + y_offset))
                        # The second pass is scoped to the recognized field strip;
                        # a matching value in this crop is label-associated.
                        for match in case_pattern.finditer(re.sub(r"\s*-\s*", "-", text)):
                            add_candidate(
                                re.sub(r"\s+", "", match.group(1)), "EasyOCR-anchored",
                                float(confidence) * 100.0, absolute_rect,
                                label_kind, 0.0)
                except Exception as exc:
                    self._log("Label-anchored EasyOCR recognition failed: %s", exc)

                try:
                    label_value_text, trocr_score = self._trocr_read_scored(anchored_crop)
                    label_value_norm = re.sub(
                        r"\s*-\s*", "-",
                        label_value_text.replace("—", "-").replace("–", "-"))
                    region_rect = (
                        region_x0 + x_offset, region_y0 + y_offset,
                        region_x1 + x_offset, region_y1 + y_offset)
                    for match in case_pattern.finditer(label_value_norm):
                        add_candidate(
                            re.sub(r"\s+", "", match.group(1)),
                            "TrOCR-anchored", trocr_score, region_rect, label_kind, 0.0)
                except Exception as exc:
                    self._log("Label-anchored TrOCR recognition failed: %s", exc)

        # Preserve joined detections, but only when the joined text still
        # contains a complete case-number-shaped value.
        combined = " ".join(combined_text)
        for match in case_pattern.finditer(re.sub(r"\s*-\s*", "-", combined)):
            number = re.sub(r"\s+", "", match.group(1))
            if number not in candidates:
                add_candidate(number, "EasyOCR", 0.0, None)

        # Return every strict case-number-shaped candidate. Field labels affect
        # ranking, but do not hide other possible case numbers from the reviewer.
        return list(candidates.values())


    def _poll(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "folder_load_result":
                    _, generation, year, saved, pdfs, state_error = msg
                    if generation == self._folder_load_generation:
                        if self._folder_load_timeout_id is not None:
                            self.root.after_cancel(self._folder_load_timeout_id)
                            self._folder_load_timeout_id=None
                        try:
                            self._apply_folder_load(year, saved, pdfs, state_error)
                        finally:
                            self._loader_finish("rolls")
                            self.refresh_root_btn.config(state="normal")
                            if self._startup_ready and not (self._loading_jobs & {"rolls", "learning"}):
                                self.choose_folder_btn.config(state="normal")
                elif kind == "folder_load_error":
                    _, generation, detail = msg
                    if generation == self._folder_load_generation:
                        if self._folder_load_timeout_id is not None:
                            self.root.after_cancel(self._folder_load_timeout_id)
                            self._folder_load_timeout_id=None
                        self._loader_finish("rolls")
                        self.refresh_root_btn.config(state="normal")
                        self.choose_folder_btn.config(state="normal")
                        self.status.config(text="Could not refresh root PDFs; current list remains unchanged.")
                        messagebox.showerror("Folder load failed", detail)
                elif kind == "offline_models_ready":
                    self._offline_prepare_busy=False
                    self.prepare_offline_btn.config(state="normal",
                                                    text="PREPARE OFFLINE MODELS")
                    self.reader=None
                    self.trocr_model=None
                    self.trocr_processor=None
                    self.trocr_error=""
                    self.status.config(text="Models installed locally. "
                                            "Initializing offline OCR…")
                    self._begin_model_preload()
                elif kind == "offline_models_error":
                    self._offline_prepare_busy=False
                    self.prepare_offline_btn.config(state="normal",
                                                    text="PREPARE OFFLINE MODELS")
                    self.status.config(text="Model preparation failed: "+str(msg[1]))
                    messagebox.showerror("Offline models",
                        "Could not prepare all models; nothing was uploaded. "
                        "Check the connection or available disk space.\n\n"+str(msg[1]))
                elif kind == "startup_models_ready":
                    self._complete_startup()
                elif kind == "startup_models_error":
                    self._fail_startup(msg[1])
                elif kind == "model_load_start":
                    self._loader_start("model", msg[1])
                elif kind == "model_load_done":
                    self._loader_finish("model")
                elif kind == "learning_connect_result":
                    _, connected, detail = msg
                    startup_origin = self._learning_connection_source == "startup"
                    self._learning_connection_source = None
                    self._loader_finish("learning")
                    if (not (self._loading_jobs & {"rolls", "learning"})
                            and not startup_origin):
                        self.choose_folder_btn.config(state="normal")
                    self._refresh_learning_stats()
                    self._refresh_learning_button_state()
                    if connected:
                        self.status.config(text=f"{detail} PDF queue is ready.")
                        if startup_origin:
                            self.root.after(250, self._start_automatic_learning_if_ready)
                        elif self._startup_learning_pending:
                            self.root.after(250, self._start_automatic_learning_if_ready)
                    else:
                        if startup_origin:
                            self._startup_learning_pending = False
                            self._fail_startup(detail)
                        self.status.config(text="Shared learning unavailable; scanning disabled.")
                        if not startup_origin:
                            messagebox.showwarning(
                            "Shared learning unavailable",
                            "Scanning is blocked until shared learning is available. "
                            "Reconnect to the department network and retry startup.\n\n"
                            f"Expected location:\n\\\\172.16.1.231\\MicroficheNew\\2008\\.case_ocr_learning\n\n"
                            f"Details: {detail}")
                elif kind == "blank":
                    _, iid, destination, idx = msg
                    if iid in self.records:
                        self.records[iid]["status"] = "Moved to BLANKS"
                        if self.tree.exists(iid):
                            self.tree.delete(iid)
                    self._scan_progress_update(idx,"SCANNING")
                    self.status.config(
                        text=f"Scanned {idx} of {len(self.scan_items)} PDFs. Blank PDF moved to BLANKS: {destination.name}")
                    self._refresh_review_controls()
                    self._save_state()
                elif kind == "blank_conflict":
                    _, iid, destination, idx = msg
                    if iid in self.records:
                        self.records[iid]["status"] = "Blank candidate — filename conflict; left in root"
                        if self.tree.exists(iid):
                            self._insert_scan_row(iid, self.records[iid])
                    self._scan_progress_update(idx,"SCANNING")
                    self.status.config(
                        text=f"Blank PDF not moved because BLANKS already contains {destination.name}.")
                    self._refresh_review_controls()
                    self._save_state()
                elif kind == "row":
                    _, iid, cand, snip, note, hand, score, engines, agreement, idx = msg
                    r = self.records[iid]
                    normalized_candidate = normalize_case_number(cand) if cand else ""
                    if r.get("manual_approved") and not r.get("auto_labeled"):
                        self._diag_event("manual_override_preserved",
                            file=Path(r["path"]).name,ocr_candidate=normalized_candidate,
                            confirmed=r.get("confirmed"),score=float(score or 0))
                        if snip is not None and r.get("snippet") is None:
                            r["snippet"]=snip
                        self._insert_scan_row(iid,r)
                        self._scan_progress_update(idx,"SCANNING")
                        self._save_state()
                        continue
                    r.update(
                        candidate=normalized_candidate, snippet=snip, status=note,
                        handwritten=hand, score=score, engines=engines,
                        engine_agreement=agreement,
                        scan_source=("incident" if note.startswith("Case field right-of-label")
                                     or note.startswith("INCIDENT NO. has conflicting")
                                     else "case" if note.startswith("CASE NUMBER field")
                                     else "fallback" if note.startswith("Upper-right")
                                     else "incident-unreadable" if note.startswith("Case field located")
                                     else "unknown"),
                        scan_ambiguous="conflicting OCR reads" in note,
                        sequence_excluded=False,
                        sequence_conflict=False, sequence_inferred=False,
                        sequence_anchors=[], sequence_suggestion="")
                    if note.startswith("Upper-right header"):
                         r["scan_source"]="header"
                     # Diagnostic counters belong to THIS run, not accumulated saved state.
                    if note.startswith("Scan error"):
                        reason = "scan_error"
                    elif "conflicting OCR reads" in note:
                        reason = "conflicting_readings"
                    elif note.startswith("Upper-right"):
                        reason = "unverified_fallback_location"
                    elif not normalized_candidate:
                        reason = "no_case_number"
                    elif r["scan_source"] == "incident" and score < self._get_auto_threshold():
                        reason = "incident_low_or_uncalibrated_confidence"
                    else:
                        reason = "incident_candidate"
                    self._diag_event("row_decision",file=Path(r["path"]).name,
                         candidate=normalized_candidate,score=float(score or 0),
                         source=r["scan_source"],ambiguous=bool(r["scan_ambiguous"]),
                         status=note,engines=list(engines or []),
                         outcome_bucket=reason)
                    self._run_outcomes[reason] = self._run_outcomes.get(reason, 0) + 1
                    auto_done = False
                    labeled = r["scan_source"] in ("incident","case")
                    positioned_header = r["scan_source"] == "header"
                    strong = float(score or 0) >= self._get_auto_threshold()
                    # A fallback's 100% score is usable only with a real OCR
                    # bounding box from _ocr_pdf's 3x3 region, not an unlocated
                    # TrOCR guess or a preview-only crop.
                    fallback_95 = (
                        r["scan_source"] == "fallback"
                        and float(score or 0) >= self._get_auto_threshold()
                        and isinstance(snip,(tuple,list)) and len(snip)>=4
                        and snip[3] is not None
                        and self._is_corner_case_rect(snip[3],snip[1])
                    )
                    if (normalized_candidate
                            and ((labeled or positioned_header) and strong
                                 or fallback_95)
                            and not r["scan_ambiguous"]
                            and self.auto_label_var.get()):
                        block_reason=self._auto_label_block_reason(
                            score,agreement,normalized_candidate)
                        if block_reason is None:
                            r["confirmed"]=normalized_candidate
                            r["auto_labeled"]=True
                            r["manual_approved"]=True
                            rule=("95pct_labeled_case_field" if labeled
                                  else "95pct_unique_upper_right_header" if positioned_header
                                  else "95pct_localized_3x3_fallback")
                            area=("labeled field" if labeled else "upper-right header"
                                  if positioned_header else "3x3 localized fallback")
                            r["status"]=(
                                f"{score:.1f}% {area} — automatically approved; "
                                "awaiting SEND ALL SELECTED")
                            self._diag_event("approval_decision",
                                file=Path(r["path"]).name,outcome="auto_approved",
                                rule=rule,case=normalized_candidate,
                                score=float(score or 0),source=r["scan_source"])
                            self._move_to_completed(iid)
                            auto_done=True
                            self._run_outcomes["auto_approved"]=(
                                self._run_outcomes.get("auto_approved",0)+1)
                        else:
                            r["status"]=note+" • Auto-confirm blocked: "+block_reason
                    elif not normalized_candidate:
                        r["status"]=(
                            "Blank PDF — retained in root for manual review"
                            if note.startswith("Blank PDF") else
                            "No valid 08- case number in 3x3 — checking before/after fallback")
                    elif r["scan_ambiguous"]:
                        r["status"]=note+" — conflicting case-field reads; manual review"
                    elif labeled or positioned_header:
                        r["status"]=(f"{score:.1f}% case-field OCR; below 95% "
                                      "or auto-confirm disabled — manual review")
                    elif r["scan_source"]=="fallback":
                        r["status"]=(f"{float(score or 0):.1f}% 3x3 fallback — "
                            "requires an unambiguous localized 95% OCR read "
                            "and auto-confirm enabled; otherwise MANUAL REVIEW")
                    # A fallback at 100% with one localized case can auto-send;
                    # unlocated guesses and conflicting year-matched cases cannot.
                    self._diag_event("row_final",file=Path(r["path"]).name,
                         status=r.get("status"),candidate=normalized_candidate,
                         score=float(score or 0),auto_approved=auto_done,
                         source=r.get("scan_source"),ambiguous=r.get("scan_ambiguous"))
                    if not auto_done and self.tree.exists(iid):
                        self._insert_scan_row(iid, r)
                    self._scan_progress_update(idx,"SCANNING")
                    self.status.config(text=f"Scanned {idx} of {len(self.scan_items)} PDF(s).")
                    self._refresh_review_controls()
                    self._update_completed_count()
                    self._save_state()
                elif kind == "export_result":
                    _, iid, source_name, destination, error = msg
                    self._apply_export_result(iid, source_name, destination, error)
                elif kind == "export_done":
                    if not self.busy:
                        self._scan_progress_update(len(self.scan_items),"SELECTED SEND COMPLETE")
                    self.export_busy = False
                    self._export_in_flight_iids.clear()
                    self._update_completed_count()
                    self._refresh_scan_button()
                    self._export_reschedule = False
                    self._schedule_auto_export()
                elif kind == "engine_status":
                    _, message = msg
                    self._loader_finish("ocr")
                    self.model_status_label.config(text=message)
                elif kind == "engine_warning":
                    _, message = msg
                    self.model_status_label.config(text=message)
                    self._log(message)
                elif kind == "model_status":
                    _, message = msg
                    self.model_status_label.config(text=message)
                elif kind == "training_progress":
                    _, percent, stage = msg
                    self._update_training_progress(percent, stage)
                    if self._learning_auto_run:
                        self._startup_notice(f"Learning update {int(percent)}% — {stage}")
                elif kind == "learning_stopped":
                    _, message = msg
                    self.trocr_training = False
                    self.learning_pause_event.set()
                    self.learning_stop_event.clear()
                    self.trocr_model = None
                    self.trocr_processor = None
                    self.trocr_error = ""
                    self._load_learning_data()
                    self.status.config(text=message)
                    self._log(message)
                    self._refresh_scan_button()
                    self._refresh_learning_button_state()
                    self._update_training_progress(100, message, finished=True)
                    self._learning_auto_run = False
                    # Flush any approved transfers deferred during training.
                    self._schedule_auto_export()
                    if not self._startup_ready:
                        self._begin_model_preload()
                elif kind == "learning_status":
                    _, message = msg
                    self.trocr_training = False
                    self.learning_pause_event.set()
                    self.learning_stop_event.clear()
                    # The active model can be reloaded from its validated disk checkpoint.
                    self.trocr_model = None
                    self.trocr_processor = None
                    self.trocr_error = ""
                    self._load_learning_data()
                    self.status.config(text=message)
                    self._log(message)
                    self._refresh_scan_button()
                    self._refresh_learning_button_state()
                    self._update_training_progress(100, message, finished=True)
                    self._learning_auto_run = False
                    # Flush any approved transfers deferred during training.
                    self._schedule_auto_export()
                    if not self._startup_ready:
                        self._begin_model_preload()
                elif kind == "fatal":
                    self._loader_finish("ocr")
                    self._loader_finish("model")
                    self.busy = False
                    self._refresh_scan_button()
                    self.pause_btn.config(state="disabled", text="PAUSE")
                    self.restart_btn.config(state="normal", text="RESTART")
                    self._refresh_review_controls()
                    self._refresh_learning_button_state()
                    self._update_completed_count()
                    self._log("Fatal scan error: %s", msg[1])
                    messagebox.showerror("OCR initialization failed", msg[1])
                elif kind == "update_check_result":
                    self.update_busy = False
                    self.update_btn.config(state="normal", text="UPDATE")
                    latest_version, release, error = msg[1], msg[2], msg[3]
                    if self._startup_auto_update:
                        if error:
                            self._startup_software_complete(error)
                        elif not release:
                            self._startup_software_complete()
                        else:
                            self.update_release = release
                            self._startup_notice(
                                f"Version {latest_version} found. Downloading and validating; the app will restart automatically…")
                            self._download_update(release)
                    elif error:
                        messagebox.showwarning("Update check", error)
                    elif not release:
                        messagebox.showinfo("Update check", f"M.F. Case Number OCR is up to date (V{self.app_version}).")
                    else:
                        self.update_release = release
                        if messagebox.askyesno(
                            "Update available",
                            f"Version {latest_version} is available. You are running V{self.app_version}.\n\n"
                            "Download and install the update now? The program will restart when installation finishes."):
                            self._download_update(release)
                elif kind == "update_download_ready":
                    self.update_busy = False
                    self.update_btn.config(state="normal", text="UPDATE")
                    archive, version = Path(msg[1]), msg[2]
                    if self._startup_auto_update:
                        self._startup_notice(
                            f"V{str(version).lstrip('v')} verified. The program will close and reopen automatically now.")
                        self.root.after(1200, lambda path=archive: self._launch_update_installer(path))
                    elif messagebox.askyesno(
                            "Install update",
                            f"Version {version} has downloaded and passed ZIP integrity checks.\n\n"
                            "The app will close, install the update, and reopen. Continue?"):
                        self._launch_update_installer(archive)
                    else:
                        try:
                            archive.unlink(missing_ok=True)
                        except Exception:
                            pass
                elif kind == "update_error":
                    self.update_busy = False
                    self.update_btn.config(state="normal", text="UPDATE")
                    if self._startup_auto_update:
                        self._startup_software_complete(msg[1])
                    else:
                        messagebox.showerror("Update failed", msg[1])
                elif kind == "stopped":
                    self._loader_finish("ocr")
                    self._loader_finish("model")
                    self.busy = False
                    self.paused = False
                    self.stop_event.clear()
                    self.pause_event.set()
                    self._refresh_scan_button()
                    self.pause_btn.config(state="disabled", text="PAUSE")
                    self.restart_btn.config(state="normal", text="RESTART")
                    self._refresh_review_controls()
                    self._refresh_learning_button_state()
                    self._update_completed_count()
                    self.status.config(text="Scan stopped. Press RESTART to continue with the remaining files.")
                    self._save_state()
                elif kind == "done":
                    self._loader_finish("ocr")
                    self._loader_finish("model")
                    self.busy = False
                    self._scan_progress_update(len(self.scan_items),"SCAN COMPLETE — READY TO SEND")
                    self._apply_sequence_consensus()
                    self.paused = False
                    self.pause_event.set()
                    self._refresh_scan_button()
                    self.pause_btn.config(state="disabled", text="PAUSE")
                    self.restart_btn.config(state="normal")
                    self._refresh_review_controls()
                    self._refresh_learning_button_state()
                    self._update_completed_count()
                    self.status.config(
                        text="Scan complete. All PDFs remain in the root folder. "
                             "Review or edit them, then press SEND ALL SELECTED.")
                    self._refresh_learning_button_state()
                    self._save_state()
                    self._diag_event("run_end",summary=dict(self._run_outcomes))
                    threading.Thread(target=self._diag_sync_shared,daemon=True).start()
                    self._log("Scan-run summary: %s", json.dumps(self._run_outcomes, sort_keys=True))
                    self._schedule_auto_export()
        except queue.Empty:
            pass
        self._update_scan_file_count()
        self.root.after(150, self._poll)

    def _review_ready_ids(self):
        ready = []
        for iid, record in self.records.items():
            if record.get("status") in ("Not scanned", "Scanning"):
                continue
            if record.get("status") == "Moved to BLANKS":
                continue
            record_path = record.get("path")
            if not record_path or not Path(record_path).is_file():
                continue
            ready.append(iid)
        return ready

    def _refresh_review_controls(self):
        if not hasattr(self,"review_btn") or not hasattr(self,"tree"):
            return
        ready=set(self._review_ready_ids())
        enabled=not self.trocr_training and any(
            iid in ready and self.tree.exists(iid) and
            (not self.folder or Path(self.records[iid]["path"]).parent==Path(self.folder))
            for iid in self.tree.selection())
        state="normal" if enabled else "disabled"
        self.review_btn.config(state=state)

    def on_double_click(self, event):
        if self.trocr_training:
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            if self.tree.identify_column(event.x) == "#1":
                return "break"
            if iid not in self._review_ready_ids():
                return
            selected = [item for item in self.tree.selection()
                        if item in self._review_ready_ids()]
            if iid not in selected:
                selected = [iid]
            self._open_review_queue(selected, selected.index(iid))

    def _on_completed_click(self, event):
        """Checkbox clicks affect export selection, not the review highlight."""
        if (self.completed_tree.identify_region(event.x, event.y) == "cell"
                and self.completed_tree.identify_column(event.x) == "#7"):
            iid = self.completed_tree.identify_row(event.y)
            if iid in self.records and self.completed_tree.exists(iid):
                if iid in self.completed_checked_iids:
                    self.completed_checked_iids.remove(iid)
                else:
                    self.completed_checked_iids.add(iid)
                self._update_completed_checkbox(iid)
                self._update_completed_count()
            return "break"

    def _update_completed_checkbox(self, iid):
        if not self.completed_tree.exists(iid):
            return
        self.completed_tree.set(iid, "select", "☑" if iid in self.completed_checked_iids else "☐")

    def _refresh_completed_checkbox_heading(self):
        all_rows = set(self.completed_tree.get_children(""))
        self.completed_checked_iids.intersection_update(all_rows)
        all_checked = bool(all_rows) and all_rows <= self.completed_checked_iids
        self.completed_tree.heading("select", text="☑ SELECT ALL" if all_checked else "☐ SELECT ALL")

    def _toggle_completed_select_all(self):
        rows = set(self.completed_tree.get_children(""))
        if rows and rows <= self.completed_checked_iids:
            self.completed_checked_iids.difference_update(rows)
        else:
            self.completed_checked_iids.update(rows)
        for iid in rows:
            self._update_completed_checkbox(iid)
        self._update_completed_count()

    def on_completed_double_click(self, event):
        if self.trocr_training:
            return
        iid = self.completed_tree.identify_row(event.y)
        if not iid or iid not in self.records:
            return
        column = self.completed_tree.identify_column(event.x)
        if column == "#0":
            self._show_completed_evidence(iid)
        elif column == "#2":
            self._start_completed_edit(iid)
        elif column == "#7":
            return "break"

    def _close_completed_edit(self, iid):
        widgets = self.completed_edit_widgets.pop(iid, None)
        if widgets:
            for widget in widgets:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass

    def _remember_completed_draft(self, iid, entry):
        self.completed_edit_drafts[iid] = entry.get()

    def _start_completed_edit(self, iid):
        """Focus the permanently visible, initially blank CHANGE box."""
        if iid not in self.completed_edit_widgets:
            self._position_completed_unverify_buttons()
        widgets = self.completed_edit_widgets.get(iid)
        if widgets:
            widgets[0].focus_set()

    def _cancel_completed_case_edit(self, iid):
        """Discard the draft, leaving the existing confirmed case unchanged."""
        self.completed_edit_drafts.pop(iid, None)
        widgets = self.completed_edit_widgets.get(iid)
        if widgets:
            widgets[0].delete(0, "end")

    def _save_learning_label_for_file(self, source_name, confirmed_number):
        """Keep the ground-truth label consistent after a filename correction."""
        if not CASE_RE.fullmatch(normalize_case_number(confirmed_number)):
            raise ValueError("Case number must begin with 08- before saving learning.")
        if not self.learning_dir:
            return
        changed = False
        for example in self.learning_data.get("examples", []):
            if example.get("source_pdf") == source_name:
                example["confirmed_case_number"] = confirmed_number
                changed = True
        for region in self.learning_data.get("field_regions", []):
            if region.get("source_pdf") == source_name:
                region["confirmed_case_number"] = confirmed_number
                changed = True
        if changed:
            self._persist_learning_data(strict_revision=True)
            self._refresh_learning_stats()

    def _save_completed_case_edit(self, iid, entry):
        record = self.records.get(iid)
        if not record or record.get("exported"):
            self._close_completed_edit(iid)
            return
        if iid in self._export_in_flight_iids:
            self.status.config(text="That file is being sent to COMPLETED; edits are temporarily unavailable.")
            return
        value = entry.get().strip()
        value = normalize_case_number(value)
        if not CASE_RE.fullmatch(value):
            messagebox.showerror(
                "Invalid case number",
                "Only 08- followed by 1–7 case digits is valid. No year will be added or corrected automatically.",
                parent=self.root)
            return

        old_case = str(record.get("confirmed", ""))
        if value == old_case:
            self._cancel_completed_case_edit(iid)
            return
        old_cache = self._completed_thumbnail_path(record)
        if old_cache and old_cache.exists():
            try:
                old_cache.unlink()
            except OSError:
                logging.exception("Could not remove old thumbnail cache after case-number edit")
        record["confirmed"] = value
        # Editing is an explicit human decision, not another OCR inference.
        record["manual_approved"] = True
        record["auto_labeled"] = False
        record["sequence_inferred"] = False
        record["sequence_conflict"] = False
        record["sequence_excluded"] = False
        record["sequence_anchors"] = []
        try:
            self._save_learning_label_for_file(record["path"].name,value)
            record["learning_pending"]=False
        except Exception as exc:
            record["learning_pending"]=True
            self._log("Corrected case retained; learning update pending: %s",exc)
        record["status"]=f"Manual case {value} saved — awaiting SEND ALL SELECTED"
        self._insert_scan_row(iid,record)
        self.completed_edit_drafts.pop(iid, None)
        self._close_completed_edit(iid)
        self._insert_completed_row(iid, record)
        self._save_state()
        self._update_completed_count()
        message = (f"Case number saved as {value} for {record['path'].name}. "
                   "It remains in root until SEND ALL SELECTED.")
        self.status.config(text=message)
        self.completed_send_status.config(text=message)
        self._apply_sequence_consensus()
        # A human correction must remain in COMPLETED until the user presses
        # SEND TO KURT; it must not race an automatic export callback.

    def _completed_xscroll_command(self, *args):
        self.completed_tree.xview(*args)
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _completed_xscrollbar_update(self, first, last):
        self.completed_xscrollbar.set(first, last)
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _completed_scrollbar_command(self, *args):
        self.completed_tree.yview(*args)
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _completed_scrollbar_update(self, first, last):
        self.completed_scrollbar.set(first, last)
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _scroll_completed(self, amount):
        self.completed_tree.yview_scroll(amount, "units")
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _position_completed_unverify_buttons(self):
        """Virtualize the CHANGE widgets and UNVERIFY button for visible rows."""
        if not hasattr(self, "completed_tree"):
            return
        visible = set()
        for iid in self.completed_tree.get_children(""):
            record = self.records.get(iid)
            if not record or not record.get("confirmed"):
                continue
            edit_cell = self.completed_tree.bbox(iid, "#2")
            unverify_cell = self.completed_tree.bbox(iid, "#6")
            if not edit_cell and not unverify_cell:
                continue
            visible.add(iid)
            if edit_cell:
                x, y, w, h = edit_cell
                widgets = self.completed_edit_widgets.get(iid)
                if widgets is None or not widgets[0].winfo_exists():
                    entry = ttk.Entry(self.completed_tree, font=("Segoe UI", 10))
                    # Never prefill with the current number. Drafts survive scrolling.
                    draft = self.completed_edit_drafts.get(iid, "")
                    if draft:
                        entry.insert(0, draft)
                    save = ttk.Button(self.completed_tree, text="Save",
                                      command=lambda key=iid, e=entry: self._save_completed_case_edit(key, e))
                    cancel = ttk.Button(self.completed_tree, text="Cancel",
                                        command=lambda key=iid: self._cancel_completed_case_edit(key))
                    entry.bind("<KeyRelease>", lambda _e, key=iid, e=entry:
                               self._remember_completed_draft(key, e))
                    entry.bind("<Return>", lambda _e, key=iid, e=entry:
                               (self._save_completed_case_edit(key, e), "break")[1])
                    entry.bind("<Escape>", lambda _e, key=iid:
                               (self._cancel_completed_case_edit(key), "break")[1])
                    widgets = (entry, save, cancel)
                    self.completed_edit_widgets[iid] = widgets
                entry, save, cancel = widgets
                disabled = "disabled" if record.get("exported") else "normal"
                entry.configure(state=disabled)
                save.configure(state=disabled)
                cancel.configure(state=disabled)
                entry_h = max(23, min(31, h // 3))
                buttons_h = max(23, min(30, h // 3))
                top = y + max(2, (h - entry_h - buttons_h - 5) // 2)
                entry.place(x=x + 5, y=top, width=max(90, w - 10), height=entry_h)
                button_w = (w - 15) // 2
                save.place(x=x + 5, y=top + entry_h + 5,
                           width=button_w, height=buttons_h)
                cancel.place(x=x + 10 + button_w, y=top + entry_h + 5,
                             width=max(46, w - 15 - button_w), height=buttons_h)
            if unverify_cell:
                x, y, w, h = unverify_cell
                btn = self.completed_unverify_buttons.get(iid)
                if btn is None or not btn.winfo_exists():
                    btn = tk.Button(self.completed_tree, text="UNVERIFY",
                                    fg="#000000", bg="#ee5757", activeforeground="#000000",
                                    activebackground="#ff7878", disabledforeground="#555555",
                                    relief="raised", bd=1, cursor="hand2",
                                    font=("Segoe UI", 9, "bold"),
                                    command=lambda key=iid: self._show_unverify_dialog(key))
                    self.completed_unverify_buttons[iid] = btn
                btn.configure(state="disabled" if record.get("exported") else "normal")
                button_w = max(70, min(88, w - 14))
                button_h = max(26, min(34, h - 12))
                btn.place(x=x + (w - button_w) // 2,
                          y=y + (h - button_h) // 2,
                          width=button_w, height=button_h)
        for iid in list(self.completed_edit_widgets):
            if iid not in visible:
                widgets = self.completed_edit_widgets[iid]
                self.completed_edit_drafts[iid] = widgets[0].get()
                self._close_completed_edit(iid)
        for iid, btn in list(self.completed_unverify_buttons.items()):
            if iid not in visible:
                try:
                    btn.place_forget()
                except tk.TclError:
                    pass
            if iid not in self.records or not self.completed_tree.exists(iid):
                btn.destroy()
                self.completed_unverify_buttons.pop(iid, None)

    def _completed_mousewheel(self, event):
        direction = -1 if event.delta > 0 else 1
        self.completed_tree.yview_scroll(direction * max(1, abs(int(event.delta / 120))), "units")
        self.root.after_idle(self._position_completed_unverify_buttons)
        return "break"

    def _completed_thumbnail_path(self, record):
        if not self.folder:
            return None
        case = str(record.get("confirmed") or "unknown")
        name = record.get("path").name if record.get("path") else "unknown.pdf"
        token = hashlib.sha1(f"{name}|{case}".encode("utf-8")).hexdigest()[:18]
        cache_dir = self.folder / ".case_ocr_thumbnails"
        cache_dir.mkdir(exist_ok=True)
        return cache_dir / f"{token}.png"

    def _make_completed_thumbnail(self, record):
        """Build a small, cached image of the exact OCR field used for the filename."""
        from PIL import Image, ImageDraw, ImageFont, ImageTk
        cache_path = self._completed_thumbnail_path(record)
        if cache_path and cache_path.exists():
            try:
                cached = Image.open(cache_path).convert("RGB")
                cached.thumbnail((190, 58), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(cached)
            except Exception:
                logging.exception("Could not load cached case-number thumbnail")

        self._ensure_preview(record)
        snippet = record.get("snippet")
        crop = None
        if snippet and snippet[1] is not None:
            page = Image.fromarray(snippet[1]).convert("RGB")
            candidates = snippet[4] if len(snippet) > 4 and isinstance(snippet[4], list) else []
            confirmed = str(record.get("confirmed") or "").strip()
            chosen = next((c for c in candidates
                           if str(c.get("number") or "").strip() == confirmed and c.get("rect")), None)
            rect = chosen.get("rect") if chosen else None
            # Prefer the reviewer's green box when the final confirmed value was
            # manually corrected and has no matching red OCR candidate.
            if not rect:
                rect = record.get("manual_crop_rect") or snippet[3]
            # Last resort: any recognized number box, then the corner OCR region.
            if not rect:
                fallback = next((c for c in candidates if c.get("rect")), None)
                rect = fallback.get("rect") if fallback else None
            if rect:
                x0, y0, x1, y1 = [int(round(float(v))) for v in rect]
                pad_x = max(18, int((x1 - x0) * 0.28))
                pad_y = max(12, int((y1 - y0) * 0.45))
                x0 = max(0, x0 - pad_x); y0 = max(0, y0 - pad_y)
                x1 = min(page.width, x1 + pad_x); y1 = min(page.height, y1 + pad_y)
                if x1 > x0 and y1 > y0:
                    crop = page.crop((x0, y0, x1, y1))
            if crop is None:
                # Fallback: display the complete upper-right OCR search zone rather than
                # showing an unrelated full-page region or a blank thumbnail.
                w = min(page.width, int(5 * 72 * 2.2))
                h = min(page.height, int(5 * 72 * 2.2))
                crop = page.crop((page.width - w, 0, page.width, h))
        if crop is None:
            crop = Image.new("RGB", (190, 58), "white")
            draw = ImageDraw.Draw(crop)
            draw.text((6, 20), str(record.get("confirmed") or "Preview unavailable"), fill="black")
        crop.thumbnail((190, 58), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (198, 62), "white")
        x = (tile.width - crop.width) // 2
        y = (tile.height - crop.height) // 2
        tile.paste(crop, (x, y))
        ImageDraw.Draw(tile).rectangle((0, 0, tile.width - 1, tile.height - 1), outline="#999999")
        if cache_path:
            try:
                tile.save(cache_path)
            except Exception:
                logging.exception("Could not save case-number thumbnail cache")
        return ImageTk.PhotoImage(tile)

    def _completed_queue_status(self, record):
        score = float(record.get("score", 0.0) or 0.0)
        match = f"{score:.1f}%" if score > 0 else "—"
        approval = "AUTOMATCH" if record.get("auto_labeled") else "APPROVED"
        return f"{match} | {approval}"

    def _display_original_parent(self, record):
        """Show the rightmost folder segments; never the full network share."""
        path = str(record.get("original_path") or record.get("path") or "")
        pieces = [piece for piece in re.split(r"[\\/]", path) if piece]
        # The file name is already displayed under ORIGINAL NAME.
        parts = pieces[:-1]
        if not parts:
            return "—"
        tail = "\\".join(parts[-2:])
        label = ("…\\" if len(parts) > 2 else "") + tail
        try:
            from tkinter import font as tkfont
            ft = tkfont.nametofont("TkDefaultFont")
            max_px = max(64, int(self.completed_tree.column("original_path", "width")) - 18)
            while label and ft.measure(label) > max_px:
                # Elide the left, retaining the closest folder name and its end.
                label = "…" + label[2:] if label.startswith("…") else "…" + label[1:]
        except (tk.TclError, AttributeError):
            pass
        return label or "…"

    def _schedule_completed_layout(self):
        if getattr(self, "_completed_layout_after", None) is None:
            self._completed_layout_after = self.root.after(90, self._autofit_completed_columns)

    def _autofit_completed_columns(self):
        """Size the columns to their content, share any spare width, center all cells."""
        self._completed_layout_after = None
        if not hasattr(self, "completed_tree") or not self.completed_tree.winfo_exists():
            return
        from tkinter import font as tkfont
        face = tkfont.nametofont("TkDefaultFont")
        columns = ("#0", "case", "edit_case", "file", "original_path", "status", "unverify", "select")
        mins = {"#0": 255, "case": 105, "edit_case": 195, "file": 155,
                "original_path": 135, "status": 150, "unverify": 88, "select": 103}
        maxs = {"#0": 275, "case": 155, "edit_case": 245, "file": 390,
                "original_path": 330, "status": 225, "unverify": 100, "select": 125}
        samples = {key: [] for key in columns}
        for iid in self.completed_tree.get_children(""):
            record = self.records.get(iid)
            if not record:
                continue
            samples["case"].append(str(record.get("confirmed", "")))
            samples["file"].append(Path(record["path"]).name)
            samples["status"].append(self._completed_queue_status(record))
        base = {}
        for col in columns:
            desired = max((face.measure(text) + 28 for text in samples[col]), default=0)
            desired = max(desired, mins[col])
            base[col] = min(maxs[col], desired)
        base["#0"] = 258  # Preserve the enlarged IMAGE thumbnail.
        viewport = max(750, self.completed_tree.winfo_width() - 20)
        spare = max(0, viewport - sum(base.values()))
        for col in ("file", "original_path", "status", "edit_case", "case", "select"):
            add = min(spare, maxs[col] - base[col])
            base[col] += add
            spare -= add
        changed = False
        for col in columns:
            if self.completed_tree.column(col, "width") != base[col]:
                self.completed_tree.column(col, width=base[col], anchor="center")
                changed = True
        if changed:
            for iid in self.completed_tree.get_children(""):
                rec = self.records.get(iid)
                if rec:
                    self.completed_tree.set(iid, "original_path", self._display_original_parent(rec))
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _completed_row_values(self, iid, record):
        return (record.get("confirmed", ""), "", record["path"].name,
                self._display_original_parent(record),
                self._completed_queue_status(record), "",
                "☑" if iid in self.completed_checked_iids else "☐")

    def _insert_completed_row(self, iid, record):
        if self.completed_tree.exists(iid):
            stale = self._completed_thumbnail_path(record)
            if stale and stale.exists():
                try:
                    stale.unlink()
                except Exception:
                    logging.exception("Could not refresh case-number thumbnail cache")
        photo = self._make_completed_thumbnail(record)
        self.completed_photo_refs[iid] = photo
        vals = self._completed_row_values(iid, record)
        if self.completed_tree.exists(iid):
            self.completed_tree.item(iid, image=photo, text="", values=vals)
        else:
            self.completed_tree.insert("", "end", iid=iid, image=photo, text="", values=vals)
        self.root.after_idle(self._position_completed_unverify_buttons)
        self._schedule_completed_layout()
        self._refresh_completed_checkbox_heading()

    def _show_completed_evidence(self, iid):
        """Open the original PDF page with the selected field in green and other OCR detections in red."""
        record = self.records.get(iid)
        if not record:
            return
        self._ensure_preview(record)
        snippet = record.get("snippet")
        if not snippet or snippet[1] is None:
            messagebox.showwarning(
                "Preview unavailable",
                "The original PDF preview could not be rebuilt. Check that the source PDF is still in the selected root folder.",
                parent=self.root)
            return
        try:
            from PIL import Image, ImageTk, ImageDraw
            original = Image.fromarray(snippet[1]).convert("RGB")
            # If saved OCR preview pixels are blank, render the PDF page directly.
            try:
                import numpy as _np
                if float(_np.asarray(original).std()) < 1.0:
                    with pymupdf.open(record["path"]) as _doc:
                        _page_num = max(0, min(int(snippet[0] or 0), len(_doc) - 1))
                        _pix = _doc[_page_num].get_pixmap(
                            matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
                        original = Image.frombytes("RGB", [_pix.width, _pix.height], _pix.samples)
                        record["snippet"] = (
                            _page_num, _np.asarray(original).copy(),
                            snippet[2], snippet[3], snippet[4] if len(snippet) > 4 else [])
                        snippet = record["snippet"]
            except Exception:
                logging.exception("Could not rebuild blank evidence preview from source PDF")
            candidates = snippet[4] if len(snippet) > 4 and isinstance(snippet[4], list) else []
            confirmed = str(record.get("confirmed") or "").strip()
            selected = next((c for c in candidates
                             if str(c.get("number") or "").strip() == confirmed and c.get("rect")), None)
            green_rect = record.get("manual_crop_rect") or (selected.get("rect") if selected else None) or snippet[3]

            win = tk.Toplevel(self.root)
            win.title(f"M.F. Case Evidence — {record['path'].name}")
            win.transient(self.root)
            try:
                win.state("zoomed")
            except tk.TclError:
                win.geometry("1400x900")
            head = ttk.Frame(win, padding=(10, 8))
            head.pack(fill="x")
            case_description = (
                f"Case number: {confirmed or 'Not set'} • SEQUENCE-INFERRED (not OCR)"
                if record.get("sequence_inferred") else
                f"Case number: {confirmed or 'Not set'}")
            ttk.Label(head, text=case_description,
                      font=("Segoe UI", 12, "bold")).pack(side="left")
            score_description = (
                "Sequential filename inference; no OCR confidence assigned"
                if record.get("sequence_inferred") else
                f"OCR detector score: {float(record.get('score') or 0):.1f}%"
                if record.get("score") else "OCR detector score: unavailable")
            ttk.Label(head, text=score_description).pack(side="right")
            ttk.Label(win, text=str(record["path"]), padding=(10, 0)).pack(anchor="w")
            ttk.Label(win, text="GREEN = field used for the filename / learning crop     RED = other detected case-number candidates",
                      foreground="#1b6e36", padding=(10, 5)).pack(anchor="w")
            panel = ttk.Frame(win, padding=(8, 0, 8, 8))
            panel.pack(fill="both", expand=True)
            panel.rowconfigure(0, weight=1); panel.columnconfigure(0, weight=1)
            canvas = tk.Canvas(panel, bg="#333333", highlightthickness=0)
            sx = ttk.Scrollbar(panel, orient="horizontal", command=canvas.xview)
            sy = ttk.Scrollbar(panel, orient="vertical", command=canvas.yview)
            canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            sy.grid(row=0, column=1, sticky="ns")
            sx.grid(row=1, column=0, sticky="ew")
            photo_ref = {"photo": None}
            state = {"scale": 1.0}

            def render():
                avail_w = max(300, canvas.winfo_width() - 20)
                avail_h = max(300, canvas.winfo_height() - 20)
                # Keep full page readable and scrollable; fit to width, capped at 1:1.
                scale = min(avail_w / original.width, 1.0)
                if avail_h / original.height < scale:
                    scale = min(scale, avail_h / original.height)
                state["scale"] = max(0.15, scale)
                w = max(1, int(original.width * state["scale"]))
                h = max(1, int(original.height * state["scale"]))
                shown = original.resize((w, h), Image.Resampling.LANCZOS)
                draw = ImageDraw.Draw(shown)
                if green_rect:
                    x0, y0, x1, y1 = [int(round(float(v) * state["scale"])) for v in green_rect]
                    draw.rectangle((x0, y0, x1, y1), outline="#00e85a", width=max(3, int(3*state["scale"])))
                    draw.text((x0, max(0, y0-18)), confirmed or "SELECTED", fill="#00e85a", stroke_width=2, stroke_fill="#111111")
                chosen_used = False
                for candidate in candidates:
                    rect = candidate.get("rect")
                    if not rect:
                        continue
                    if (not chosen_used and selected is candidate):
                        chosen_used = True
                        continue
                    # Do not draw a red alternative over the selected green location.
                    if green_rect and tuple(map(float, rect)) == tuple(map(float, green_rect)):
                        continue
                    x0, y0, x1, y1 = [int(round(float(v) * state["scale"])) for v in rect]
                    draw.rectangle((x0, y0, x1, y1), outline="#ff3030", width=max(2, int(2*state["scale"])))
                    label = str(candidate.get("number") or "candidate")
                    draw.text((x0, max(0, y0-17)), label, fill="#ff3030", stroke_width=2, stroke_fill="#111111")
                photo = ImageTk.PhotoImage(shown)
                photo_ref["photo"] = photo
                canvas.delete("all")
                canvas.create_image(0, 0, image=photo, anchor="nw")
                canvas.configure(scrollregion=(0, 0, w, h))
                canvas.xview_moveto(0); canvas.yview_moveto(0)

            def render_when_ready(_event=None):
                if not win.winfo_exists():
                    return
                if canvas.winfo_width() <= 2 or canvas.winfo_height() <= 2:
                    win.after(100, render_when_ready)
                    return
                render()

            canvas.bind("<Configure>", render_when_ready)
            win.after(250, render_when_ready)
            win.bind("<Escape>", lambda _e: win.destroy())
        except Exception as exc:
            logging.exception("Could not open completed case evidence viewer")
            messagebox.showerror("Evidence preview failed", str(exc), parent=self.root)

    def _remove_learning_data_for_file(self, source_name):
        """Remove this file's saved learning examples and spatial hints."""
        if not self.learning_dir:
            return
        kept = []
        removed_images = []
        for example in self.learning_data.get("examples", []):
            if example.get("source_pdf") == source_name:
                if example.get("image"):
                    removed_images.append(example.get("image"))
            else:
                kept.append(example)
        self.learning_data["examples"] = kept
        self.learning_data["field_regions"] = [
            region for region in self.learning_data.get("field_regions", [])
            if region.get("source_pdf") != source_name]
        for rel in removed_images:
            try:
                target = (self.learning_dir / rel).resolve()
                examples_root = (self.learning_dir / "examples_v3").resolve()
                if target.is_relative_to(examples_root) and target.is_file():
                    target.unlink()
            except Exception:
                logging.exception("Could not remove unverified learning crop %s", rel)
        try:
            self._persist_learning_data(strict_revision=True)
        except Exception:
            logging.exception("Could not persist removal of unverified learning examples")
        self._refresh_learning_stats()

    def _selected_unverify_ids(self):
        rows = self.completed_tree.get_children("")
        selected = self.completed_checked_iids or set(self.completed_tree.selection())
        return [
            iid for iid in rows
            if iid in selected and iid not in self._export_in_flight_iids
            and self.records.get(iid, {}).get("confirmed")
            and not self.records[iid].get("exported")
        ]

    def _unverify_completed_selected(self):
        selected = self._selected_unverify_ids()
        if not selected or self.export_busy or self.trocr_training:
            return
        if not messagebox.askyesno(
            "Unverify selected files",
            f"Return {len(selected)} selected file(s) to the Scan list? "
            "Their confirmed case numbers, saved learning examples and location hints "
            "will be cleared.",
            parent=self.root,
        ):
            return
        for iid in selected:
            self._unverify_record(iid)
        self.completed_send_status.config(
            text=f"Unverified {len(selected)} file(s). Review them again from the Scan list.")

    def _show_unverify_dialog(self, iid):
        record = self.records.get(iid)
        if not record or record.get("exported"):
            return
        win = tk.Toplevel(self.root)
        win.title("UNVERIFY FILE")
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()
        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="UNVERIFY THIS FILE?", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(body, text=record["path"].name, wraplength=420).pack(anchor="w", pady=(8, 4))
        ttk.Label(body, text=(
            "This returns the PDF to the review list and clears its confirmed case number. "
            "Its saved learning example and location hint will also be removed. "
            "Previously trained model weights are not automatically rolled back."),
            wraplength=440).pack(anchor="w", pady=(2, 14))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        def no():
            win.destroy()
        def yes():
            self._unverify_record(iid)
            win.destroy()
        ttk.Button(buttons, text="UNVERIFY — YES", command=yes).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="NO", command=no).pack(side="right")
        win.bind("<Escape>", lambda _e: no())
        win.bind("<Return>", lambda _e: yes())
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(0,x)}+{max(0,y)}")

    def _unverify_record(self, iid):
        record = self.records.get(iid)
        if not record or record.get("exported"):
            return
        if iid in self._export_in_flight_iids:
            self.status.config(text="That file is being sent to COMPLETED; UNVERIFY is temporarily unavailable.")
            return
        old_thumbnail = self._completed_thumbnail_path(record)
        if old_thumbnail and old_thumbnail.exists():
            try:
                old_thumbnail.unlink()
            except Exception:
                logging.exception("Could not remove stale thumbnail for unverified file")
        if self.completed_tree.exists(iid):
            self.completed_tree.delete(iid)
        self.completed_photo_refs.pop(iid, None)
        self.completed_checked_iids.discard(iid)
        self.completed_edit_drafts.pop(iid, None)
        self._close_completed_edit(iid)
        old_button = self.completed_unverify_buttons.pop(iid, None)
        if old_button:
            old_button.destroy()
        record["confirmed"] = ""
        record["send_after_manual_approval"] = False
        record["manual_approved"] = False
        record["auto_labeled"] = False
        record["sequence_inferred"] = False
        record["sequence_conflict"] = False
        record["sequence_anchors"] = []
        record["sequence_suggestion"] = ""
        # Explicit UNVERIFY is a veto, not permission to silently infer again.
        record["sequence_excluded"] = True
        record["manual_crop_rect"] = None
        record["status"] = "Unverified — returned to review"
        record["completed_iid"] = None
        self._remove_learning_data_for_file(record["path"].name)
        self._insert_scan_row(iid, record)
        self._refresh_review_controls()
        self._update_completed_count()
        self._update_scan_file_count()
        self._save_state()
        self.status.config(text=f"Unverified {record['path'].name}; returned to the review list.")

    def review_selected(self):
        if self.trocr_training:
            return
        ready=set(self._review_ready_ids())
        selected = [
            iid for iid in self.tree.selection()
            if iid in ready and not self.records[iid].get("exported")
            and (not self.folder or Path(self.records[iid]["path"]).parent==Path(self.folder))]
        if not selected:
            messagebox.showinfo(
                "Nothing ready to review",
                "Select a PDF whose scan has finished. You can review completed scans while the remaining PDFs continue processing.")
            return
        self._open_review_queue(selected, 0)

    def review_next(self):
        if self.trocr_training:
            return
        pending = [
            iid for iid in self._review_ready_ids()
            if not self.records[iid].get("confirmed")]
        if not pending:
            messagebox.showinfo(
                "No scanned files ready",
                "No completed OCR results are waiting for review yet. The scanner may still be processing the remaining PDFs.")
            return
        self._open_review_queue(pending, 0)

    def review_completed_selected(self):
        if self.trocr_training:
            return
        selected = list(self.completed_tree.selection())
        if not selected:
            messagebox.showinfo(
                "Select completed files",
                "Select one or more files in Review to inspect and approve them.")
            return
        self._open_review_queue(selected, 0)

    def _open_review_queue(self, iids, index=0):
        ready = set(self._review_ready_ids())
        self.review_queue = [
            iid for iid in iids
            if iid in self.records and
            (iid in ready or self.records[iid].get("confirmed"))
        ]
        if not self.review_queue:
            return
        self._show_review_item(index)

    def _move_to_completed(self, iid, batch=False):
        r = self.records[iid]
        if r.get("sequence_inferred"):
            queue_status = "Confirmed by matching neighbors, OCR score retained — ready for export"
        elif r.get("auto_labeled") and r.get("manual_approved"):
            source=r.get("scan_source")
            if source=="fallback":
                queue_status="100% localized 3x3 OCR — ready for export"
            else:
                queue_status=f"{float(r.get('score') or 0):.1f}% OCR approved — ready for export"
        elif r.get("manual_approved"):
            queue_status = "Approved — ready for export"
        elif r.get("auto_labeled"):
            queue_status = "Auto-detected — review required"
        else:
            queue_status = "Confirmed — ready for export"
        r["status"] = queue_status
        r["export_error"] = ""
        self._insert_completed_row(iid, r)
        r["completed_iid"] = iid
        # Do not hide approved PDFs: the unified list shows COMPLETED.
        self._insert_scan_row(iid,r)
        if not batch:
            self._update_completed_count()
            self._update_scan_file_count()
            self._save_state()
            # _schedule_auto_export only picks machine-approved scans. A human
            # approval stays visible in Completed for a final edit and SEND.
            self._schedule_auto_export()

    def _update_completed_count(self):
        approved = sum(
            1 for r in self.records.values()
            if r.get("confirmed") and r.get("manual_approved") and not r.get("exported")
            and r["path"].is_file())
        needs_review = sum(
            1 for r in self.records.values()
            if r.get("confirmed") and r.get("auto_labeled")
            and not r.get("manual_approved") and not r.get("exported")
            and r["path"].is_file())
        self._refresh_completed_checkbox_heading()
        selected_count = len(self.completed_checked_iids)
        self.completed_count.config(
            text=f"{approved} approved for export • {needs_review} auto-detected awaiting review"
                 f" • {selected_count} selected")
        self.export_btn.config(state="normal" if approved and not self.export_busy else "disabled")
        can_unverify = not self.export_busy and not self.trocr_training and self._selected_unverify_ids()
        self.unverify_selected_btn.config(state="normal" if can_unverify else "disabled")
        pending_review = any(
            r.get("confirmed") and not r.get("manual_approved")
            and not r.get("exported") for r in self.records.values())
        self.review_completed_btn.config(
            state="normal" if pending_review and not self.trocr_training else "disabled")

    def _ensure_preview(self, record):
        existing = record.get("snippet")
        if existing is not None and len(existing) > 1 and existing[1] is not None:
            try:
                sample = np.asarray(existing[1]) if np is not None else None
                # Older queue state can contain an empty/black placeholder image.
                # Re-render those from the original PDF instead of opening a blank viewer.
                if sample is not None and sample.size and float(sample.std()) >= 1.0:
                    return
            except Exception:
                pass
        record["snippet"] = None
        path = record.get("path")
        if not path or not path.is_file() or pymupdf is None or np is None:
            return
        try:
            meta = record.get("preview_meta", {}) or {}
            with pymupdf.open(path) as doc:
                if len(doc) == 0:
                    return
                page_num = max(0, min(int(meta.get("page_num", 0)), len(doc) - 1))
                pix = doc[page_num].get_pixmap(
                    matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
                image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n).copy()
                rect = tuple(meta["rect"]) if meta.get("rect") else None
                label = meta.get("label", "Full page — preview rebuilt from source PDF")
                candidates = meta.get("candidates", [])
                record["snippet"] = (page_num, image, label, rect, candidates)
        except Exception:
            logging.exception("Could not rebuild review preview for %s", path.name)

    def _show_review_item(self, index):
        if not self.review_queue:
            return
        index = max(0, min(index, len(self.review_queue)-1))
        self.review_index = index
        iid = self.review_queue[index]
        r = self.records[iid]
        self._ensure_preview(r)
        visual_candidates = (r.get("snippet")[4]
                             if r.get("snippet") and len(r.get("snippet")) > 4 else [])
        candidate_values = []
        for item in visual_candidates:
            number = item.get("number")
            if number and number not in candidate_values:
                candidate_values.append(number)
        if r.get("candidate") and r.get("candidate") not in candidate_values:
            candidate_values.insert(0, r.get("candidate"))
        sequence_values, sequence_message = self._sequence_hint_for_iid(iid)
        # A two-sided exact agreement is a stronger clue than a truncated OCR
        # candidate. Put that value first in the selector, but keep all OCR
        # readings available as alternatives for reviewer inspection.
        sequence_consensus = (
            len(sequence_values) == 1
            and "both adjacent files read" in sequence_message.lower()
        )
        if sequence_consensus:
            consensus_value = sequence_values[0]
            candidate_values = [consensus_value] + [
                value for value in candidate_values if value != consensus_value]
        else:
            for value in sequence_values:
                if value not in candidate_values:
                    candidate_values.append(value)

        initial_reading = self._initial_review_reading(
            r, candidate_values, sequence_values, sequence_message)

        win = tk.Toplevel(self.root)
        win.title(f"Verify Case Number ({index+1} of {len(self.review_queue)})")
        win.transient(self.root)
        win.grab_set()
        try:
            win.state("zoomed")
        except tk.TclError:
            win.geometry("1400x900")

        # Two-pane review: portrait document on the left, all controls on the right.
        body = ttk.Frame(win, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1, minsize=300)
        body.rowconfigure(0, weight=1)

        preview_panel = ttk.Frame(body)
        preview_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        preview_panel.rowconfigure(0, weight=1)
        preview_panel.columnconfigure(0, weight=1)

        canvas = tk.Canvas(preview_panel, bg="#333", highlightthickness=0)
        sx = ttk.Scrollbar(preview_panel, orient="horizontal", command=canvas.xview)
        sy = ttk.Scrollbar(preview_panel, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")

        controls = ttk.Frame(body, padding=12)
        controls.grid(row=0, column=1, sticky="nsew")
        controls.columnconfigure(0, weight=1)

        ttk.Label(controls, text=f"File {index+1} of {len(self.review_queue)}",
                  font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(controls, text=r["path"].name, wraplength=280).grid(
            row=1, column=0, sticky="w", pady=(0, 12))

        size_button = ttk.Button(controls, text="Restore Window")
        size_button.grid(row=2, column=0, sticky="ew", pady=(0, 14))

        def toggle_size():
            try:
                if win.state() == "zoomed":
                    win.state("normal")
                    win.geometry("1400x900")
                    size_button.config(text="Full Screen")
                else:
                    win.state("zoomed")
                    size_button.config(text="Restore Window")
            except tk.TclError:
                pass
        size_button.config(command=toggle_size)

        ttk.Label(controls, text=f"OCR READ: {float(r.get('score') or 0):.1f}%",
                  font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w")
        candidate_choice_var = tk.StringVar(value=initial_reading)
        candidate_selector = ttk.Combobox(
            controls, textvariable=candidate_choice_var,
            values=candidate_values, state="readonly" if candidate_values else "disabled")
        # Candidate dropdown hidden; red-box selection remains available on the PDF.
        if sequence_message:
            if sequence_consensus and not r.get("manual_approved"):
                sequence_display = (
                    f"{sequence_message} The strongest suggestion ({sequence_values[0]}) "
                    "has been placed in the confirmation field. Verify it against the document before confirming."
                )
            else:
                sequence_display = sequence_message
            ttk.Label(controls, text=sequence_display, wraplength=280,
                      foreground="#8a5a00").grid(row=5, column=0, sticky="w", pady=(0, 8))
            label_row = 6
        else:
            label_row = 5
        ttk.Label(controls, text="Confirmed case number (08- plus 1–7 digits):",
                  wraplength=280).grid(row=label_row, column=0, sticky="w")
        entry = ttk.Entry(controls, font=("Segoe UI", 14))
        entry.grid(row=label_row + 1, column=0, sticky="ew", pady=(5, 4))
        entry.insert(0, initial_reading)
        def use_selected_candidate(_event=None):
            selected_value = candidate_choice_var.get().strip()
            if selected_value:
                entry.delete(0, "end")
                entry.insert(0, selected_value)
        candidate_selector.bind("<<ComboboxSelected>>", use_selected_candidate)
        err = ttk.Label(controls, text="", foreground="red", wraplength=280)
        err.grid(row=label_row + 2, column=0, sticky="w", pady=(0, 12))

        ttk.Separator(controls).grid(row=label_row + 3, column=0, sticky="ew", pady=8)

        nav_buttons = ttk.Frame(controls)
        # Previous/Next controls removed from simplified one-file review.
        nav_buttons.columnconfigure(0, weight=1)
        nav_buttons.columnconfigure(1, weight=1)

        def move(delta):
            win.destroy()
            nxt = index + delta
            if 0 <= nxt < len(self.review_queue):
                self._show_review_item(nxt)

        ttk.Button(nav_buttons, text="◀ Previous",
                   command=lambda: move(-1),
                   state="normal" if index > 0 else "disabled").grid(
                       row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(nav_buttons, text="Next ▶",
                   command=lambda: move(1),
                   state="normal" if index < len(self.review_queue)-1 else "disabled").grid(
                       row=0, column=1, sticky="ew", padx=(4, 0))

        send_after_approval=False
        training_type_var=tk.StringVar(value=r.get("training_type","unsure"))
        type_buttons={}
        def choose_training_type(value):
            current=training_type_var.get()
            training_type_var.set("unsure" if current==value else value)
            for type_name,widget in type_buttons.items():
                active=(training_type_var.get()==type_name)
                widget.config(relief="sunken" if active else "raised",
                              bg="#1456a8" if active else "#ededed",
                              fg="white" if active else "black")
            r["training_type"]=training_type_var.get()
            self._save_state()

        def confirm():
            value=normalize_case_number(entry.get().strip())
            if not CASE_RE.fullmatch(value):
                err.config(text="Case number must start with 08- followed by 1–7 digits.")
                return
            original_ocr=str(r.get("candidate") or "")
            prior_confirmed=str(r.get("confirmed") or "")
            r["training_type"]=training_type_var.get()
            r["confirmed"]=value
            r["manual_approved"]=True
            r["auto_labeled"]=False
            r["sequence_inferred"]=False
            r["sequence_conflict"]=False
            r["scan_ambiguous"]=False
            r["sequence_anchors"]=[]
            r["sequence_excluded"]=False
            r["sequence_suggestion"]=""
            r["export_error"]=""
            r["send_after_manual_approval"]=False
            r["status"]=f"Manual case confirmed as {value} — awaiting SEND ALL SELECTED"
            self._move_to_completed(iid)
            r["status"]=f"Manual case confirmed as {value} — awaiting SEND ALL SELECTED"
            self._insert_scan_row(iid,r)
            if not self._save_state():
                err.config(text="Could not save your correction to the root-folder "
                          "state file. The case remains entered here; check network "
                          "access, then click SAVE REVIEW again.")
                return
            learning_error=None
            try:
                self._save_learning_example(r,value)
            except Exception as exc:
                learning_error=str(exc)
                self._log("Case saved locally, but learning sync failed: %s",exc)
            r["learning_pending"]=bool(learning_error)
            if learning_error:
                r["status"]=(f"Manual case {value} SAVED — learning pending; "
                             "awaiting SEND ALL SELECTED")
            else:
                r["status"]=(f"Manual case {value} SAVED and learning updated — "
                             "awaiting SEND ALL SELECTED")
            self._diag_event("manual_case_confirmation",
                file=Path(r["path"]).name,ocr_candidate=original_ocr,
                previous_confirmed=prior_confirmed,confirmed=value,
                learning_saved=not bool(learning_error),
                transfer="deferred_until_send_all_selected")
            self._insert_scan_row(iid,r)
            self._save_state()
            self.status.config(text=r["status"])
            win.destroy()
            self._apply_sequence_consensus()
            if learning_error:
                messagebox.showwarning("Case number saved",
                    f"{value} is saved and remains in SCAN. Shared learning "
                    f"did not save: {learning_error}\\n\\n"
                    "Reopen REVIEW SELECTED and save again when the learning "
                    "share is available. No PDF was sent.")

        action_row = label_row + 5
        type_row=ttk.Frame(controls)
        type_row.grid(row=action_row,column=0,sticky="ew",pady=(3,8))
        for type_name in ("handwritten","printed"):
            button=tk.Button(type_row,text=type_name.upper(),
                    command=lambda value=type_name:choose_training_type(value),
                    padx=12,pady=6)
            button.pack(side="left",padx=(0,8))
            type_buttons[type_name]=button
        for name,widget in type_buttons.items():
            active=(training_type_var.get()==name)
            widget.config(relief="sunken" if active else "raised",
                          bg="#1456a8" if active else "#ededed",
                          fg="white" if active else "black")
        ttk.Button(controls,text="SAVE REVIEW",
                   command=confirm).grid(row=action_row+1,column=0,sticky="ew",pady=(0,8))
        ttk.Button(controls,text="SKIP",
                   command=win.destroy).grid(row=action_row+2,column=0,sticky="ew")

        ttk.Label(controls,
                  text="SAVE REVIEW stores the corrected 08- case and learning "
                       "without moving any PDF. You can reopen and revise it "
                       "until you press SEND ALL SELECTED.",
                  wraplength=280).grid(row=action_row + 2, column=0, sticky="w", pady=(18, 0))

        # Full-page preview with optional manual case-number crop selection.
        try:
            from PIL import Image, ImageTk, ImageDraw
            if r.get("snippet") is None:
                raise FileNotFoundError("The original PDF is missing or its preview could not be rebuilt.")
            page_num, full_arr, label, auto_rect = r.get("snippet")[:4]
            original = Image.fromarray(full_arr).convert("RGB")

            zoom = {"value": 1.0, "fit": True}
            photo_ref = {"photo": None, "crop": None}
            selection = {
                "enabled": False,
                "drag": None,
                "preview_rect": None
            }

            preview_note = f"Page {page_num+1} • {len(visual_candidates)} case-number box(es) in red • {label}" if label else f"Page {page_num+1} • No OCR candidate"
            ttk.Label(controls, text=preview_note, wraplength=280).grid(
                row=26, column=0, sticky="w", pady=(12, 0))

            crop_heading = ttk.Label(
                controls, text="TRAINING CROP (optional)",
                font=("Segoe UI", 10, "bold"))
            crop_heading.grid(row=27, column=0, sticky="w", pady=(14, 4))

            # Classification is controlled by the two optional buttons above.
            # Optional classification is available as handwritten/printed buttons.
            training_type_box = ttk.Combobox(
                controls, textvariable=training_type_var,
                values=("printed", "handwritten", "unsure"),
                state="readonly", width=16)
            # Hidden legacy control retained for internal state compatibility.
            # Explanation moved to the Instructions window.

            selection_status = ttk.Label(
                controls,
                text="Click a red box to choose that OCR reading. To teach a new location, draw a green box around the field label (Case Number or INCIDENT NO.) and complete case number.",
                wraplength=280)
            selection_status.grid(row=29, column=0, sticky="w", pady=(5, 5))

            def set_selection_mode():
                selection["enabled"] = not selection["enabled"]
                if selection["enabled"]:
                    selection_button.config(text="Finish Box Selection")
                    selection_status.config(
                        text="Draw a green box around the field label (Case Number or INCIDENT NO.) AND the complete case number. The confirmed number is the training answer. Drag inside to move; drag an edge to resize.")
                else:
                    selection_button.config(text="Draw / Redraw Crop Box")
                    selection_status.config(text="Box selection paused. Click any red OCR box to select that reading, or draw a new green training box.")
            selection_button = ttk.Button(
                controls, text="Draw / Redraw Crop Box",
                command=set_selection_mode)
            selection_button.grid(row=30, column=0, sticky="ew", pady=(0, 5))

            def clear_selection():
                selection["preview_rect"] = None
                r["manual_crop_rect"] = None
                r["manual_crop_origin"]=""
                selection["drag"] = None
                redraw()
                update_crop_preview()
                selection_status.config(
                    text="Manual box cleared. The OCR-detected box will be used when available.")
                self._save_state()

            ttk.Button(
                controls, text="Clear Manual Box",
                command=clear_selection).grid(row=31, column=0, sticky="ew")

            crop_preview_row = ttk.Frame(controls)
            crop_preview_row.grid(row=32, column=0, sticky="ew", pady=(8, 0))
            crop_preview_label = ttk.Label(
                crop_preview_row, text="Crop preview will appear here.", anchor="center")
            crop_preview_label.pack(side="left", fill="x", expand=True)
            score_value = float(r.get("score", 0.0) or 0.0)
            review_score_label = ttk.Label(
                crop_preview_row,
                text=(f"OCR score\n{score_value:.1f}%" if score_value else "OCR score\n—"),
                font=("Segoe UI", 10, "bold"), anchor="center", justify="center")
            review_score_label.pack(side="right", padx=(8, 4))

            def redraw():
                avail_w = max(200, canvas.winfo_width()-24)
                avail_h = max(200, canvas.winfo_height()-24)
                if zoom["fit"]:
                    zoom["value"] = min(
                        avail_w/original.width, avail_h/original.height, 1.0)
                scale = zoom["value"]
                w = max(1, int(original.width*scale))
                h = max(1, int(original.height*scale))
                resized = original.resize((w, h), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(resized)
                photo_ref["photo"] = photo
                canvas.delete("all")
                canvas.create_image(0, 0, image=photo, anchor="nw")
                canvas.configure(scrollregion=(0, 0, w, h))

                # Red = every case-number-shaped OCR detection. Green = the
                # reviewer-selected/drawn training region.
                if visual_candidates:
                    for candidate_box in visual_candidates:
                        rect = candidate_box.get("rect")
                        if not rect:
                            continue
                        ax0, ay0, ax1, ay1 = rect
                        canvas.create_rectangle(
                            ax0*scale, ay0*scale, ax1*scale, ay1*scale,
                            outline="#ff3333", width=2, dash=(5, 3))
                        label_text = candidate_box.get("number", "")
                        if label_text:
                            canvas.create_text(
                                ax0*scale, max(2, ay0*scale-8), anchor="sw",
                                text=label_text, fill="#ff3333",
                                font=("Segoe UI", max(8, int(9*scale)), "bold"))
                elif auto_rect:
                    ax0, ay0, ax1, ay1 = auto_rect
                    canvas.create_rectangle(
                        ax0*scale, ay0*scale, ax1*scale, ay1*scale,
                        outline="#ff3333", width=2, dash=(5, 3))
                manual_rect = selection["preview_rect"] or r.get("manual_crop_rect")
                if manual_rect:
                    mx0, my0, mx1, my1 = manual_rect
                    canvas.create_rectangle(
                        mx0*scale, my0*scale, mx1*scale, my1*scale,
                        outline="#00ff66", width=3)
                    handle = max(4, 6*scale)
                    for hx, hy in ((mx0,my0),(mx1,my0),(mx0,my1),(mx1,my1)):
                        canvas.create_rectangle(
                            hx*scale-handle, hy*scale-handle,
                            hx*scale+handle, hy*scale+handle,
                            fill="#00ff66", outline="#003311")
                if zoom["fit"]:
                    canvas.xview_moveto(0)
                    canvas.yview_moveto(0)

            def update_crop_preview():
                manual_rect = selection["preview_rect"] or r.get("manual_crop_rect")
                rect_to_show = manual_rect or auto_rect
                if not rect_to_show:
                    crop_preview_label.config(
                        image="", text="No crop box yet. Draw one around the case number.")
                    photo_ref["crop"] = None
                    return
                x0, y0, x1, y1 = [int(v) for v in rect_to_show]
                x0=max(0,min(original.width,x0)); x1=max(0,min(original.width,x1))
                y0=max(0,min(original.height,y0)); y1=max(0,min(original.height,y1))
                if x1 <= x0 or y1 <= y0:
                    crop_preview_label.config(image="", text="Crop box is empty.")
                    photo_ref["crop"] = None
                    return
                crop = original.crop((x0,y0,x1,y1))
                crop.thumbnail((260,110),Image.Resampling.LANCZOS)
                crop_photo = ImageTk.PhotoImage(crop)
                photo_ref["crop"] = crop_photo
                crop_preview_label.config(
                    image=crop_photo,
                    text=f"{'Manual' if manual_rect else 'OCR'} crop • {x1-x0} × {y1-y0} px",
                    compound="top")
                r["training_type"] = training_type_var.get()

            def canvas_to_image(event):
                scale = max(0.01, zoom["value"])
                x = canvas.canvasx(event.x) / scale
                y = canvas.canvasy(event.y) / scale
                return (
                    max(0.0, min(float(original.width), x)),
                    max(0.0, min(float(original.height), y)))

            def apply_candidate_selection(number):
                chosen = next((item for item in visual_candidates
                               if item.get("number") == number), None)
                if not number:
                    return
                current_case=normalize_case_number(entry.get().strip())
                already_verified=bool(r.get("manual_approved") and
                                      not r.get("auto_labeled"))
                manually_changed=bool(current_case and
                                      current_case!=initial_reading)
                if not (already_verified or manually_changed):
                    entry.delete(0,"end")
                    entry.insert(0,number)
                candidate_choice_var.set(number)

                if chosen:
                    # This is a real OCR candidate with a corresponding red box.
                    r["candidate"] = number
                    r["score"] = float(chosen.get("score") or 0.0)
                    review_score_label.config(
                        text=f"OCR score\n{r['score']:.1f}%" if r["score"] else "OCR score\n—")
                    r["engines"] = list(chosen.get("engines") or [])
                    families = {str(e).split("-", 1)[0] for e in r["engines"]}
                    r["engine_agreement"] = (
                        "EasyOCR" in families and "TrOCR" in families)
                    rect = chosen.get("rect")
                    if rect:
                        x0, y0, x1, y1 = [int(v) for v in rect]
                        pad_x = max(24, int((x1-x0)*0.55))
                        pad_y = max(12, int((y1-y0)*0.45))
                        r["manual_crop_rect"] = (
                            max(0, x0-pad_x), max(0, y0-pad_y),
                            min(original.width, x1+int(pad_x*0.25)),
                            min(original.height, y1+pad_y))
                        r["manual_crop_origin"]="candidate"
                        selection["preview_rect"] = None
                    selection_status.config(
                        text=(f"Selected OCR box {number} for crop learning. "
                              f"Confirmed case remains {entry.get().strip()}. "
                              "Typing a correction has priority over OCR box selection. "
                              "You can redraw the training crop if needed."))
                    redraw()
                    update_crop_preview()
                elif number in sequence_values:
                    # A sequence suggestion is not an OCR read. Do not copy its
                    # value into r['candidate'], alter OCR confidence, or claim
                    # engine agreement. It only pre-fills the human confirmation.
                    selection_status.config(
                        text=f"Selected sequence suggestion {number}. This is based on neighboring files, not an OCR detection. Verify the actual digits and the green training crop before confirming.")
                self._save_state()

            def on_candidate_dropdown(event=None):
                apply_candidate_selection(candidate_choice_var.get())

            candidate_selector.bind("<<ComboboxSelected>>", on_candidate_dropdown)

            def begin_box(event):
                if not selection["enabled"]:
                    x, y = canvas_to_image(event)
                    # Select the smallest red candidate box under the click.
                    matches = []
                    for candidate_box in visual_candidates:
                        rect = candidate_box.get("rect")
                        if not rect:
                            continue
                        x0, y0, x1, y1 = [float(v) for v in rect]
                        if x0 <= x <= x1 and y0 <= y <= y1:
                            area = max(1.0, (x1-x0)*(y1-y0))
                            matches.append((area, candidate_box))
                    if matches:
                        _area, chosen = min(matches, key=lambda item: item[0])
                        apply_candidate_selection(chosen.get("number", ""))
                    return "break"
                x, y = canvas_to_image(event)
                existing = r.get("manual_crop_rect")
                tolerance = max(8.0, 10.0 / max(zoom["value"], 0.1))
                mode = "draw"
                edges = set()
                start_rect = None
                if existing:
                    x0,y0,x1,y1 = existing
                    within = x0 <= x <= x1 and y0 <= y <= y1
                    if within:
                        near_left = abs(x-x0) <= tolerance
                        near_right = abs(x-x1) <= tolerance
                        near_top = abs(y-y0) <= tolerance
                        near_bottom = abs(y-y1) <= tolerance
                        if near_left: edges.add("left")
                        if near_right: edges.add("right")
                        if near_top: edges.add("top")
                        if near_bottom: edges.add("bottom")
                        if edges:
                            mode = "resize"
                            start_rect = tuple(existing)
                        else:
                            mode = "move"
                            start_rect = tuple(existing)
                selection["drag"] = {
                    "mode": mode, "start": (x,y),
                    "start_rect": start_rect, "edges": edges}
                if mode == "draw":
                    selection["preview_rect"] = (x,y,x,y)
                canvas.focus_set()
                redraw()
                return "break"

            def drag_box(event):
                drag = selection.get("drag")
                if not selection["enabled"] or not drag:
                    return
                x,y = canvas_to_image(event)
                sx,sy = drag["start"]
                if drag["mode"] == "draw":
                    rect_now = (min(sx,x), min(sy,y), max(sx,x), max(sy,y))
                elif drag["mode"] == "move":
                    x0,y0,x1,y1 = drag["start_rect"]
                    dx,dy=x-sx,y-sy
                    width,height=x1-x0,y1-y0
                    nx0=max(0.0,min(original.width-width,x0+dx))
                    ny0=max(0.0,min(original.height-height,y0+dy))
                    rect_now=(nx0,ny0,nx0+width,ny0+height)
                else:
                    x0,y0,x1,y1=drag["start_rect"]
                    dx,dy=x-sx,y-sy
                    edges=drag["edges"]
                    if "left" in edges: x0=max(0.0,min(x1-1,x0+dx))
                    if "right" in edges: x1=min(float(original.width),max(x0+1,x1+dx))
                    if "top" in edges: y0=max(0.0,min(y1-1,y0+dy))
                    if "bottom" in edges: y1=min(float(original.height),max(y0+1,y1+dy))
                    rect_now=(x0,y0,x1,y1)
                selection["preview_rect"]=tuple(int(round(v)) for v in rect_now)
                redraw()
                update_crop_preview()
                return "break"

            def finish_box(event):
                if not selection["enabled"] or not selection.get("drag"):
                    return
                drag=selection["drag"]
                selection["drag"]=None
                selected=selection.get("preview_rect")
                if selected:
                    x0,y0,x1,y1=selected
                    if x1-x0 >= 35 and y1-y0 >= 10:
                        r["manual_crop_rect"]=selected
                        r["manual_crop_origin"]="drawn"
                        r["training_type"]=training_type_var.get()
                        selection_status.config(
                            text=f"Manual crop selected: {x1-x0} × {y1-y0} pixels. Include the field label and complete case number in this box.")
                        self._save_state()
                    else:
                        selection["preview_rect"]=None
                        selection_status.config(
                            text="That box is too small. Include INCIDENT NO. and the full case number in a larger box.")
                redraw()
                update_crop_preview()
                return "break"

            def on_canvas_resize(event):
                if zoom["fit"]:
                    redraw()

            def on_wheel(event):
                if event.state & 0x0004:
                    zoom["fit"] = False
                    zoom["value"] = max(0.15, min(
                        4.0, zoom["value"]*(1.15 if event.delta > 0 else 1/1.15)))
                    redraw()
                else:
                    canvas.yview_scroll(int(-event.delta/120), "units")
                return "break"

            def on_linux_up(event):
                if event.state & 0x0004:
                    zoom["fit"] = False
                    zoom["value"] = min(4.0, zoom["value"]*1.15)
                    redraw()
                else:
                    canvas.yview_scroll(-3, "units")
                return "break"

            def on_linux_down(event):
                if event.state & 0x0004:
                    zoom["fit"] = False
                    zoom["value"] = max(0.15, zoom["value"]/1.15)
                    redraw()
                else:
                    canvas.yview_scroll(3, "units")
                return "break"

            canvas.bind("<Configure>", on_canvas_resize)
            canvas.bind("<MouseWheel>", on_wheel)
            canvas.bind("<Button-4>", on_linux_up)
            canvas.bind("<Button-5>", on_linux_down)
            canvas.bind("<ButtonPress-1>", begin_box)
            canvas.bind("<B1-Motion>", drag_box)
            canvas.bind("<ButtonRelease-1>", finish_box)
            def set_training_type(event=None):
                r["training_type"] = training_type_var.get()
                self._save_state()
                update_crop_preview()
            training_type_box.bind("<<ComboboxSelected>>", set_training_type)

            win.update_idletasks()
            redraw()
            update_crop_preview()
        except Exception as exc:
            ttk.Label(controls, text=f"Preview unavailable: {exc}",
                      wraplength=280).grid(row=26, column=0, sticky="w")

        # Pressing Enter in the case-number field confirms and advances the queue.
        entry.bind("<Return>", lambda event: (confirm(), "break")[1])
        entry.focus_set()

    def refresh_completed_list(self):
        """Reconcile the UI with actual unsent PDFs; preserve files and history."""
        if self.busy or self.trocr_training:
            messagebox.showinfo("Refresh unavailable", "The list can be refreshed when processing finishes.")
            return
        visible_rows = set()
        for iid, record in self.records.items():
            source = record.get("path")
            if (record.get("confirmed") and not record.get("exported")
                    and source is not None and source.is_file()):
                visible_rows.add(iid)
        for iid in list(self.completed_tree.get_children("")):
            if iid not in visible_rows:
                self.completed_tree.delete(iid)
                self.completed_photo_refs.pop(iid, None)
                self.completed_checked_iids.discard(iid)
                self.completed_edit_drafts.pop(iid, None)
                self._close_completed_edit(iid)
                btn = self.completed_unverify_buttons.pop(iid, None)
                if btn is not None:
                    btn.destroy()
                if iid in self.records:
                    self.records[iid]["completed_iid"] = None
        for iid in visible_rows:
            record = self.records[iid]
            if not self.completed_tree.exists(iid):
                self._insert_completed_row(iid, record)
            else:
                self.completed_tree.item(iid, values=self._completed_row_values(iid, record))
            record["completed_iid"] = iid
        self._update_completed_count()
        self.root.after_idle(self._position_completed_unverify_buttons)
        self.status.config(text=f"Completed list refreshed: {len(visible_rows)} unsent PDF(s).")

    def _eligible_for_kurt(self, iid, rec, automatic=False):
        """Transfer only verified PDFs still in root; keep explicit OCR conflicts
        in review unless a person confirmed the corrected case number.
        """
        human_approved = bool(rec.get("manual_approved")) and not bool(rec.get("auto_labeled"))
        if (not self.folder or not rec.get("confirmed")
                or not rec.get("manual_approved") or rec.get("exported")
                or not CASE_RE.fullmatch(str(rec.get("confirmed") or ""))):
            return False
        # Every verified COMPLETED record may be transferred after scanning.
        # Records without confirmed+manual_approved are rejected above.
        if (rec.get("sequence_conflict") or rec.get("scan_ambiguous")) and not human_approved:
            return False
        path = rec.get("path")
        if path is None or not Path(path).is_file():
            return False
        # Do not send a record from a different folder into the current Kurt
        # destination if the user changed folders while an after() was pending.
        if Path(path).parent.resolve() != Path(self.folder).resolve():
            return False
        # Do not send while a scan is still building sequence evidence.
        if self.busy:
            return False
        if automatic and rec.get("export_error"):
            return False
        return True

    def _schedule_auto_export(self):
        """Do not auto-transfer. User must press SEND ALL SELECTED."""
        return None

    def _run_scheduled_auto_export(self):
        """Legacy delayed callback cannot cause a PDF transfer."""
        self._auto_export_after=None
        return None

    def export_selected(self):
        """Kept as a compatibility alias; Completed now has one SEND button."""
        return self.export()

    def _unsaved_completed_changes(self, selected_set):
        """Prevent exporting an old case number while CHANGE contains a draft."""
        pending = []
        for iid in self.completed_tree.get_children(""):
            if selected_set is not None and iid not in selected_set:
                continue
            record = self.records.get(iid)
            if not record or record.get("exported"):
                continue
            widgets = self.completed_edit_widgets.get(iid)
            if widgets and widgets[0].winfo_exists():
                current = widgets[0].get().strip()
            else:
                current = self.completed_edit_drafts.get(iid, "").strip()
            if current:
                pending.append(iid)
        return pending

    def export(self, selected_iids=None, automatic=False):
        """Move verified root PDFs ONLY for a user-initiated SEND selection."""
        if automatic:
            self._diag_event("automatic_export_blocked",
                             reason="explicit_send_all_selected_required")
            return
        if self.export_busy:
            self._export_reschedule = True
            self.status.config(text="PDF transfer in progress. Additional approvals will send afterward.")
            return
        if not self.folder or self.busy or self.trocr_training:
            if not automatic:
                message = "Sending waits until the scan and sequence review finish."
                self.status.config(text=message)
                self.completed_send_status.config(text=message)
            return
        if automatic:
            selected_set = None  # never let UI checkboxes suppress approved auto-send
        elif selected_iids is not None:
            selected_set = set(selected_iids)
        else:
            checked = set(self.completed_tree.get_children("")) & self.completed_checked_iids
            selected_set = checked if checked else None
        if not automatic:
            drafts = self._unsaved_completed_changes(selected_set)
            if drafts:
                message = (f"Save or Cancel the CHANGE entry for {len(drafts)} selected "
                           "file(s) before SEND TO KURT. Nothing was sent.")
                self.status.config(text=message)
                self.completed_send_status.config(text=message)
                self._start_completed_edit(drafts[0])
                return
        approved = [
            (iid, Path(r["path"]), str(r["confirmed"]))
            for iid, r in self.records.items()
            if (selected_set is None or iid in selected_set)
            and self._eligible_for_kurt(iid, r, automatic=automatic)
        ]
        # Preserve the original scan order when numbering multiple pages
        # belonging to the same case (Page 2 must precede Page 10).
        approved.sort(key=lambda item: self._natural_pdf_sort_key(item[1]))
        if not approved:
            if not automatic:
                message = "No eligible approved files to send to COMPLETED."
                self.status.config(text=message)
                self.completed_send_status.config(text=message)
            return
        if not automatic:
            # A manual SEND retries prior transfers that failed, without popups.
            for iid, _, _ in approved:
                self.records[iid]["export_error"] = ""
        self.export_busy = True
        self._export_in_flight_iids = {iid for iid, _, _ in approved}
        self._update_completed_count()
        message = f"Sending {len(approved)} approved PDF(s) to COMPLETED…"
        self.status.config(text=message)
        self.completed_send_status.config(text=message)
        target = self.folder / "COMPLETED"
        threading.Thread(
            target=self._export_worker, args=(target, approved),
            name="MFCase-PDFTransfer", daemon=True).start()

    def _export_worker(self, destination_folder, approved):
        """One writer serializes original PDF moves, collision suffixes, and errors."""
        try:
            destination_folder.mkdir(parents=True, exist_ok=True)
            reserved = {p.name.casefold() for p in destination_folder.iterdir() if p.is_file()}
            for iid, source, case in approved:
                case = _mfcase_export_case_name(case)
                filename = f"{case}.pdf"
                counter = 1
                destination = None
                reserved_by_us = False
                try:
                    if not source.is_file():
                        raise FileNotFoundError(f"Original PDF is missing: {source.name}")
                    # O_EXCL reserves a unique filename atomically, including
                    # when coworkers export the same case to the same share.
                    while True:
                        if filename.casefold() in reserved:
                            filename = f"{case} ({counter}).pdf"
                            counter += 1
                            continue
                        destination = destination_folder / filename
                        try:
                            handle = os.open(str(destination), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                            os.close(handle)
                            reserved_by_us = True
                            break
                        except FileExistsError:
                            reserved.add(filename.casefold())
                            filename = f"{case} ({counter}).pdf"
                            counter += 1
                    # The source and COMPLETED are on the same volume, so an
                    # atomic rename replaces only our own zero-byte reservation.
                    os.replace(str(source), str(destination))
                    self._diag_event("transfer_result",file=source.name,
                         source_file=source.name,destination_file=destination.name,
                         case=case,outcome="moved")
                    reserved_by_us = False
                    try:
                        # Refresh Windows Explorer "Date modified" when MFCase
                        # finishes processing; PDF contents remain unchanged.
                        os.utime(destination, None)
                    except OSError as metadata_error:
                        self._log(
                            "Transfer succeeded, but Date modified could not be "
                            "updated for %s: %s", destination, metadata_error)
                except Exception as exc:
                    if reserved_by_us and destination is not None:
                        try:
                            if destination.stat().st_size == 0:
                                destination.unlink()
                        except OSError:
                            self._log("Could not clean reserved filename %s", destination)
                    self._diag_event("transfer_result",file=source.name,
                         destination_file=str(destination) if destination else None,
                         outcome="failed",error=repr(exc))
                    self.queue.put(("export_result", iid, source.name, "", str(exc)))
                else:
                    reserved.add(filename.casefold())
                    self.queue.put(("export_result", iid, source.name, str(destination), ""))
        except Exception as exc:
            # Keep every untouched source in the queue for a later retry.
            for iid, source, _case in approved:
                self.queue.put(("export_result", iid, source.name, "", str(exc)))
        finally:
            self.queue.put(("export_done",))

    def _apply_export_result(self, iid, source_name, destination, error):
        """Update record and Tk widgets only after the worker confirms a move."""
        rec = self.records.get(iid)
        if not rec:
            return
        self._export_in_flight_iids.discard(iid)
        if error:
            rec["export_error"] = error
            rec["status"] = f"Transfer failed: {error}"
            self._log("PDF transfer failed for %s: %s", source_name, error)
            message = (f"Transfer failed for {source_name}: {error}. "
                       "Source retained; select COMPLETED row and click SEND ALL SELECTED.")
            self.status.config(text=message)
            self.completed_send_status.config(text=message)
            if self.completed_tree.exists(iid):
                self.completed_tree.item(iid, values=self._completed_row_values(iid, rec))
            self._insert_scan_row(iid,rec)
        else:
            target = Path(destination)
            rec["original_name"] = rec.get("original_name") or source_name
            rec["original_path"] = rec.get("original_path") or str(rec["path"])
            rec["path"] = target
            rec["exported"] = True
            rec["send_after_manual_approval"] = False
            rec["export_error"] = ""
            rec["export_name"] = target.name
            rec["status"] = f"Moved to COMPLETED: {target.name}"
            if self.completed_tree.exists(iid):
                self.completed_tree.delete(iid)
            # Physical move succeeded: exclude this non-root PDF from SCAN.
            if self.tree.exists(iid):
                self.tree.delete(iid)
            self.completed_photo_refs.pop(iid, None)
            self.completed_checked_iids.discard(iid)
            self.scan_checked_iids.discard(iid)
            self.completed_edit_drafts.pop(iid, None)
            self._close_completed_edit(iid)
            button = self.completed_unverify_buttons.pop(iid, None)
            if button is not None:
                button.destroy()
            rec["completed_iid"] = None
            message = f"Moved to COMPLETED: {target.name} (from {source_name})"
            self.status.config(text=message)
            self.completed_send_status.config(text=message)
        self._save_state()
        self._update_completed_count()
        self._update_scan_file_count()


def main():
    root=tk.Tk(); App(root); root.mainloop()
if __name__=="__main__": main()
