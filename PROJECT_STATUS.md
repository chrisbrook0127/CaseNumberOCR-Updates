# MFCase status

Published production version: V5.3.4 (2026-11-17). Previous production baseline: V5.3.3. No separate development version.

V5.3.4 adds a bottom displayed-file count, focus-loss completion for changed valid inline edits, removal of the model-preparation button, 100%-either-engine confidence approval, and handwritten categorization of new examples only. Historical category support, examples, crops and checkpoints are retained. Inline corrections append learning rather than relabeling old examples.

Validation: 52 regression tests passed locally and in GitHub Actions run 44118287939. The V5.3.3 updater discovered, downloaded, validated and installed the actual V5.3.4 asset; synthetic documents and learning remained unchanged. The published full installer matches the supplied package. Local synthetic printed crop recognition recovered all 12 expected strings spanning 1-7 digits, including thousands and leading zeros. This is not a real-document accuracy guarantee. Native Windows GUI testing remains outstanding.

Locked behavior: 08- plus 1-7 digits; leading zeros preserved; explicit EXPORT COMPLETED after the final before/after check; manual corrections protected; multiple conflicts require review; no inference chains; separate manual UPDATE/UPDATE LEARNING; offline installed models and learning. Fine-tuning loop and learning minimums unchanged. New confidence rules apply on rescanning; prior approvals are not silently recategorized.

Next priority: verify native Windows click-away behavior and full-resolution scan accuracy. Fresh installations without local OCR weights require restoring model files; the download button is deliberately removed.
