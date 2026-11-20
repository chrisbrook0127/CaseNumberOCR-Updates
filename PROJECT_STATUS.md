# MFCase status

Production and last verified baseline: V5.3.8. Development release: V5.3.9.

Changes: validated numeric identity ignores leading padding for OCR ambiguity checks and confirmed before/after anchors. 08-0084 and 08-84 match in either direction and can resolve multiple middle files as 08-84. Original OCR/confirmed/training labels remain intact. No OCR rescan is added; filenames already strip leading padding safely.

Locked: single upper-right 4x4; 08- plus 1-7 digits; 75-100 slider default 95; independently confirmed anchors; inference never chains; fresh revalidation before rename and after decisions; explicit in-place collision-safe renaming; green-check acceptance of drafts; unchanged learning examples, training loop/minimums and checkpoint behavior.

Validation: 96 tests passed locally; package integrity passed. Live release/updater verification pending. Native Windows GUI and real-document OCR accuracy unverified.
