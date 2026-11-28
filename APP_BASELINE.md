# Baseline ledger — MFCase
Verified public GitHub release metadata and tag references, 2026-11-27 (US Central).

| Role | Version | Immutable source reference |
| --- | --- | --- |
| Published stable / rollback | v6.0.3 | 08a640b3a1bffba03f1e4f3b1d3014734bc3d3e5 |
| Published prerelease, NOT stable | v6.0.4-rc1 | ba227250386578547b94ff3b2693d41402af72e0 |
| Historical main, infrastructure PR base only | historical app source | a7deaa9fd60916ab324105719292e651eb85b709 |

Stable release: https://github.com/chrisbrook0127/CaseNumberOCR-Updates/releases/tag/v6.0.3
Stable full installer: MFCase_Number_OCR_V6.0.3_Full_Online_Install.zip, SHA-256 expected in existing tools/verify_v603.py: 25c59ded15c502a9fdf087a9c933e822c5531bd849b090bfe0b19a53a762e3ca.
Stable updater: CaseNumberOCR_Update.zip, expected SHA-256: 2f2fdec1d19d26720a4e622f632d3678f1c914272403961c2e21fef410d22a76.
Test release: https://github.com/chrisbrook0127/CaseNumberOCR-Updates/releases/tag/v6.0.4-rc1; installer MFCase_Number_OCR_V6.0.4_TEST_Full_Online_Install.zip, updater CaseNumberOCR_Update.zip, SHA256SUMS.json. Existing builder verifies stable input and patch hashes. Actual local installed version and local user-designated source/archive are not verified in this setup. Published stable is not a substitute for explicit user approval of a different development baseline. Do not promote RC1 or infer main is current. Preserve existing updater repository/asset name, previous installer and local data. Source verification and Windows execution are independent of tag metadata. See CI_ADAPTER.md.
