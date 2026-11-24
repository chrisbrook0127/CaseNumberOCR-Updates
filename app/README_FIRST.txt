MFCASE V5.6.0 — FULL INSTALL / WORKFLOW

INSTALLATION
1. Back up the existing MFCase folder and preserve all data/learning files.
2. Extract the COMPLETE ZIP and run SETUP.bat to verify Python 3.11 and installed OCR libraries.
3. The desktop shortcut uses Launch_Case_OCR.vbs so no CMD window stays open.
4. RUN_CASE_OCR_DEBUG.bat shows startup errors when needed.

SINGLE-SCREEN WORKFLOW
- SELECT FILES > SCAN. Only the upper-right 4x4 is searched automatically;
  both EasyOCR and TrOCR can accept a full, localized, unambiguous 08- reading.
- The green check COMPLETES a row but does not send it unless AUTO SEND is enabled
  AND a scan has ended and before/after checks completed.
- Click R or double-click anywhere on a row to open full-page manual REVIEW.
- During SCAN, case numbers on other rows can be corrected and checked.
  The currently active OCR file is locked until its read is finished.
- RESCAN ALL re-OCRs every unsent root PDF, including completed ones.
  Independently confirmed human corrections are retained.
- SETTINGS > AUTO SEND COMPLETED is OFF by default. When enabled, all eligible
  verified unsent PDFs are sent at normal scan completion, not STOP.
- UPDATE LEARNING is manual or allowed 90 seconds after a completed scan while idle.
  No automatic training occurs on application launch or while scanning.

PERMANENT LOCAL LOGS
- %LOCALAPPDATA%\MFCase\logs\MFCase_Work_Log.txt: append-only operation/error history.
- %LOCALAPPDATA%\MFCase\logs\MFCase_File_Trace.jsonl: original-to-sent
  destination records; combination provenance is recorded only by the actual combiner.
- SETTINGS > EXPORT WORK LOG includes the durable text history and trace.
- Neither scan logs nor original PDFs are included in GitHub release packages.

OPTIONAL COMBINER (FULL INSTALLER ONLY)
- python MFCase_Combine_And_Trace.py "<ROLL>\COMPLETED"
- Combines matching case PDFs into COMPLETED\COMBINED\08-###.pdf, preserving
  all originals and logging each input PDF and its page count.
- Existing combined PDFs are NOT overwritten unless --replace is specified.

DATA SAFETY
Keep application and learning data backed up. No background model download is
performed during scanning. The updater stages permitted code files only.
