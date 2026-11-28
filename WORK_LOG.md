# Development work log — MFCase (public, sanitized)

## 2026-11-27 — Infrastructure setup
- Verified public release feed plus historical app source/build/test archive. Main base a7deaa9fd60916ab324105719292e651eb85b709; stable v6.0.3 and test v6.0.4-rc1 have different source references (APP_BASELINE.md).
- Added development records, Windows CI adapter and non-publishing workflow. Reuses actual existing release scripts and regression suites. No modification to app, tests, existing publishing workflows, release assets, or sibling project.
- Existing historical PROJECT_STATUS.md and PUBLISH_SCOPE.md retained. Live CI outcomes and PR review pending at this commit; record actual results after observation. Private logs and data remain local/private.
