# MFCase status

Production baseline: V5.4.1.

Fixes: immediate selection synchronization; SCAN uses visible selection, offers explicit rescan for unresolved prior scans, reports all blocked starts, catches startup exceptions and restores idle controls. Busy/sending disables SCAN. Diagnostic header reflects 4x4 and actual threshold.

Preserved: literal 08- prefix, padded anchors, original-name automatic renaming, explicit SEND FILES to COMPLETED, all historical learning/model data and training behavior.

Validation: 109 tests passed, including scan-start worker dispatch, stale selection, thread failure recovery and visible block reasons. GitHub release workflow 45167193170 succeeded. Live V5.4.0 updater discovered, downloaded and installed V5.4.1 in a temporary installation; synthetic document and historical learning crop retained unchanged. Published full installer matches the local build.

User-specific click failure not reproduced on native Windows; visible errors now expose its cause if it persists.
