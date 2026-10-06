# Version 4.0 validation

## Automated backend checks

24 tests passed using a temporary SQLite database. Coverage includes:
- First-admin bootstrap, authentication, session access and untrusted-origin rejection.
- Zero/negative/nonfinite inputs, boolean numeric inputs and ratio/tenure bounds.
- CSV missing/duplicate columns, empty files, invalid rows and partial success.
- Saved imported-business full assessments and explicit missing-data assumptions.
- Zero-rate instalments, DSCR, unknown affordability and a sales-drop stress test.
- Scenario validation, exact scenario committee/report inputs, saved revisions and stale-scenario rejection.
- Applicant ownership restrictions, officer permissions and admin-only training/audit access.
- Document content checks, protected downloads, field verification, approval guards and audit records.
- Conditional approvals reopening for evidence and application revisions.
- Disbursement record and full-settlement repayment records.
- Ledger zero-sales months, dated six-month growth and extracted/corrected provenance.
- Policy re-assessment and restart persistence.
- Real-data tool outcome labels, immature horizons, decision-time leakage rejection, chronological/business-separated splits and local model artifacts.

## Frontend checks

Production build and TypeScript checks passed with Next.js 16.3.8 / React 19.3.0. The production dependency audit returned zero reported vulnerabilities at verification time.

Headless Chromium checks passed for administrator setup/login, saved application selection, partial-success CSV import, clicking an imported business into full assessment, manual application entry, full cash-flow inputs, affordability rendering, combined loan scenarios, labelled offline committee review, matching scenario JSON export and explicitly saving a revision. Animated score and factor bar reached their final targets. No browser page errors were observed. The 390-pixel mobile layout had no horizontal page overflow. Light, dark and mobile previews are included.

## Explicit limits of verification

Live Anthropic calls were not tested with an API key. Extraction and offline fallback paths are covered with controlled fixtures. The real-data trainer was tested using synthetic fixtures solely to check code behaviour; no real-world repayment data or accuracy certification is supplied. Human document verification is a recorded workflow, not automated authenticity verification. No identity authority, credit bureau, actual bank transfer or partial-payment allocation integration is included.
