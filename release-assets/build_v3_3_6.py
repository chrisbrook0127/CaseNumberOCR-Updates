import hashlib
import re
import sys
import zipfile
from pathlib import Path

BASE = Path(sys.argv[1])
OUT = Path(sys.argv[2])

def replace_method(source, name, replacement):
    marker = f"    def {name}("
    start = source.find(marker)
    if start < 0:
        raise RuntimeError(f"Could not find method {name}")
    match = re.search(r"(?m)^    (?:async )?def [A-Za-z_]\w*\(", source[start + len(marker):])
    end = start + len(marker) + match.start() if match else len(source)
    return source[:start] + replacement.rstrip() + "\n\n" + source[end:]

def replace_method_block(source, start_name, end_name, replacement):
    start = source.find(f"    def {start_name}(")
    end = source.find(f"    def {end_name}(", start + 1)
    if start < 0 or end < 0:
        raise RuntimeError(f"Could not find method block {start_name}..{end_name}")
    return source[:start] + replacement.rstrip() + "\n\n" + source[end:]


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)

with zipfile.ZipFile(BASE, "r") as z:
    files = {Path(n).name: z.read(n) for n in z.namelist() if not n.endswith("/")}

if "case_number_ocr.py" not in files or "updater_apply.py" not in files:
    raise RuntimeError("Base updater package is missing required application files.")

src = files["case_number_ocr.py"].decode("utf-8")
if 'self.app_version = "3.3.5"' not in src:
    raise RuntimeError("Expected V3.3.5 source was not found.")

# Internal operation controls. Pause is cooperative at safe, per-file/per-sample boundaries.
src = replace_once(
    src,
    "        self.pause_event = threading.Event()\n        self.pause_event.set()\n",
    "        self.pause_event = threading.Event()\n        self.pause_event.set()\n"
    "        self.stop_event = threading.Event()\n"
    "        self.learning_pause_event = threading.Event()\n"
    "        self.learning_pause_event.set()\n"
    "        self.learning_stop_event = threading.Event()\n",
    "scan and learning control events"
)
src = src.replace(
    "        self.training_close_button = None\n",
    "        self.training_close_button = None\n"
    "        self.training_pause_button = None\n"
    "        self.training_stop_button = None\n",
    1
)
src = src.replace('self.app_version = "3.3.5"', 'self.app_version = "3.3.6"', 1)

ui = r'''    def _ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 2))
        brand = ttk.Label(header, text="M.F.", font=("Segoe UI", 18, "bold"),
                          foreground="#ffffff", background="#1456a8", padding=(10, 3))
        brand.pack(side="left", padx=(0, 10))
        title_stack = ttk.Frame(header)
        title_stack.pack(side="left", fill="x", expand=True)
        ttk.Label(title_stack, text="Case Number OCR & Renamer — V3.3.6",
                  font=("Segoe UI", 17, "bold")).pack(anchor="w")

        # Coworker-facing navigation and learning action.
        self.select_files_nav_btn = tk.Button(
            header, text="SELECT FILES", command=lambda: self._show_main_view("select"),
            bg="#0d4388", fg="white", activebackground="#0d4388", activeforeground="white",
            relief="sunken", bd=2, padx=14, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.completed_nav_btn = tk.Button(
            header, text="COMPLETED", command=lambda: self._show_main_view("completed"),
            bg="#1456a8", fg="white", activebackground="#0d4388", activeforeground="white",
            relief="raised", bd=2, padx=14, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.update_btn = ttk.Button(header, text="UPDATE", command=self.check_for_updates)
        self.finish_batch_btn = ttk.Button(
            header, text="UPDATE LEARNING", command=self._finish_import_batch, state="disabled")
        self.update_btn.pack(side="right", padx=(8, 0), ipadx=12, ipady=6)
        self.finish_batch_btn.pack(side="right", padx=(8, 0), ipadx=10, ipady=6)
        self.completed_nav_btn.pack(side="right", padx=(6, 0))
        self.select_files_nav_btn.pack(side="right", padx=(6, 0))
        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(8, 7))

        self.view_container = ttk.Frame(main)
        self.view_container.pack(fill="both", expand=True)
        self.review_tab = ttk.Frame(self.view_container, padding=8)
        self.completed_tab = ttk.Frame(self.view_container, padding=8)
        for view in (self.review_tab, self.completed_tab):
            view.place(relx=0, rely=0, relwidth=1, relheight=1)

        # The selected year stays internal at the established default of 08.
        # Coworkers only choose the folder and run/review the files.
        self.year_var = tk.StringVar(value="08")
        self.auto_label_var.set(True)
        self.auto_threshold_var.set("95")

        folder_bar = ttk.Frame(self.review_tab)
        folder_bar.pack(fill="x", pady=(0, 8))
        self.choose_folder_btn = ttk.Button(
            folder_bar, text="CHOOSE FOLDER", command=self.choose_folder)
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
            action_bar, text="REVIEW FILES", command=self.review_next, state="disabled")
        self.review_btn.pack(side="left", padx=5, ipadx=8, ipady=3)
        self.review_all_btn = self.review_btn
        self.refresh_root_btn = ttk.Button(
            action_bar, text="REFRESH", command=self.refresh_root_folder)
        self.refresh_root_btn.pack(side="left", padx=5, ipadx=8, ipady=3)

        pane = ttk.Frame(self.review_tab)
        pane.pack(fill="both", expand=True)
        cols = ("file", "candidate", "score", "status")
        self.tree = ttk.Treeview(pane, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("file", text="PDF FILE")
        self.tree.column("file", width=620, minwidth=220, stretch=True)
        self.tree.heading("candidate", text="Candidate")
        self.tree.column("candidate", width=1, minwidth=0, stretch=False)
        self.tree.heading("score", text="Score")
        self.tree.column("score", width=1, minwidth=0, stretch=False)
        self.tree.heading("status", text="STATUS")
        self.tree.column("status", width=240, minwidth=120, stretch=True)
        self.tree["displaycolumns"] = ("file", "status")
        sy = ttk.Scrollbar(pane, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sy.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double_click)

        self.progress = ttk.Progressbar(self.review_tab, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 4))
        self.status = ttk.Label(self.review_tab, text="Choose a folder, then click SCAN.")
        self.status.pack(anchor="w")

        # Internal status labels remain available to the existing diagnostics,
        # but are not placed on the coworker screen.
        self.learning_stats_label = ttk.Label(self.review_tab, text="")
        self.model_status_label = ttk.Label(self.review_tab, text="")
        self.learning_type_stats_label = ttk.Label(self.review_tab, text="")
        self.auto_eligibility_label = ttk.Label(self.review_tab, text="")
        self.scan_file_count_label = ttk.Label(self.review_tab, text="")

        completed_header = ttk.Frame(self.completed_tab)
        completed_header.pack(fill="x", pady=(0, 5))
        ttk.Label(completed_header, text="Completed Files / Ready for Export",
                  font=("Segoe UI", 12, "bold")).pack(side="left")
        self.export_btn = ttk.Button(
            completed_header, text="SEND TO KURT", command=self.export, state="disabled")
        self.export_btn.pack(side="right", padx=(8, 0))
        ttk.Label(
            self.completed_tab,
            text="Double-click a case image to inspect it. Double-click the case number to make a correction.",
            wraplength=1100).pack(anchor="w", pady=(0, 5))
        cpane = ttk.Frame(self.completed_tab)
        cpane.pack(fill="both", expand=True)
        ccols = ("case", "edit_case", "actions", "file", "status")
        style = ttk.Style(self.root)
        try:
            one_inch = max(72, int(self.root.winfo_fpixels("1i")))
        except Exception:
            one_inch = 80
        style.configure("Completed.Treeview", rowheight=one_inch)
        self.completed_tree = ttk.Treeview(
            cpane, columns=ccols, show=("tree", "headings"),
            selectmode="extended", style="Completed.Treeview")
        self.completed_tree.heading("#0", text="Case-number image")
        self.completed_tree.column("#0", width=210, minwidth=170, stretch=False, anchor="center")
        for col, title, width in [
            ("case", "Filename case #", 145),
            ("edit_case", "Change case # (double-click)", 175),
            ("actions", "Save / Cancel", 125),
            ("file", "Original PDF filename", 390),
            ("status", "Queue status", 245)]:
            self.completed_tree.heading(col, text=title)
            self.completed_tree.column(col, width=width, minwidth=90)
        self.completed_tree.bind("<Double-1>", self.on_completed_double_click)
        self.completed_tree.bind("<MouseWheel>", self._completed_mousewheel)
        self.completed_tree.bind("<Button-4>", lambda e: self.completed_tree.yview_scroll(-3, "units"))
        self.completed_tree.bind("<Button-5>", lambda e: self.completed_tree.yview_scroll(3, "units"))
        csy = ttk.Scrollbar(cpane, orient="vertical", command=self.completed_tree.yview)
        self.completed_tree.configure(yscrollcommand=csy.set)
        self.completed_tree.pack(side="left", fill="both", expand=True)
        csy.pack(side="right", fill="y")
        cbar = ttk.Frame(self.completed_tab)
        cbar.pack(fill="x", pady=(10, 0))
        self.completed_count = ttk.Label(cbar, text="0 files ready for export")
        self.completed_count.pack(side="left")
        self.review_completed_btn = ttk.Button(
            cbar, text="Review / Approve Selected", command=self.review_completed_selected, state="disabled")
        self.review_completed_btn.pack(side="right")
        self._show_main_view("select")
'''
src = replace_method(src, "_ui", ui)

# Learning database: assign stable IDs and migrate old counters into per-example completion markers.
load_method = r'''    def _learning_example_id(self, example):
        existing = str(example.get("example_id") or "").strip()
        if existing:
            return existing
        image = str(example.get("image") or "").strip()
        source = str(example.get("source_pdf") or "").strip()
        confirmed = str(example.get("confirmed_case_number") or "").strip()
        raw = f"{image}|{source}|{confirmed}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest() if image and confirmed else ""

    def _load_learning_data(self):
        db_v3 = self.learning_dir / "learning_examples_v3.json"
        db_v2 = self.learning_dir / "learning_examples_v2.json"
        legacy_db = self.learning_dir / "learning_examples.json"
        try:
            source_db = db_v3 if db_v3.exists() else (db_v2 if db_v2.exists() else legacy_db)
            loaded = json.loads(source_db.read_text(encoding="utf-8")) if source_db.exists() else {}
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
            self._sync_learning_type_databases()
            temp = db_v3.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self.learning_data, indent=2), encoding="utf-8")
            os.replace(temp, db_v3)
        except Exception:
            logging.exception("V3 learning database could not be read; using an empty learning set")
            self.learning_data = {
                "examples": [], "corrections": {},
                "trocr_v2_trained_count": 0, "trocr_v2_attempted_count": 0,
                "trocr_v2_validation": {}, "field_regions": [],
                "training_type_validation": {}, "last_training_summary": {},
                "trocr_v3_attempted_ids": [], "trocr_v3_trained_ids": []
            }
        self._refresh_learning_stats()
'''
learning_id_method = r'''    def _learning_example_id(self, example):
        existing = str(example.get("example_id") or "").strip()
        if existing:
            return existing
        image = str(example.get("image") or "").strip()
        source = str(example.get("source_pdf") or "").strip()
        confirmed = str(example.get("confirmed_case_number") or "").strip()
        raw = f"{image}|{source}|{confirmed}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest() if image and confirmed else ""
'''
src = replace_once(
    src,
    "    def _load_learning_data(self):",
    learning_id_method + "\n    def _load_learning_data(self):",
    "learning example identity helper"
)
src = replace_method(src, "_load_learning_data", load_method)

# Fixed 95% setting: no coworker controls, no editable percentage.
src = replace_method(src, "_get_auto_threshold", r'''    def _get_auto_threshold(self):
        """The department standard is fixed at 95%; this is not a user setting."""
        return 95.0''')

# Keep the review-time sequence clue anchored to all root-folder PDFs, not only the current scan subset.
seq_hint = r'''    def _sequence_hint_for_iid(self, iid):
        """Compare the immediately adjacent PDFs in natural filename order."""
        ordered = sorted(
            ((key, record) for key, record in self.records.items()
             if record.get("path") and Path(record["path"]).is_file()),
            key=lambda pair: self._natural_pdf_sort_key(Path(pair[1]["path"])))
        ids = [item[0] for item in ordered]
        try:
            pos = ids.index(iid)
        except ValueError:
            return [], ""
        if pos <= 0 or pos >= len(ids) - 1:
            return [], ""
        previous = self._sequence_case_number(self.records.get(ids[pos - 1]))
        following = self._sequence_case_number(self.records.get(ids[pos + 1]))
        if not previous or not following:
            return [], ""
        match_a, match_b = CASE_RE.fullmatch(previous), CASE_RE.fullmatch(following)
        if not match_a or not match_b:
            return [], ""
        year_a, digits_a = match_a.group(1).split("-", 1)
        year_b, digits_b = match_b.group(1).split("-", 1)
        if year_a != year_b:
            return [], ""
        a, b = int(digits_a), int(digits_b)
        if a == b:
            return [previous], f"Sequence clue: both adjacent files read {previous}; verify whether this file is also {previous}."
        if b == a + 2:
            options = [f"{year_a}-{a}", f"{year_a}-{a+1}"]
            return options, f"Sequence clue: adjacent files are {previous} and {following}; this file may be {options[0]} or {options[1]}."
        return [], ""

    def _apply_sequence_consensus(self):
        """Auto-place a one-file gap when both immediate neighbors independently agree."""
        ordered = sorted(
            ((key, record) for key, record in self.records.items()
             if record.get("path") and Path(record["path"]).is_file()),
            key=lambda pair: self._natural_pdf_sort_key(Path(pair[1]["path"])))
        inferred = 0
        for position in range(1, len(ordered) - 1):
            iid, record = ordered[position]
            if record.get("confirmed") or record.get("manual_approved"):
                continue
            if record.get("status") in ("Not scanned", "Scanning", "Moved to BLANKS"):
                continue
            values, message = self._sequence_hint_for_iid(iid)
            if len(values) != 1 or "both adjacent files read" not in message.lower():
                continue
            agreed = values[0]
            # Do not override a strong, conflicting two-engine reading.
            candidate = str(record.get("candidate") or "")
            try:
                strong_conflict = (
                    candidate != agreed
                    and bool(record.get("engine_agreement"))
                    and float(record.get("score") or 0.0) >= 95.0
                )
            except Exception:
                strong_conflict = False
            if strong_conflict:
                continue
            record["candidate"] = agreed
            record["confirmed"] = agreed
            record["auto_labeled"] = True
            record["manual_approved"] = False
            record["sequence_inferred"] = True
            record["status"] = "Sequence match — review required"
            self._move_to_completed(iid)
            inferred += 1
        if inferred:
            self._save_state()
            self.status.config(text=f"Sequence matching found {inferred} additional file(s) to review.")
        return inferred'''
src = replace_method(src, "_sequence_hint_for_iid", seq_hint)

# Learning update uses a limited incremental batch and keeps a small replay sample.
new_training = r'''    def _pending_learning_examples(self):
        attempted = set(self.learning_data.get("trocr_v3_attempted_ids", []))
        pending = []
        for example in self.learning_data.get("examples", []):
            if not (example.get("image") and example.get("confirmed_case_number")):
                continue
            example_id = self._learning_example_id(example)
            if example_id and example_id not in attempted:
                copy = dict(example)
                copy["example_id"] = example_id
                pending.append(copy)
        return pending

    @staticmethod
    def _even_sample(items, limit):
        if len(items) <= limit:
            return list(items)
        if limit <= 0:
            return []
        if limit == 1:
            return [items[-1]]
        indices = sorted({round(i * (len(items) - 1) / (limit - 1)) for i in range(limit)})
        return [items[i] for i in indices]

    def _refresh_learning_button_state(self):
        button = getattr(self, "finish_batch_btn", None)
        if button is None:
            return
        if self.busy or self.trocr_training:
            button.config(state="disabled")
            return
        attempted = set(self.learning_data.get("trocr_v3_attempted_ids", []))
        required = 5 if attempted else 12
        pending_count = len(self._pending_learning_examples())
        button.config(state="normal" if pending_count >= required else "disabled")

    def _maybe_train_trocr(self):
        if self.trocr_training or not self.learning_dir:
            return False
        usable = [
            dict(ex) for ex in self.learning_data.get("examples", [])
            if ex.get("image") and ex.get("confirmed_case_number")
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
        minimum_new = 12 if first_update else 5
        if len(pending) < minimum_new:
            return False

        # A single click processes at most 24 new crops plus up to 16 replay
        # examples. This prevents every update from walking the full 200+ library.
        selected_new = pending[:24]
        replay = self._even_sample(previously_used, 16)
        examples = selected_new + replay
        if len(examples) < 12:
            return False
        new_ids = [ex["example_id"] for ex in selected_new]

        self.trocr_training = True
        self.learning_pause_event.set()
        self.learning_stop_event.clear()
        if hasattr(self, "status"):
            self.status.config(
                text=f"Updating learning with {len(selected_new)} new examples and {len(replay)} reference examples.")
        self.scan_btn.config(state="disabled")
        self.finish_batch_btn.config(state="disabled")
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
        required = 5 if attempted else 12
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
'''
maybe_method = new_training[:new_training.find("    def _finish_import_batch")].rstrip()
finish_method = new_training[new_training.find("    def _finish_import_batch"):].rstrip()
src = replace_method(src, "_maybe_train_trocr", maybe_method)
src = replace_method(src, "_finish_import_batch", finish_method)

# Save a stable ID on each new verified crop.
src = replace_once(
    src,
    '            "confirmed_at": datetime.now().isoformat(timespec="seconds")\n        }\n',
    '            "confirmed_at": datetime.now().isoformat(timespec="seconds")\n        }\n'
    '        example["example_id"] = self._learning_example_id(example)\n',
    "stable learning example id"
)

# Training popup: PAUSE and STOP now interrupt safely between examples.
popup = r'''    def _open_training_progress(self):
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
'''
src = replace_method_block(src, "_open_training_progress", "_maybe_train_trocr", popup)

# Extend current training worker to accept new IDs, honor pause/stop, and only mark this batch used.
src = replace_once(
    src,
    "    def _train_trocr_worker(self, learning_dir, examples):\n",
    "    def _train_trocr_worker(self, learning_dir, examples, new_ids):\n",
    "training worker signature"
)
src = replace_once(
    src,
    "        try:\n            import torch\n            from PIL import Image\n            from transformers import TrOCRProcessor, VisionEncoderDecoderModel\n",
    "        try:\n            import torch\n            from PIL import Image\n            from transformers import TrOCRProcessor, VisionEncoderDecoderModel\n",
    "training imports unchanged"
)
src = replace_once(
    src,
    "            for index, ex in enumerate(examples, 1):\n                image_path = learning_dir / ex[\"image\"]\n",
    "            for index, ex in enumerate(examples, 1):\n                self._wait_for_learning_control()\n                image_path = learning_dir / ex[\"image\"]\n",
    "pause/stop while loading examples"
)
src = replace_once(
    src,
    "                for index, (image, target) in enumerate(validation_items, 1):\n                    pixels = processor(images=image, return_tensors=\"pt\").pixel_values.to(device)\n",
    "                for index, (image, target) in enumerate(validation_items, 1):\n                    self._wait_for_learning_control()\n                    pixels = processor(images=image, return_tensors=\"pt\").pixel_values.to(device)\n",
    "pause/stop during validation"
)
src = replace_once(
    src,
    "            for epoch in range(2):\n                model.train()\n",
    "            for epoch in range(2):\n                self._wait_for_learning_control()\n                model.train()\n",
    "pause/stop between training passes"
)
src = replace_once(
    src,
    "                for example_index, (image, label) in enumerate(train_items, 1):\n                    pixels = processor(images=image, return_tensors=\"pt\").pixel_values.to(device)\n",
    "                for example_index, (image, label) in enumerate(train_items, 1):\n                    self._wait_for_learning_control()\n                    pixels = processor(images=image, return_tensors=\"pt\").pixel_values.to(device)\n",
    "pause/stop during fine tuning"
)
src = replace_once(
    src,
    '            db = learning_dir / "learning_examples_v3.json"\n            with self.data_lock:\n',
    '            db = learning_dir / "learning_examples_v3.json"\n            with self.data_lock:\n',
    "model update metadata block"
)
src = replace_once(
    src,
    '            if improved:\n                report(94, "Validation improved. Saving and activating the updated model...")\n',
    '            self._wait_for_learning_control()\n            if improved:\n                report(94, "Validation improved. Saving and activating the updated model...")\n',
    "stop before activating model"
)
src = replace_once(
    src,
    '                latest["trocr_v2_attempted_count"] = len(usable)\n                if improved:\n                    latest["trocr_v2_trained_count"] = len(usable)\n',
    '                attempted_ids = set(latest.get("trocr_v3_attempted_ids", []))\n'
    '                attempted_ids.update(new_ids)\n'
    '                latest["trocr_v3_attempted_ids"] = sorted(attempted_ids)\n'
    '                latest["trocr_v2_attempted_count"] = len(attempted_ids)\n'
    '                if improved:\n'
    '                    trained_ids = set(latest.get("trocr_v3_trained_ids", []))\n'
    '                    trained_ids.update(new_ids)\n'
    '                    latest["trocr_v3_trained_ids"] = sorted(trained_ids)\n'
    '                    latest["trocr_v2_trained_count"] = len(trained_ids)\n',
    "record only current training batch as attempted"
)
src = replace_once(
    src,
    '                    "examples_total": len(usable),\n',
    '                    "examples_total": len(usable),\n'
    '                    "new_examples": len(new_ids),\n'
    '                    "reference_examples": max(0, len(examples) - len(new_ids)),\n',
    "record incremental batch size"
)
src = replace_once(
    src,
    '        except Exception as exc:\n            self._log("TrOCR training/validation failed: %s", exc)\n',
    '        except InterruptedError as exc:\n'
    '            self._log("TrOCR learning update stopped: %s", exc)\n'
    '            self.queue.put(("learning_stopped", "Learning update stopped. All saved corrections were kept; no incomplete model was activated."))\n'
    '        except Exception as exc:\n            self._log("TrOCR training/validation failed: %s", exc)\n',
    "stop training without activating a partial model"
)

# Fixed 95% detector threshold and hidden auto-confirm state; retain statistical validation safety gate.
# New scan list contains only files not scanned yet (or prior scan errors), rather than re-running all reviewed work.
scan_method = r'''    def scan(self):
        if self.busy or self.trocr_training or not self.records:
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
                json.dumps({"year": self.year_var.get().strip()}, indent=2),
                encoding="utf-8")
        except Exception:
            self._log("Could not save case-year setting")
        self.scan_items = [
            (iid, rec) for iid, rec in self.records.items()
            if not rec.get("confirmed")
            and rec.get("status") != "Moved to BLANKS"
            and (rec.get("status") == "Not scanned"
                 or str(rec.get("status", "")).startswith("Scan error:"))
            and Path(rec["path"]).is_file()
        ]
        if not self.scan_items:
            self.status.config(text="No new files to scan.")
            self.restart_btn.config(text="RESTART", state="normal")
            self.scan_btn.config(state="disabled")
            return
        self.busy = True
        self.paused = False
        self.stop_event.clear()
        self.pause_event.set()
        self.pause_btn.config(text="PAUSE", state="normal")
        self.restart_btn.config(text="STOP", state="normal")
        self.scan_btn.config(state="disabled")
        self._refresh_review_controls()
        self._update_completed_count()
        self.progress.config(maximum=len(self.scan_items), value=0)
        self.status.config(text=f"Scanning {len(self.scan_items)} file(s)...")
        self._log("Scan started in %s with year filter %s", self.folder, self.scan_year_prefix or "ALL")
        threading.Thread(target=self._worker, daemon=True).start()'''
src = replace_method(src, "scan", scan_method)

worker_method = r'''    def _worker(self):
        try:
            try:
                import torch
                gpu_available = bool(torch.cuda.is_available())
            except Exception:
                gpu_available = False
            reader_gpu = gpu_available
            if self.reader is None:
                try:
                    self.reader = easyocr.Reader(["en"], gpu=reader_gpu, verbose=False)
                except Exception:
                    self._log("EasyOCR GPU initialization failed; retrying on CPU")
                    reader_gpu = False
                    self.reader = easyocr.Reader(["en"], gpu=False, verbose=False)
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
                        blanks_dir = self.folder / "BLANKS"
                        blanks_dir.mkdir(exist_ok=True)
                        destination = blanks_dir / source_path.name
                        if destination.exists():
                            self.queue.put(("blank_conflict", iid, destination, idx))
                        else:
                            shutil.move(str(source_path), str(destination))
                            self.queue.put(("blank", iid, destination, idx))
                        continue
                    result = self._ocr_pdf(source_path)
                    candidate, snippet, note, handwritten, score, engines, agreement = result
                    self.queue.put((
                        "row", iid, candidate, snippet, note, handwritten,
                        score, engines, agreement, idx))
                except Exception as exc:
                    self._log("Scan failed for %s: %s", source_path, exc)
                    self.queue.put((
                        "row", iid, "", None, f"Scan error: {exc}",
                        False, 0.0, [], False, idx))
            if self.stop_event.is_set():
                stopped = True
            self.queue.put(("stopped", processed) if stopped else ("done",))
        except Exception as exc:
            self._log("Fatal scan initialization error: %s", exc)
            self.queue.put(("fatal", str(exc)))'''
src = replace_method(src, "_worker", worker_method)

pause_method = r'''    def toggle_pause(self):
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
        self.scan()'''
src = replace_method(src, "toggle_pause", pause_method)

# Apply two-sided consensus after a complete scan and after a human confirmation.
src = replace_once(
    src,
    '                elif kind == "done":\n                    self.busy = False\n',
    '                elif kind == "stopped":\n'
    '                    self.busy = False\n'
    '                    self.paused = False\n'
    '                    self.stop_event.clear()\n'
    '                    self.pause_event.set()\n'
    '                    self.scan_btn.config(state="normal" if self.records else "disabled")\n'
    '                    self.pause_btn.config(state="disabled", text="PAUSE")\n'
    '                    self.restart_btn.config(state="normal", text="RESTART")\n'
    '                    self._refresh_review_controls()\n'
    '                    self._refresh_learning_button_state()\n'
    '                    self._update_completed_count()\n'
    '                    self.status.config(text="Scan stopped. Press RESTART to continue with the remaining files.")\n'
    '                    self._save_state()\n'
    '                elif kind == "done":\n                    self.busy = False\n'
    '                    self._apply_sequence_consensus()\n',
    "stop and finish scan handling"
)
src = src.replace(
    '                    self.pause_btn.config(state="disabled", text="PAUSE SCAN")\n'
    '                    self.restart_btn.config(state="normal")\n',
    '                    self.pause_btn.config(state="disabled", text="PAUSE")\n'
    '                    self.restart_btn.config(state="normal", text="RESTART")\n',
    1
)
src = replace_once(
    src,
    '                    self.status.config(\n'
    '                        text="Scan complete. Review every candidate; then finish the batch to update the learning model.")\n',
    '                    self.status.config(text="Scan complete. Review the files that need attention.")\n'
    '                    self._refresh_learning_button_state()\n',
    "simplify completion status"
)
src = src.replace('self.pause_btn.config(state="disabled", text="PAUSE SCAN")',
                  'self.pause_btn.config(state="disabled", text="PAUSE")')
src = re.sub(
    r'self\\.restart_btn\\.config\\(state="normal"\\)',
    'self.restart_btn.config(state="normal", text="RESTART")',
    src
)

src = src.replace('self.finish_batch_btn.config(state="normal")',
                  'self._refresh_learning_button_state()')

src = replace_once(
    src,
    '            self._move_to_completed(iid)\n            win.destroy()\n            next_index = index + 1\n',
    '            self._move_to_completed(iid)\n            win.destroy()\n            self._apply_sequence_consensus()\n            next_index = index + 1\n',
    "recheck adjacent files after manual confirmation"
)

# Learning updates can start with a reviewed subset; they do not require every PDF in the folder.
# Add graceful training stopped state to the Tk queue poller.
src = replace_once(
    src,
    '                elif kind == "learning_status":\n                    _, message = msg\n                    self.trocr_training = False\n',
    '                elif kind == "learning_stopped":\n'
    '                    _, message = msg\n'
    '                    self.trocr_training = False\n'
    '                    self.learning_pause_event.set()\n'
    '                    self.learning_stop_event.clear()\n'
    '                    self.trocr_model = None\n'
    '                    self.trocr_processor = None\n'
    '                    self.trocr_error = ""\n'
    '                    self._load_learning_data()\n'
    '                    self.status.config(text=message)\n'
    '                    self._log(message)\n'
    '                    self.scan_btn.config(state="normal" if self.records else "disabled")\n'
    '                    self._refresh_learning_button_state()\n'
    '                    self._update_training_progress(100, message, finished=True)\n'
    '                elif kind == "learning_status":\n'
    '                    _, message = msg\n'
    '                    self.trocr_training = False\n'
    '                    self.learning_pause_event.set()\n'
    '                    self.learning_stop_event.clear()\n',
    "training cancellation queue handling"
)

# The finish control is in the header, not in the scan toolbar. Update it after corrections.
src = replace_once(
    src,
    '        self._refresh_learning_stats()\n        self._save_state()\n        # Deliberately do not start training here.',
    '        self._refresh_learning_stats()\n        self._refresh_learning_button_state()\n        self._save_state()\n        # Deliberately do not start training here.',
    "refresh update-learning button after confirmed crop"
)

# Keep only the coworker-friendly two-button review workflow in the file list.
# Existing candidate/score values remain internally available but are not displayed.
src = replace_once(
    src,
    '        self._refresh_auto_eligibility_status()\n        if hasattr(self, "learning_type_stats_label"):\n',
    '        self._refresh_auto_eligibility_status()\n        self._refresh_learning_button_state()\n        if hasattr(self, "learning_type_stats_label"):\n',
    "refresh learning action state"
)

# Make the auto-confirm flag non-optional and lock the threshold to 95 across scans.
# Keep the hidden flag enabled even after any legacy persisted settings are loaded.
src = replace_once(
    src,
    '        self._refresh_auto_eligibility_status()\n        root.after(150, self._poll)\n',
    '        self.auto_label_var.set(True)\n        self.auto_threshold_var.set("95")\n        self._refresh_auto_eligibility_status()\n        root.after(150, self._poll)\n',
    "enforce fixed auto confirm on startup"
)

# Modern status handling after model update and scan stop.
# The training worker only marks this batch as attempted; unfinished/cancelled work stays pending.
files["case_number_ocr.py"] = src.encode("utf-8")
if "README.txt" in files:
    files["README.txt"] = (
        b"M.F. CASE NUMBER OCR - VERSION 3.3.6\r\n\r\n"
        b"Use SELECT FILES to choose the PDF folder. Click SCAN, then REVIEW FILES.\r\n"
        b"Auto-confirm uses the fixed 95% department standard and the existing validation safeguards.\r\n"
        b"UPDATE LEARNING processes a limited group of new confirmed examples at a time. "
        b"PAUSE and STOP are available while the model is updating.\r\n"
        b"Learning corrections are saved immediately. Stopping an update does not remove saved corrections "
        b"or replace the active model with an incomplete model.\r\n"
        b"Files automatically matched by the before-and-after rule are placed in Completed for review; "
        b"they are not automatically approved for export.\r\n"
        b"PDF originals are preserved. SEND TO KURT only copies manually approved PDFs.\r\n"
    )

import ast
module_ast = ast.parse(src, filename="case_number_ocr.py")
app_class = next(
    (node for node in module_ast.body if isinstance(node, ast.ClassDef) and node.name == "App"),
    None
)
if app_class is None:
    raise RuntimeError("App class is missing.")
method_names = [
    node.name for node in app_class.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
]
for method_name in (
    "_open_training_progress", "_training_popup_close_requested",
    "_close_training_popup", "_update_training_progress",
    "_toggle_learning_pause", "_stop_learning_update",
    "_wait_for_learning_control", "_maybe_train_trocr",
    "_finish_import_batch", "_sequence_hint_for_iid",
    "_apply_sequence_consensus", "scan", "_worker",
    "toggle_pause", "stop_or_restart_scan"
):
    if method_names.count(method_name) != 1:
        raise RuntimeError(
            f"Expected exactly one definition of {method_name}; "
            f"found {method_names.count(method_name)}."
        )
ui_method = next(node for node in app_class.body if isinstance(node, ast.FunctionDef) and node.name == "_ui")
ui_literals = [
    node.value.value for node in ast.walk(ui_method)
    if isinstance(node, ast.Constant) and isinstance(node.value, str)
]
for forbidden in ("OCR score is a detector score", "Minimum OCR score %", "Inspect Learning Data",
                  "Finish Batch / Update Learning Model"):
    if forbidden in ui_literals:
        raise RuntimeError(f"Old technical text remains in Select Files UI: {forbidden}")
if 'self.app_version = "3.3.6"' not in src:
    raise RuntimeError("Application version was not updated to 3.3.6.")

compile(src, "case_number_ocr.py", "exec")
# Static verification of key requirements.
for required in [
    "UPDATE LEARNING", "REVIEW FILES", "SEND TO KURT",
    'text="PAUSE"', 'text="STOP"', 'text="REFRESH"',
    "self.auto_threshold_var.set(\"95\")",
    "self._apply_sequence_consensus()", "trocr_v3_attempted_ids",
    "self._wait_for_learning_control()", "learning_stopped"
]:
    if required not in src:
        raise RuntimeError(f"Missing expected V3.3.6 feature: {required}")
if "Finish Batch / Update Learning Model" in src:
    raise RuntimeError("Old learning button text remains.")
if "Minimum OCR score %" in src or "OCR score is a detector score" in src:
    raise RuntimeError("Technical explanations remain in the SELECT FILES view.")

with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for name, data in files.items():
        z.writestr(name, data)
with zipfile.ZipFile(OUT, "r") as z:
    bad = z.testzip()
    if bad:
        raise RuntimeError(f"ZIP integrity failure: {bad}")
    if set(Path(n).name for n in z.namelist()) != set(files):
        raise RuntimeError("The update ZIP contents changed unexpectedly.")
print(f"V3.3.6 package built: {OUT.stat().st_size} bytes")
