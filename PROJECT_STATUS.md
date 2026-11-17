# MFCase status

Production baseline: V5.3.1. Release candidate: V5.3.2.

V5.3.2 implements complete-token parsing, number-only crop recovery, separate per-engine scores, strict >75% both / >95% either on matching case values, frozen final before/after correction (including 100% conflicts), manual review for multiple conflicts, inline case editing, and removal of classification choices.

Locked behavior: literal 08- plus 1–7 digits; preserve manual corrections and original PDFs; no automatic transfers; separate manual UPDATE and UPDATE LEARNING; offline OCR and learning after setup; retain existing models/examples and updater rollback. Existing automatic approvals from older rules need rescan.

Validation: 37 automated tests cover parser, engine scores, sequence, editing routes, persistence, update installation, path rejection, and rollback. Windows GUI and actual failing handwriting samples have not been tested in this environment. No accuracy improvement is claimed without samples. The baseline and candidate both use the shared TrOCR model; existing training categories remain compatible but are no longer selected by users.

Next: verify the published asset through the old updater, then confirm actual handwriting examples on Windows.
