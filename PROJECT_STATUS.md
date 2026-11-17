# MFCase status

Production baseline: V5.3.2. Release candidate: V5.3.3.

V5.3.3 adds EXPORT COMPLETED for all eligible approved files independent of selection, exact leading-zero preservation, a persisted final-sequence-check export barrier, and direct whole-line recognition to reduce detector-related missing digits. Earlier human corrections and learning remain intact. Two-engine thresholds and conflict safeguards remain unchanged.

Validation: 44 regression tests cover parser, OCR confidence, sequence decisions, inline editing, persistence, all-completed export, preservation of review files and exact digit strings, collision safety, and updater installation/rollback. Local screenshot tests recover the printed 08-0119 and both 08-120 crops. This does not establish full-page/two-engine accuracy or reliable handwriting recognition; uncertain cases remain reviewable. Native Windows GUI testing remains outstanding.

Locked requirements: literal 08- plus 1–7 digits; no automatic transfers; explicit EXPORT COMPLETED after full scan and before/after check; manual corrections protected; separate manual UPDATE and UPDATE LEARNING; offline OCR/learning after setup; retain existing model/examples. Inferred results cannot anchor chained guesses. No confidential input or learning data is included in release assets.

Next: publish V5.3.3, verify the old updater against the actual assets, and validate full-resolution failing PDFs on Windows.
