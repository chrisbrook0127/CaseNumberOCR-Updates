# MFCase status
Production baseline: 5.5.8. Development: 5.5.9.
Scope: bounded OCR calls and literal 08- suggestions from either engine.
Locked: upper-right 4x4, manual full-page crops, 95% approval, before/after anchors, existing learning data and checkpoints preserved.
Validation: focused prefix, confidence selection, call-bound, scan ROI, and source-PDF preservation tests.
Known limitation: no claim that OCR confidence guarantees accuracy or eliminates 3/1 confusion. Real Windows and trained-model recognition not validated here.
Next: review real-world logs and confirmed corrections after deployment.
