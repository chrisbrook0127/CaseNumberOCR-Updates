# MFCase status

Production baseline: V5.3.7. Development release: V5.3.8.

Changes: exact-string anchors and OCR conflict comparisons; fresh inference plan on every check; stale approvals withdrawn; automatic recheck after human decisions and loading; mandatory pre-rename revalidation with failure barrier; original inference evidence persisted. Filename-only leading-zero removal; all OCR/confirmed/training digits preserved. UNVERIFY preserves historical learning data.

Locked: single upper-right 4x4; 08- plus 1-7 digits; 75-100 slider default 95; confirmed independent anchors; inference never chains; explicit in-place collision-safe renaming; green-check acceptance of drafts; unchanged training loop/minimums and checkpoints.

Validation: 91 tests passed locally. Live release/updater verification pending. Windows GUI and real OCR accuracy unverified. Existing filenames change only through explicit rename, even when a previous inference is withdrawn. Next priority: real-document accuracy and Windows usage feedback.
