# MFCase status
Production baseline: V5.6.12 user-supplied installer. Development: V5.6.13.
Approved changes: universal top training spinner; separate auto95 sent-location observations; shared page OCR budget.
88 tests passed locally. Recognition accuracy and Windows end-to-end behavior require workstation validation.
Existing learning examples/checkpoints and PDF contents remain unchanged.
Known pre-existing limitation: learned locations outside the quarter are read, but the UI auto-approval gate still checks quarter bounds. Not changed without approval.
