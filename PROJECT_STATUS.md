# MFCase status

Production baseline: V5.3.5. V5.3.6 release prepared 2026-11-17.

V5.3.6 removes the veto on two or more conflicting middle files between matching confirmed anchors. Competing boundary ranges still require review. Unconfirmed and inferred cases cannot anchor. Explicit export and the all-scans-finished barrier remain.

New learning examples use the original number box even when its OCR text differs from the manual correction. Drawn rectangles take priority and captions are trimmed when detectable. Missing crops report a learning failure. Saving an example is distinguished from training. All historical examples/crops/checkpoints are untouched. Fine-tuning loop and thresholds unchanged.

Validation: 65 regression tests passed locally. Live release/updater check pending. Native Windows UI and the user's trained model have not been tested. No claim of corrected handwriting accuracy; UPDATE LEARNING is still required after collecting corrections.
