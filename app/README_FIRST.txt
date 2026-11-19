MFCASE NUMBER OCR V5.3.7 - APPLICATION AND ONLINE DEPENDENCY INSTALLER

Existing users: SETTINGS > UPDATE. Existing OCR models and learning are reused.
This ZIP contains application code, launchers and dependency setup, not Python,
third-party packages, OCR weights or learning examples. Fresh installations
require installed/restored local OCR models. No model-preparation button.

INSTALL/RESTORE
1. Extract into a permanent application folder, separate from your PDFs.
2. Run SETUP.bat if Python or dependencies need installation (internet needed).
3. Keep existing model files and the learning directory in place.
4. Launch RUN_CASE_OCR.bat or your shortcut.

SCAN WORKFLOW
1. SELECT FILES, then SCAN. One upper-right 4x4-inch region is searched on each
   page. Existing crop enhancements retry difficult text; no 5x5 expansion and
   no identical second whole-document scan.
2. Either engine can meet the slider threshold (75-100%, default 95%). Only
   complete 08- plus 1-7 digits qualifies; leading zeros remain.
3. Click the green check beside a case number to accept it, even below threshold.
   Double-click the case-number cell to change it. Enter/click-away saves a
   draft; click the green check to approve and complete. Escape cancels editing.
   Double-click the thumbnail or filename to inspect the document.
4. BEFORE/AFTER runs after scanning and can be rerun with the button. Matching
   independently confirmed cases on BOTH sides can correct multiple middle
   files. Inferred results cannot anchor. Unaccepted drafts must be approved
   first. Competing boundary answers remain REVIEW. The button reports its result.
5. RENAME APPROVED renames all approved PDFs IN THEIR CURRENT FOLDER. No copy
   and no move into COMPLETED. Existing names are protected; same-case files get
   unique (1), (2), ... suffixes. Already matching case filenames are retained.
   Renamed files remain displayed; unapproved files keep their original names.

Settings contains UPDATE, UPDATE LEARNING, logs, refresh/rescan and other
secondary controls. The bottom of SCAN shows the number of files displayed.

Learning saves a NEW handwritten example when a user accepts/corrects a number.
UPDATE LEARNING trains separately once enough usable examples exist. Existing
examples, categories and checkpoints are untouched. Confidence is not measured
accuracy. The repeated-digit fix removes overlapping identical OCR detections;
it does not shorten legitimate case numbers or guarantee model correctness.

See CHANGELOG_V5_3_7.txt for validation and limitations.
