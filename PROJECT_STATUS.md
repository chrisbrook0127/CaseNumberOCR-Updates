# MFCase status

Published production version: V5.3.9. Previous verified baseline: V5.3.8. No separate development version.

Changes: validated numeric identity ignores leading padding for OCR ambiguity checks and confirmed before/after anchors. 08-0084 and 08-84 match in either direction and can resolve multiple middle files as 08-84. Original OCR/confirmed/training labels remain intact. No OCR rescan is added; filenames already strip leading padding safely.

Locked: single upper-right 4x4; 08- plus 1-7 digits; 75-100 slider default 95; independently confirmed anchors; inference never chains; fresh revalidation before rename and after decisions; explicit in-place collision-safe renaming; green-check acceptance of drafts; unchanged learning examples, training loop/minimums and checkpoint behavior.

Validation: 96 tests passed locally; package integrity passed. GitHub Actions run 44642925924 passed. Live V5.3.8 updater discovered, downloaded, validated and installed V5.3.9; synthetic PDFs and historical learning unchanged; published full installer matches local package. Native Windows GUI and real-document OCR accuracy unverified.
