MFCASE V5.5.5 INSTALLATION AND REPAIR

1. Extract the complete ZIP to a writable application folder (not inside a ZIP).
2. Run SETUP.bat once. It finds existing Python 3.11 installations, verifies the bundle, creates the desktop shortcut, and installs only missing packages.
3. Use the desktop shortcut 'M.F. Case Number OCR'. No console window should remain open.
4. RUN_CASE_OCR.bat is a quiet launcher; RUN_CASE_OCR_DEBUG.bat shows errors for diagnostics.
5. If the app does not launch, check %LOCALAPPDATA%\MFCaseOCR\logs\startup_error.txt.

SCAN PERFORMANCE
- Confirmed 99.5% original INCIDENT NO. detections skip redundant field OCR.
- A unique detector-localized complete 08- case at >=95% can stop reading subsequent pages of a multi-page PDF.
- Uncertain, incomplete, and conflicting reads continue through the original slower review path.
- STOP now interrupts between PDF pages as well as between separate files.
- Background learning starts after an initial idle delay and yields to scanning.

DATA SAFETY
Source PDFs, learning databases, model checkpoints and sent-history records are not included or deleted by the installer/updater. The updater only downloads signed-by-digest release assets; network connectivity does not prove that data is safe.

A shortcut or successful installer does not guarantee OCR models are present. Use PREPARE OFFLINE MODELS if required.
