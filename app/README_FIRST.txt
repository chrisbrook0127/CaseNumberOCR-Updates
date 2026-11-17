MFCASE NUMBER OCR V5.3.5 - APPLICATION AND ONLINE DEPENDENCY INSTALLER

Existing users: use UPDATE in the app. Existing OCR model files are reused.
This ZIP contains application code, launchers and dependency setup. It does
not contain Python, third-party packages, OCR weights or learning examples.
The PREPARE OFFLINE MODELS button has been removed. A fresh installation
requires previously installed/restored local OCR model files before scanning.

INSTALL/RESTORE
1. Extract all files to a permanent local application folder, separate from PDFs.
2. Run SETUP.bat only if Python or dependencies need installation (internet needed).
3. Keep your existing local OCR model files and learning directory in place.
4. Launch RUN_CASE_OCR.bat or the desktop shortcut and select your document root.

SCAN has a bottom count: ## files displayed.
Double-click the case-number cell to edit. Enter or clicking away saves a valid
change and marks the file COMPLETED. Esc cancels. EXPORT COMPLETED moves all
approved files after the scan's final before/after check; review files remain.
Leading zeros are preserved. Cases use 08- followed by one to seven digits.
The recognition path applies to that entire range, including thousands.

Use the top slider to choose 75-100% (default 95%) for new scans. Either engine
meeting it passes the confidence gate; location, ambiguity, required
08- format and final before/after safeguards remain. All NEW saved examples are
categorized handwritten. Existing examples, crops and checkpoints are not
migrated or recategorized. Inline corrections append new examples instead of
relabeling historical ones. UPDATE LEARNING remains a separate manual action.

See CHANGELOG_V5_3_5.txt for validation and limitations.

BEFORE/AFTER reruns the final check after scanning. Only independently auto-
confirmed or manually confirmed files anchor. Matching anchors override a
conflicting middle entry, including manual entries. Multiple conflicts require
review. Inferred results cannot anchor further corrections. Search starts in
the upper-right 3x3 and expands to 5x5 only if no valid 08- candidate is found.
