# MFCase status

Published production version: V5.3.2 (2026-11-17). Previous production baseline: V5.3.1. No separate development version currently.

V5.3.2 implements complete-token parsing, number-only crop recovery, separate per-engine scores, strict >75% both / >95% either on matching case values, frozen final before/after correction (including 100% conflicts), manual review for multiple conflicts, inline case editing, and removal of classification choices.

Locked behavior: literal 08- plus 1–7 digits; preserve manual corrections and original PDFs; no automatic transfers; separate manual UPDATE and UPDATE LEARNING; offline OCR and learning after setup; retain existing models/examples and updater rollback. Existing automatic approvals from older rules need rescan.

Validation: 37 automated regression tests passed locally and in GitHub Actions. V5.3.1 updater code was tested against the actual published V5.3.2 release: discovery, download validation, byte comparison, and installation all passed; synthetic document and learning files were preserved. Published full installer matches the supplied download exactly. Release workflow 44096885524 succeeded.

Limitations: Windows GUI and actual failing handwriting samples have not been tested in this environment. No measured handwriting-accuracy improvement is claimed. Existing learning categories remain compatible with the single shared TrOCR model but are no longer selected by users.

Next priority: validate actual handwriting samples and native Windows interaction; investigate any reported failures without changing the established offline/manual-transfer behavior.
