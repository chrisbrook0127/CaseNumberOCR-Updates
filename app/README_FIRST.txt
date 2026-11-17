MFCASE NUMBER OCR V5.3.3 - FULL ONLINE INSTALL PACKAGE

Existing users: press UPDATE in the application to install the published update.
Use this full package for a fresh installation or a reference copy.

INSTALL
1. Extract ALL files to a permanent local application folder, separate from case documents.
2. Run SETUP.bat to install Python and required packages (internet required).
3. Start with the desktop shortcut or RUN_CASE_OCR.bat.
4. On a new computer, click PREPARE OFFLINE MODELS while connected.
5. Select your existing document root. Old human corrections remain saved;
   previous automatic approvals need rescanning under the new rules.

Python, dependencies and model weights are downloaded, not embedded in this ZIP.
Normal scanning and learning operate offline after setup. Department shares
still require access to the department network. UPDATE and UPDATE LEARNING
remain separate manual buttons. Documents wait for EXPORT COMPLETED.

EDIT: Double-click a case-number cell; Enter saves, Esc cancels.
Double-click the filename to open document review.

APPROVAL: Both engines must read the same full 08- number above 75%, with
at least one above 95%. The final before/after pass can correct a single
conflicting OCR result, including 100% reads. Multiple conflicts need review.
Manual corrections are protected. No handwriting/printed selection is needed.

See CHANGELOG_V5_3_3.txt for detailed behavior and validation limits.

EXPORT COMPLETED moves all approved files, regardless of selection, leaving review files in the root. Leading zeros are retained exactly (08-01114 stays 08-01114). Export waits for the final before/after pass after scanning.
