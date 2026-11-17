# MFCase status

Published production version: V5.3.3 (2026-11-17). Previous production baseline: V5.3.2. No separate development version.

V5.3.3 adds EXPORT COMPLETED for all eligible approved files independent of selection, exact leading-zero preservation, a persisted final-sequence-check export barrier, and direct whole-line recognition to reduce detector-related missing digits. Earlier human corrections and learning remain intact. Two-engine thresholds and conflict safeguards remain unchanged.

Validation: 44 regression tests passed locally and in GitHub Actions (run 44100048400). V5.3.2 updater code discovered, downloaded, validated and installed the actual V5.3.3 asset, preserving synthetic PDF and learning files. Published full installer matches the supplied package. Local EasyOCR checks of four screenshot crops recovered printed 08-0119, both printed 08-120 examples, and one handwritten 08-118 crop above 75%. This is a crop-level test, not a full-page/two-engine accuracy benchmark. Native Windows GUI testing remains outstanding; uncertain cases remain reviewable.

Locked requirements: literal 08- plus 1–7 digits; preserve leading zeros; no automatic transfers; explicit EXPORT COMPLETED after full scan and before/after check; manual corrections protected; separate manual UPDATE and UPDATE LEARNING; offline OCR/learning after setup; retain existing model/examples. Inferred results cannot anchor chained guesses. No confidential input or learning data is included in release assets.

Next priority: verify full-resolution failing PDFs and native Windows interaction; preserve existing offline and manual-export behavior.
