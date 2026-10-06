# SME-Credit-4.0

A local alternative-data SME credit assessment and human-review workspace. Python FastAPI + SQLite + Next.js. Version 4.0 retains the original logistic regression and adds persistent application workflows, evidence, affordability and accessible motion.

**Practice software, not a bank or disbursement system.** The included model and impact comparison use synthetic data. Automated extraction does not verify document authenticity. Identity/business checks are documented human checks, not integrations with identity authorities or credit bureaus.

## Quick start on Windows

Install Python 3.10+ and Node.js 20.9+ (a supported Node LTS is preferred). Extract this ZIP. Open PowerShell in the extracted `SME-Credit-4.0` folder. Use two terminals.

Backend:
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
```
Frontend, in a separate terminal:
```powershell
cd frontend
npm ci
npm run dev
```
Open http://localhost:3000. The first run shows administrator setup. Open `backend/data/setup-token.txt`, copy its token into the setup form and choose a username/password (minimum 10 characters). Then sign in. The token is deleted after setup. There are no shared default passwords.

On macOS/Linux, use `.venv/bin/python` in place of the Windows Python path. The backend creates its data folder automatically.

## Use the workflow

1. **New application:** enter business information, financial indicators and cash-flow terms. Unknown values stay blank. Ratios are decimals (`0.20` = 20%). Ledger-image extraction is optional.
2. **CSV imports:** download the template, fill it and import. Valid rows become saved, clickable applications. Invalid rows get row/field messages and an error export. Row 2 is the first row after the header.
3. **Applications:** view PD, score, seven risk explanations, business health, missing-data warnings, evidence provenance and affordability.
4. **Scenarios:** combine loan, cash reserves, debt and financing changes. All panels, committee reviews and JSON exports use the selected scenario. Save an explicit revision to change the live application. Stale scenarios are rejected.
5. **Evidence:** upload PDF/JPEG/PNG/WebP (10 MB maximum). Staff download documents, record review notes and select only fields the evidence actually supports. Final approval needs identity, business, bank account and revenue document checks, complete scoring inputs, verified scoring and affordability evidence and passing affordability.
6. **Human review:** officers can request information or decline. Approvers/admins can approve or approve with conditions. Reasons are mandatory; conditional approvals require conditions. Reviewing new evidence after conditional approval returns the case to review.
7. **Disbursement:** approvers can record disbursement for fully approved cases. This is a record only; the application transfers no funds. Record scheduled repayments and full settlement dates.
8. **Administration:** create accounts and roles, change the illustrative DSCR threshold. Policy changes create new assessments for unresolved applications; historical approved snapshots remain intact.
9. **Model & data:** view definitions and validation information. Export non-demo disbursed applications and payments, then follow MODEL-DATA-GUIDE.md to train locally on your own outcomes.

## Decisions and formulas

- Model approval-ready: PD ≤ 0.15. Model committee review: 0.15 < PD ≤ 0.30. Model decline: PD > 0.30.
- Score = round(300 + 550 × (1 − PD)). This is a project score, not a credit bureau score.
- Loan ratio = loan amount / (12 × monthly revenue).
- Existing debt ratio = existing monthly repayments / monthly revenue.
- Cash reserve months = cash available / average monthly operating costs.
- Revenue gap = absolute(declared − observed monthly revenue) / declared monthly revenue.
- Growth = sum(last three complete monthly sales) / sum(previous three complete monthly sales) − 1.
- Cash available for debt service = cash collections − operating cash costs − cash taxes − owner withdrawals.
- DSCR = cash available / (existing repayments + proposed instalment).
- Instalments use equal monthly payments at a fixed nominal annual rate divided by 12 on a reducing balance. Zero rate uses principal/tenure. Fees, insurance, variable rates and other financing structures are excluded.
- Default DSCR target 1.25 is illustrative and configurable. Stress test reduces collections by 20% while holding costs fixed.

**Model recommendation, evidence verification, affordability and human workflow status are distinct.** Missing risk fields use visible synthetic assumptions only for a provisional score. Missing cash-flow fields produce affordability `unknown`. No AI output changes human status.

## Live AI (optional)

Set environment variables in the backend terminal **before starting** the backend:
```powershell
$env:ANTHROPIC_API_KEY="your-key"
$env:CLAUDE_MODEL="your-accessible-Anthropic-model-id"
```
The code has a configurable default model ID; provider availability and account access can change. Committee failures fall back to a labelled template. Ledger extraction requires live AI and returns a useful error if unavailable. It never guesses missing evidence. Confirm extracted figures before saving; corrected figures have separate provenance.

## Roles

| Role | Permissions |
|---|---|
| Applicant | Create/import and access own records and documents; run own scenarios and AI reviews |
| Credit officer | Review records/documents, request information or decline, record repayments |
| Approver | Officer capabilities plus human approval and recording disbursement |
| Administrator | Staff capabilities plus user creation, policy settings and training exports |

Applicants cannot see demo businesses or other applicants' records. Backend authorization protects reports, documents, scenarios and audits. Sessions use HttpOnly SameSite cookies, eight-hour expiry, hashed server-side tokens and salted PBKDF2 passwords. Origin checks and basic login throttling are included.

## Saved data and configuration

- `backend/data/credit.sqlite3`: business records, snapshots, scenarios, documents, reviews, users, repayment history and append-only API audit events.
- `backend/data/documents/`: protected uploads and pending extraction drafts, not a public web directory.
- Back up the complete data directory while the backend is stopped. Do not commit personal data, setup tokens, passwords, API keys or model training inputs to Git.
- Optional `SME_DATA_DIR` sets another local data path. `SME_ALLOWED_ORIGINS` is a comma-separated list of permitted frontend origins; localhost:3000 and backend localhost:8000 are the local defaults.
- For HTTPS deployments, set `SME_SECURE_COOKIES=1` and configure allowed origins. The supplied frontend proxy points to 127.0.0.1:8000.
- `SME_MODEL_PATH` activates a **trusted local** artifact created by train_real.py; never load an untrusted pickle/joblib file.

## Motion

Numbers count from zero to their exact formatted value, the credit arc fills from empty, and factor/health/sector bars grow from zero. Animations cancel when values change or components unmount. OS/browser reduced-motion preferences render final values immediately. Accessible labels expose final values.

## Tests

```powershell
cd backend
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest tests -q
```
Frontend: `npm run build` inside `frontend`. Integration tests use an isolated temporary database, never your application data.

## Remaining real-world work

Real repayment data and identity/bank integrations are not supplied. The included tools cannot certify input authenticity, payment coverage, regulatory compliance or model fairness. The repayment recorder handles scheduled full-settlement records; partial-payment allocation and collections systems are outside this version. There is no actual funds-transfer integration. Before a real deployment, add your institution's controls, hardened hosting, backups, account recovery/revocation and independent model review.
