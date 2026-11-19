# MFCase status

Published production version: V5.3.6. Previous working baseline: V5.3.5. No separate development version.

V5.3.6 removes the veto on two or more conflicting middle files between matching confirmed anchors. Competing boundary ranges still require review. Unconfirmed and inferred cases cannot anchor. Explicit export and the all-scans-finished barrier remain.

New learning examples use the original number box even when its OCR text differs from the manual correction. Drawn rectangles take priority and captions are trimmed when detectable. Missing crops report a learning failure. Saving an example is distinguished from training. All historical examples/crops/checkpoints are untouched. Fine-tuning loop and thresholds unchanged.

Validation: 65 regression tests passed locally and in GitHub Actions run 44142290652. Live V5.3.5 updater discovered, downloaded, validated and installed V5.3.6; synthetic documents and historical learning were preserved. Published full installer matches the supplied package. Native Windows UI and the user's trained model have not been tested. No claim of corrected handwriting accuracy; UPDATE LEARNING is still required after collecting corrections.

Next priority: verify real-document handwriting results and native Windows UI behavior after installation.
