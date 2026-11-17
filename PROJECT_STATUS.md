# MFCase status

Production baseline: V5.3.3. Release candidate: V5.3.4.

V5.3.4 adds a bottom displayed-file count, focus-loss completion for changed valid inline edits, removal of the model-preparation button, 100%-either-engine confidence approval, and handwritten categorization of new examples only. Old categories/data/checkpoints remain compatible and unmigrated. Inline corrections append new learning instead of rewriting old labels.

Validation: 52 regression tests. Local synthetic printed crop recognition recovered all 12 expected strings across one through seven digits, including thousands and leading zeros. No hard-coded case-specific substitutions or three-digit limit. Native Windows GUI and broad real-world handwriting accuracy remain unverified.

Locked behavior: 08- plus 1-7 digits, leading zeros preserved, explicit EXPORT COMPLETED after final before/after review, manual corrections protected, no inference chains, multiple conflicts to review, separate manual UPDATE/UPDATE LEARNING, local offline models/learning. Fine-tuning loop and example minimums unchanged.

Next: publish and verify update using the previous updater. Fresh installations without cached OCR weights require restoring model files; the download button is deliberately removed.
