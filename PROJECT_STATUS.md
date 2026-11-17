# MFCase status

Production baseline: V5.3.4. V5.3.5 release prepared 2026-11-17.

V5.3.5 adds BEFORE/AFTER at the top and a 75-100% confidence slider, default 95%, applied to new scans. Either engine can qualify. Only independently auto-confirmed or manually confirmed cases anchor. Matching anchors override conflicting middle entries, including manual entries; multiple/competing conflicts require review. No inference chains. The button cannot bypass an unfinished scan's export barrier.

Strict 08- plus 1-7 digits, zeros preserved, upper-right 3x3 then 5x5 only without a valid candidate. Explicit EXPORT COMPLETED remains required. Historical learning data untouched; new examples handwritten. Learning minimums and fine-tuning unchanged.

Validation: 59 regression tests locally; release and live updater verification pending. Native Windows GUI testing outstanding. Confidence is not measured real-world accuracy.
