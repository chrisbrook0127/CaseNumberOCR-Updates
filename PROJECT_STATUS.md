# MFCase status
Production: V6.0.0. Last approved baseline: V5.6.20.
Completed: visible original source crops; red-plus-green individual sending; full-scan/pre-send case-order popup with two neighbors on each side.
Locked: _sequence_case_number, _sequence_consensus_plan, _apply_sequence_consensus_impl and _flag_context_outliers remain source-identical to V5.6.20 and are hash-tested. A review gate runs ahead of them.
Forward gaps and duplicates are allowed. Backward steps require inspection, not an invented replacement. Sent/excluded context is retained. Raw OCR and explicit review acknowledgements persist.
209 tests passed; existing PDFs, crops, examples and checkpoints preserved. Windows UI/SMB timing not exercised here.
Next: review user work logs for actual popup workflow and thumbnail behavior.
