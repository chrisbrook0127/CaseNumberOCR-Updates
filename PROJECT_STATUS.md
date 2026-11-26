# MFCase status
Production: V5.6.16, based on published V5.6.15.
Approved scope: send/clear UI recovery; STOP cancellation and safe close; last-folder restore; green approval learning; ten-neighbor outlier review; idle local learning.
132 regression tests passed. Printed OCR and inference call limits unchanged.
Matching before/after and manual corrections retain precedence; outliers are reviewed, never numerically guessed.
Existing PDFs, examples, crops and checkpoints preserved.
Limitations: cancellation completes the current inference/write safely. No live Windows/SMB or recognition-accuracy validation; real PDFs/models unavailable.
