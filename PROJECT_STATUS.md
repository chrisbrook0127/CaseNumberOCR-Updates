# MFCase status

Production baseline / last verified version: V5.3.9. Development release: V5.4.0.

Implemented: scan controls restored, APPROVE label, Ctrl+A all rows and Shift-click ranges; valid inline edits approve and persist; legacy root exported flags reopened for sending. After sequence review approved PDFs automatically rename as original_case.pdf, preserving original order. Explicit SEND FILES moves approved PDFs into COMPLETED with collision protection; review remains in root. Literal 08- gates, same-4x4 fallback when label-only recognition fails, no arbitrary fallback thumbnails, drawn-crop saving without full-page OCR or finish-toggle requirement.

Locked: original PDF contents unchanged; no historical learning migration or deletion; manual UPDATE LEARNING; thresholds 75-100 default95; padded case identities match; inference never chains and competing ranges require review. No second whole-PDF scan and no 5x5.

Validation: 104 automated tests and package integrity passed locally. Live release/updater verification pending. Native Windows UI and real OCR examples remain unverified; user images arrived as literal image text. Next priority: validate reported handwriting samples and Windows interaction.
