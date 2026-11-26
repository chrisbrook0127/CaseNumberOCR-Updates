MFCASE V5.6.17 — FULL INSTALL / USER GUIDE

INSTALL / REPAIR
1. Preserve your existing MFCase folder and all original PDF, learning, and state files.
2. Extract this COMPLETE installer ZIP. Run SETUP.bat to verify Python 3.11, OCR libraries, and create the Windows desktop shortcut.
3. Launch via the desktop shortcut; RUN_CASE_OCR_DEBUG.bat is available for troubleshooting.
4. In an existing installation, SETTINGS > UPDATE downloads the approved GitHub release and restarts the application. UPDATE LEARNING remains a separate action.

SINGLE-SCREEN WORKFLOW
1. FOLDER (DARK BLUE): Choose the root folder containing case PDFs.
2. SCAN (GREEN PLAY): OCR checks saved, verified crop locations first, then the upper-right quarter of each page. Handwriting and printed OCR remain available. Valid cases begin with 08-, followed by 1–7 digits. Automatic OCR approval remains at 95%.
3. PAUSE (LIGHT BLUE): Pause/resume the scan. STOP (RED SQUARE): Stop work without deleting existing approvals or automatically sending files.
4. FILE LIST (YELLOW THREE-LINE BUTTON): Starts in filtered REVIEW mode; click to show all files and click again to filter. The existing table, thumbnails, sortable columns, checkboxes, double-click review, green approval checks, and RESCAN ALL remain available.
5. PROGRESS: Displays the chosen root folder, selected count, active PDF, current scan index, progress, and estimated/actual/remaining time. Estimates improve after the first files complete.
6. REVIEW: Double-click a row or select R. Verify or enter the correct case number, then click the GREEN CHECK to complete. Crops and positions are saved locally for subsequent learning.
7. BEFORE/AFTER: Two matching independent approved cases on opposite sides may automatically confirm the intervening files. Manually confirmed numbers have precedence over inferred readings. Previously sent files remain eligible as anchors where supported.
8. PREVIOUS CASE PREFILL: ONE preceding strong case may fill the proposed case number in low-confidence rows, but NEVER automatically approves, renames, or sends those PDFs. Each proposed case remains in REVIEW until a person verifies it and clicks the GREEN CHECK. Two-sided matching before/after approval remains unchanged.
9. SEND FILES: Moves eligible, approved files into COMPLETED. AUTO SEND is optional and defaults OFF. STOP never triggers AUTO SEND.
10. REFRESH reloads the selected folder. CLEAR removes the visible list without deleting files.
11. SETTINGS includes USER GUIDE, GITHUB SETTINGS, EXPORT WORK LOG, UPDATE, and UPDATE LEARNING. While the app is otherwise idle, existing local background training may run; the small green animation beside SETTINGS indicates activity.
12. EXPORT WORK LOG saves a uniquely named ZIP directly to the root folder's COMPLETED subfolder. Logs may include case numbers, filenames, or OCR text; use departmental sharing safeguards.

DATA AND UPDATER SAFETY
- All OCR processing of confidential PDFs and learning examples runs locally; do not upload sensitive source documents to a repository.
- Updates may fetch released application files when explicitly requested; optional sanitized diagnostic upload is configured under GITHUB SETTINGS.
- The updater replaces only allowed application files and preserves original PDFs, model weights, learning data, saved review state, and log files.
- The original v5.6.16 installation can be retained as a separate rollback reference.

OPTIONAL COMBINER (FULL INSTALLER ONLY)
- Run: python MFCase_Combine_And_Trace.py "<ROLL>\COMPLETED"
- Combines PDFs sharing a case number to COMPLETED\COMBINED, tracks originals, and avoids overwriting existing output unless explicitly allowed.
