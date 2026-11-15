"""MFCase v5.2.0: single Scan surface, explicit file states, focused review and secured diagnostics.

Build from published v5.1.5 SHA256-verified source. Preserve all existing OCR
and learning code unless explicitly changed here. No case records are included
in the repository, and no diagnostic data is uploaded to public GitHub.
"""
import ast,base64,hashlib,io,re,subprocess,zipfile
from pathlib import Path

ROOT=Path("release-assets")
raw=base64.b64decode(subprocess.check_output(
  ["git","show","FETCH_HEAD:release-assets/update.base64"],text=True).strip(),validate=True)
assert hashlib.sha256(raw).hexdigest()=="9e687759ebc8d12ce529737102078c9b6f580e575e99e55a96c5a73fbca60ab2"
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None and set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    source=z.read("case_number_ocr.py").decode()
    updater=z.read("updater_apply.py")
assert source.count('self.app_version = "5.1.5"')==1
source=source.replace('self.app_version = "5.1.5"','self.app_version = "5.2.0"',1)
source=source.replace('root.title("M.F. Case Number OCR — V5.1.5")',
                      'root.title("M.F. Case Number OCR — V5.2.0")')
source=source.replace("Case Number OCR & Renamer — V5.1.2","Case Number OCR & Renamer — V5.2.0")

def replace_one(old,new):
    global source
    count=source.count(old)
    if count!=1:
        raise AssertionError(f"Missing/duplicate patch anchor ({count}): {old[:120]!r}")
    source=source.replace(old,new,1)

def replace_method(name,body):
    global source
    tree=ast.parse(source)
    matches=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name]
    if len(matches)!=1:raise AssertionError((name,len(matches)))
    n=matches[0];lines=source.splitlines(keepends=True)
    source="".join(lines[:n.lineno-1])+body.strip("\n")+"\n"+"".join(lines[n.end_lineno:])
    compile(source,"case_number_ocr.py","exec")

# Only SCAN and SETTINGS are user-facing. Preserve the former second tree as
# a hidden compatibility view to avoid breaking approval/export safeguards.
replace_one('''        self.completed_nav_btn.pack(side="right", padx=(6, 0))
        self.select_files_nav_btn.pack(side="right", padx=(6, 0))''','''        self.settings_nav_btn=tk.Button(
            header,text="SETTINGS",command=lambda:self._show_main_view("settings"),
            bg="#1456a8",fg="white",activebackground="#0d4388",
            activeforeground="white",relief="raised",bd=2,padx=14,pady=8,
            font=("Segoe UI",10,"bold"),cursor="hand2")
        self.settings_nav_btn.pack(side="right",padx=(6,0))
        self.select_files_nav_btn.pack(side="right", padx=(6, 0))''')
replace_one('''        self.completed_tab = ttk.Frame(self.view_container, padding=8)
        for view in (self.review_tab, self.completed_tab):''','''        self.completed_tab = ttk.Frame(self.view_container, padding=8)
        self.settings_tab = ttk.Frame(self.view_container, padding=14)
        for view in (self.review_tab, self.completed_tab, self.settings_tab):''')
replace_one('''        self.review_btn.pack(side="left", padx=5, ipadx=8, ipady=3)''',
            '''        # The old REVIEW FILES button is not shown; double-click a SCAN row.''')
replace_one('''        self.work_log_btn.pack(side="left",padx=5,ipadx=5,ipady=3)''','''        self.work_log_btn.pack(side="left",padx=5,ipadx=5,ipady=3)
        self.send_selected_btn=ttk.Button(
            action_bar,text="SEND ALL SELECTED",command=self._send_all_selected,
            state="disabled")
        self.send_selected_btn.pack(side="left",padx=5,ipadx=8,ipady=3)''')
replace_one('''        cols = ("scan_select", "file", "candidate", "score", "status")''',
            '''        cols = ("scan_select", "file", "candidate", "score", "workflow", "status")''')
replace_one('''        self.tree.heading("status", text="STATUS")
        self.tree.column("status", width=240, minwidth=120, stretch=True)''',
            '''        self.tree.heading("workflow",text="STATUS")
        self.tree.column("workflow",width=114,minwidth=98,stretch=False,anchor="center")
        self.tree.heading("status", text="OCR DETAILS")
        self.tree.column("status", width=320, minwidth=140, stretch=True)''')
replace_one('''        self.tree["displaycolumns"] = ("scan_select", "file", "candidate", "score", "status")''',
            '''        self.tree["displaycolumns"] = ("scan_select", "file", "candidate", "score", "workflow", "status")''')
replace_one('''        self.tree.bind("<Double-1>", self.on_double_click)''',
            '''        self.tree.bind("<Double-1>", self._unified_double_click)''')
replace_one('''        self._show_main_view("select")''','''        ttk.Label(self.settings_tab,text="SETTINGS",
                  font=("Segoe UI",16,"bold")).pack(anchor="w",pady=(0,14))
        ttk.Label(self.settings_tab,text="Application version: V5.2.0",
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
        self._show_main_view("select")''')

view=r'''
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
'''
replace_method("_show_main_view",view)

# This is status classification only. Do not destroy rich OCR details.
listing=r'''
    @staticmethod
    def _workflow_status(record):
        if record.get("exported"):
            return "SENT"
        if record.get("confirmed") and record.get("manual_approved"):
            return "COMPLETED"
        if record.get("status")=="Not scanned":
            return "NOT SCANNED"
        return "REVIEW"

    def _insert_scan_row(self, iid, record, candidate_override=None, score_override=None):
        """Keep every PDF visible across review, approval and physical transfer."""
        if not hasattr(self,"tree"):return
        values=("☑" if iid in self.scan_checked_iids else "☐",
                record["path"].name,
                record.get("candidate","") if candidate_override is None else candidate_override,
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
        label=getattr(self,"connection_status_label",None)
        if label is None:return
        models=bool(getattr(self,"reader",None) is not None
                    and getattr(self,"trocr_model",None) is not None)
        share=bool(self.learning_dir and Path(self.learning_dir).is_dir())
        label.config(text=(
            f"OCR models: {'READY (local recognition)' if models else 'NOT READY'}   |   "
            f"Shared learning: {'CONNECTED' if share else 'OFFLINE / NOT CONNECTED'}   |   "
            "GitHub updates: require internet; UPDATE checks availability. "
            f"Scanning: {'ENABLED' if self._startup_ready and models and share else 'BLOCKED pending required startup components'}"))
'''
replace_one("    def _insert_scan_row(self, iid, record, candidate_override=None, score_override=None):", listing+"    def _obsolete_insert_scan_row(self, iid, record, candidate_override=None, score_override=None):")
# The original insertion method is renamed, leaving the new one as the implementation.

# Preserve selected and physically sent records when reloading folder state.
replace_one('''            pdfs = sorted(
                (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
                key=self._natural_pdf_sort_key)''','''            pdfs = [p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower()==".pdf"]
            completed=folder/"COMPLETED"
            if completed.is_dir():
                pdfs.extend(p for p in completed.iterdir()
                            if p.is_file() and p.suffix.lower()==".pdf")
            pdfs.sort(key=self._natural_pdf_sort_key)''')
replace_one('''            old = saved.get(pdf.name, {})
            item = {''','''            old = saved.get(pdf.name, {})
            if not old:
                old=next((v for v in saved.values()
                          if v.get("export_name")==pdf.name and
                          pdf.parent.name.upper()=="COMPLETED"),{})
            sent=pdf.parent.name.upper()=="COMPLETED"
            item = {''')
replace_one('''"completed_iid": None, "exported": bool(old.get("exported", False)),''',
            '''"completed_iid": None, "exported": bool(old.get("exported",False) or sent),
                "original_name": old.get("original_name") or
                                 (pdf.name if not sent else old.get("source_name",pdf.name)),
                "original_path": old.get("original_path",""),''')
replace_one('''            iid = f"pdf::{pdf.name}"
            self.records[iid] = item''',
            '''            if sent:
                item["status"]=f"Sent to COMPLETED: {pdf.name}"
                item["scan_selected"]=False
            iid = f"pdf::{pdf.name}"
            if iid in self.records:
                iid=f"pdf::COMPLETED::{pdf.name}"
            self.records[iid] = item''')
replace_one('''            if item["confirmed"] and not item["exported"]:
                self._insert_completed_row(iid, item)
                item["completed_iid"] = iid
            elif item["exported"]:
                item["completed_iid"] = None
            else:
                self._insert_scan_row(iid, item)''','''            if item["confirmed"] and not item["exported"]:
                self._insert_completed_row(iid,item)
                item["completed_iid"]=iid
            else:
                item["completed_iid"]=None
            self._insert_scan_row(iid,item)''')
replace_one('''        payload["files"][path.name] = {''',
            '''        payload["files"][str(rec.get("original_name") or path.name)] = {''')
replace_one('''                "export_name": rec.get("export_name", ""),''',
            '''                "export_name": rec.get("export_name", ""),
                "original_name": rec.get("original_name",""),
                "original_path": rec.get("original_path",""),''')
replace_one('''        if self.tree.exists(iid):
            self.tree.delete(iid)
        if not batch:
            self._update_completed_count()''',
            '''        # Do not hide approved PDFs: the unified list shows COMPLETED.
        self._insert_scan_row(iid,r)
        if not batch:
            self._update_completed_count()''')
replace_one('''            if self.tree.exists(iid):
                self.tree.delete(iid)
            self.completed_photo_refs.pop(iid, None)''',
            '''            # SENT remains visible in the single Scan list.
            self._insert_scan_row(iid,rec)
            self.completed_photo_refs.pop(iid, None)''')
replace_one('''            message = f"Sent to Kurt: {target.name} (from {source_name})"''',
            '''            message = f"Moved to COMPLETED: {target.name} (from {source_name})"''')
replace_one('''            rec["status"] = f"Kurt transfer failed: {error}"''',
            '''            rec["status"] = f"Transfer failed: {error}"''')
replace_one('''            message = (f"Kurt transfer failed for {source_name}: {error}. "
                       "Source retained; use SEND TO KURT to retry.")''',
            '''            message = (f"Transfer failed for {source_name}: {error}. "
                       "Source retained; select COMPLETED row and click SEND ALL SELECTED.")''')
replace_one('''                    self.queue.put(("export_result", iid, source.name, str(destination), ""))''',
            '''                    self.queue.put(("export_result", iid, source.name, str(destination), ""))''') if False else None

# A fresh refresh is a state-aware re-enumeration of root and COMPLETED.
refresh=r'''
    def refresh_root_folder(self):
        """Reload both root PDFs and the root/COMPLETED subfolder."""
        if not self._startup_ready or self.busy or self.trocr_training or self.export_busy:
            return
        if not self.folder or not self.folder.is_dir():
            messagebox.showinfo("Choose a folder","Choose a folder first.")
            return
        self._save_state()
        self._folder_load_generation+=1
        generation=self._folder_load_generation
        self._loader_start("folder","Refreshing root and COMPLETED PDFs…")
        threading.Thread(target=self._folder_load_worker,
                         args=(self.folder,generation),daemon=True).start()
'''
replace_method("refresh_root_folder",refresh)

# Learning: manual rectangle ALWAYS wins over matching OCR candidates.
replace_one('''        if selected_candidate:
            training_rect = selected_candidate.get("rect")
        elif field_rect:''','''        if record.get("manual_crop_rect") and field_rect:
            training_rect=tuple(field_rect)
        elif selected_candidate:
            training_rect=selected_candidate.get("rect")
        elif field_rect:''')
replace_one('''        crop_origin = "candidate-number-box" if selected_candidate else ("manual-field" if record.get("manual_crop_rect") else "ocr")''',
            '''        crop_origin = ("manual-field" if record.get("manual_crop_rect")
                       else "candidate-number-box" if selected_candidate else "ocr")''')
replace_one('''        self._refresh_learning_stats()
        self._refresh_learning_button_state()
        self._save_state()''',
            '''        self._diag_event("learning_saved",file=Path(record["path"]).name,
               confirmed_case_number=confirmed_number,
               crop_origin=crop_origin,training_type=training_type,
               image_saved=bool(image_rel))
        self._diag_sync_shared()
        self._refresh_learning_stats()
        self._refresh_learning_button_state()
        self._save_state()''')

# Synchronize diagnostics to the already authorized/shared learning drive,
# not to the public GitHub repository. No case PDFs are uploaded.
shared=r'''
    def _diag_sync_shared(self):
        """Centralize logs under restricted learning storage after learning saves."""
        local=getattr(self,"_diag_file",None)
        if not local or not Path(local).is_file() or not self.learning_dir:
            return
        try:
            import socket
            host=re.sub(r"[^A-Za-z0-9_.-]","_",socket.gethostname())[:64]
            target=Path(self.learning_dir)/"diagnostics"/host
            target.mkdir(parents=True,exist_ok=True)
            final=target/Path(local).name
            part=target/(Path(local).name+".tmp")
            shutil.copy2(local,part)
            os.replace(part,final)
        except OSError as exc:
            self._log("Shared diagnostic log synchronization failed: %s",exc)

'''
replace_one("    def _diag_start_run(self):",shared+"    def _diag_start_run(self):")
replace_one('''                    self._diag_event("run_end",summary=dict(self._run_outcomes))''',
            '''                    self._diag_event("run_end",summary=dict(self._run_outcomes))
                    threading.Thread(target=self._diag_sync_shared,daemon=True).start()''')

# Simple verification panel: one read score, editable number, optional category
# toggles, SKIP and SAVE AND SEND. Keep crop edit and PDF preview intact.
replace_one('''        ttk.Label(controls, text="OCR candidates (select a reading):",''',
            '''        ttk.Label(controls, text=f"OCR READ: {float(r.get('score') or 0):.1f}%",''')
replace_one('''        candidate_selector.grid(row=4, column=0, sticky="ew", pady=(2, 4))''',
            '''        # Candidate dropdown hidden; red-box selection remains available on the PDF.''')
replace_one('''        nav_buttons.grid(row=label_row + 4, column=0, sticky="ew", pady=(4, 8))''',
            '''        # Previous/Next controls removed from simplified one-file review.''')
replace_one('''        send_after_approval = getattr(self, "_active_main_view", "select") == "select"''',
            '''        send_after_approval=True
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
            self._save_state()''')
replace_one('''            try:
                self._save_learning_example(r, value)''',
            '''            r["training_type"]=training_type_var.get()
            try:
                self._save_learning_example(r, value)''')
replace_one('''        ttk.Button(controls, text="SAVE LEARNING & SEND TO KURT" if send_after_approval else "APPROVE",
                   command=confirm).grid(row=action_row, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(controls, text="Leave for Later",
                   command=win.destroy).grid(row=action_row + 1, column=0, sticky="ew")''',
            '''        type_row=ttk.Frame(controls)
        type_row.grid(row=action_row,column=0,sticky="ew",pady=(3,8))
        for type_name in ("handwritten","printed"):
            button=tk.Button(type_row,text=type_name.upper(),
                    command=lambda value=type_name:choose_training_type(value),
                    padx=12,pady=6)
            button.pack(side="left",padx=(0,8))
            type_buttons[type_name]=button
        choose_training_type(training_type_var.get()) if training_type_var.get() in type_buttons else None
        ttk.Button(controls,text="SAVE AND SEND",
                   command=confirm).grid(row=action_row+1,column=0,sticky="ew",pady=(0,8))
        ttk.Button(controls,text="SKIP",
                   command=win.destroy).grid(row=action_row+2,column=0,sticky="ew")''')
replace_one('''            training_type_var = tk.StringVar(
                value=("handwritten" if r.get("handwritten") and
                       r.get("training_type","unsure")=="unsure" else
                       r.get("training_type","unsure")))''',
            '''            # Classification is controlled by the two optional buttons above.''')
replace_one('''            training_type_box.grid(row=15, column=0, sticky="e")''',
            '''            # Hidden legacy control retained for internal state compatibility.''')
replace_one('''            ttk.Label(controls, text="Learning category (does not change OCR score):").grid(
                row=15, column=0, sticky="w")''',
            '''            # Optional classification is available as handwritten/printed buttons.''')
replace_one('''            ttk.Label(controls, text="This only files the confirmed crop in the matching learning library.",
                      wraplength=280).grid(row=15, column=0, sticky="w", pady=(24, 0))''',
            '''            # Explanation moved to the Instructions window.''')
# Move preview fields under the action block to avoid overlap on both UI layouts.
for before,after in [("row=13,","row=26,"),("row=14,","row=27,"),
                     ("row=15,","row=28,"),("row=16,","row=29,"),
                     ("row=17,","row=30,"),("row=18,","row=31,"),
                     ("row=19,","row=32,")]:
    # Only inside _show_review_item to avoid altering unrelated UI.
    tree=ast.parse(source)
    n=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="_show_review_item")
    seg=ast.get_source_segment(source,n)
    if before in seg:
        source=source.replace(seg,seg.replace(before,after),1)
# Remove old long review direction text; keep crop instructions.
source=source.replace('"SAVE LEARNING & SEND TO KURT"','"SAVE AND SEND"')
source=source.replace('"SEND TO KURT"','"SEND ALL SELECTED"')

# Update new SEND button whenever a record changes.
replace_one('''        self._refresh_rescan_button()
 
    def clear_scan_screen''',
            '''        self._refresh_rescan_button()
 
    def clear_scan_screen''') if False else None
# Distinguish OCR candidate selection from an intentionally drawn crop.
# Drawn crops are authoritative for thumbnail generation. Selected OCR boxes
# are authoritative until the reviewer draws a new rectangle.
replace_one('''        if record.get("manual_crop_rect") and field_rect:
            training_rect=tuple(field_rect)''',
            '''        if (record.get("manual_crop_rect") and field_rect and
                (record.get("manual_crop_origin")=="drawn" or not selected_candidate)):
            training_rect=tuple(field_rect)''')
replace_one('''        crop_origin = ("manual-field" if record.get("manual_crop_rect")
                       else "candidate-number-box" if selected_candidate else "ocr")''',
            '''        crop_origin=("manual-field" if record.get("manual_crop_rect") and
                     (record.get("manual_crop_origin")=="drawn" or not selected_candidate)
                     else "candidate-number-box" if selected_candidate else "ocr")''')
replace_one('''                        selection["preview_rect"] = None
                    selection_status.config(''',
            '''                        r["manual_crop_origin"]="candidate"
                        selection["preview_rect"] = None
                    selection_status.config(''')
replace_one('''                        r["manual_crop_rect"]=selected
                        r["training_type"]=training_type_var.get()''',
            '''                        r["manual_crop_rect"]=selected
                        r["manual_crop_origin"]="drawn"
                        r["training_type"]=training_type_var.get()''')
replace_one('''                r["manual_crop_rect"] = None
                selection["drag"] = None''',
            '''                r["manual_crop_rect"] = None
                r["manual_crop_origin"]=""
                selection["drag"] = None''')
replace_one('''                "manual_crop_rect": list(rec["manual_crop_rect"]) if rec.get("manual_crop_rect") else None,''',
            '''                "manual_crop_rect": list(rec["manual_crop_rect"]) if rec.get("manual_crop_rect") else None,
                "manual_crop_origin": rec.get("manual_crop_origin",""),''')
replace_one('''                "manual_crop_rect": tuple(old.get("manual_crop_rect", [])) if old.get("manual_crop_rect") else None,''',
            '''                "manual_crop_rect": tuple(old.get("manual_crop_rect", [])) if old.get("manual_crop_rect") else None,
                "manual_crop_origin": old.get("manual_crop_origin",""),''')
# Optional category display reflects the existing state without de-selecting it.
replace_one('''        choose_training_type(training_type_var.get()) if training_type_var.get() in type_buttons else None''',
            '''        for name,widget in type_buttons.items():
            active=(training_type_var.get()==name)
            widget.config(relief="sunken" if active else "raised",
                          bg="#1456a8" if active else "#ededed",
                          fg="white" if active else "black")''')
# Re-enable SEND SELECTED after a scan finishes or a completed record is loaded.
tree=ast.parse(source)
method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="_refresh_scan_button")
oldseg=ast.get_source_segment(source,method)
assert oldseg.count('self._refresh_rescan_button()')==1
newseg=oldseg.replace('self._refresh_rescan_button()',
    '''self._refresh_rescan_button()
        button=getattr(self,"send_selected_btn",None)
        if button is not None:
            allowed=(self._startup_ready and not self.busy and
                    not self.export_busy and not self.trocr_training and
                    any(self._eligible_for_kurt(iid,self.records[iid])
                        for iid in self.tree.selection() if iid in self.records))
            button.config(state="normal" if allowed else "disabled")''')
source=source.replace(oldseg,newseg,1)
# Fix stale review status after transfer and selection change.
replace_one('''            rec["completed_iid"] = None
            message = f"Moved to COMPLETED:''',
            '''            rec["completed_iid"] = None
            self._insert_scan_row(iid,rec)
            message = f"Moved to COMPLETED:''')
# Widen support for printed headings without considering any non-case number
# as a case. The candidate regex remains digit-hyphen-digit only.
replace_one('''CASE_FIELD_LABEL_RE = re.compile(r"(?:Case\s+Number|INCIDENT\s+N[O0]\s*\.?)", re.IGNORECASE)''',
            '''CASE_FIELD_LABEL_RE = re.compile(
    r"(?:CASE\s*(?:NO\.?|NUM(?:BER)?|#)|INCIDENT\s*(?:N[O0]\s*\.?|NUM(?:BER)?|#))",
    re.IGNORECASE)''')
compile(source,"case_number_ocr.py","exec")
assert 'self.app_version = "5.2.0"' in source
assert 'root.title("M.F. Case Number OCR — V5.2.0")' in source
assert 'text="EXPORT WORK LOG"' in source
assert 'text="SAVE AND SEND"' in source
assert 'text="SKIP"' in source
assert 'text="SEND ALL SELECTED"' in source
assert 'self._diag_sync_shared()' in source
assert 'self.tree.bind("<Double-1>", self._unified_double_click)' in source
assert 'def _workflow_status(' in source
assert 'dimension_px=int(round(3*72*2.2))' in source
assert 'minimum_independent_matches=3' in source

out=io.BytesIO()
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.writestr("case_number_ocr.py",source)
    z.writestr("updater_apply.py",updater)
payload=out.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None
    assert set(z.namelist())=={"case_number_ocr.py","updater_apply.py"}
    compile(z.read("case_number_ocr.py"),"case_number_ocr.py","exec")
    compile(z.read("updater_apply.py"),"updater_apply.py","exec")
sha=hashlib.sha256(payload).hexdigest()
(ROOT/"update.base64").write_text(base64.b64encode(payload).decode("ascii"))
(ROOT/"CaseNumberOCR_Update.zip").write_bytes(payload)
(ROOT/"SHA256.txt").write_text(sha+"\n")
(ROOT/"RELEASE_TAG.txt").write_text("v5.2.0\n")
(ROOT/"PUBLISH_REQUEST.txt").write_text("V5.2.0 unified scan, review, learning crop, sent persistence and secure diagnostics\n")
print("V5.2.0 BUILT",len(payload),"SHA256",sha)
