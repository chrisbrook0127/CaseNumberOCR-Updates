# MFCase status
Production baseline: 5.6.2. Release: 5.6.3.
Fixed: complete-number digit concatenation, distinct-field conflict suppression, before/after matching past conflicting automatic anchors.
Improved: nearby label geometry, split labels/prefixes, crop prioritization, deduplication, padding, blank/word rejection and local raw diagnostics.
Locked: upper-right quarter; either-engine literal 08-; 95%; max one detector and three handwriting calls; manual protection; learning/data preservation.
Validation: 24 focused tests pass. Three defect tests fail on 5.6.2 and pass on 5.6.3.
Limitations: available work log is 5.5.8. Screenshots are not original PDF samples. No complete detector/TrOCR model available; no measured recognition-accuracy improvement claimed.
Next evidence needed: original PDFs for misreads (e.g. Image5808, Image5841, handwritten 336/136 and 01114/101) plus a new-version work log. Trained checkpoint needed only for reproducing the user's exact learning behavior.
