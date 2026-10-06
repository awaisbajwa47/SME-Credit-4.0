# Real-data training and validation

The bundled dashboard works immediately with a synthetic model. Its predicted PD has no defined real-world default horizon. Installing version 4.0 does not make that model accurate for real applicants.

## Outcome definition

The offline trainer targets **an instalment reaching 90 or more days past due within 12 calendar months of disbursement**. It requires a complete 12-month observation period for both positive and negative labels. Leap-day anniversaries use the final valid day of February. Unpaid instalments are followed up to the horizon end; settlements after the horizon do not erase an earlier default event.

## Prepare applications.csv

Required columns:

`application_id,business_id,disbursed_at,snapshot_at,monthly_revenue,loan_amount,business_age_years,revenue_growth_6m,revenue_volatility,cash_reserve_months,existing_debt_ratio,digital_payment_share,utility_on_time_rate,customer_concentration,revenue_gap`

Optional columns are the ApplicationInput fields documented in README.md, including `name,sector,city`. Each ID represents a disbursed application; the same business ID must be used consistently. Dates use YYYY-MM-DD. snapshot_at must refer to the decision-time inputs at or before disbursement. All nine risk fields must be observed, not filled with assumed defaults. Do not include rejected applications as known nondefaults. Do not include synthetic demo records.

The Administration account can export real non-demo disbursed application snapshots and payments from Model & data. Complete the repayment schedule before training. Protect the export files because they contain business financial information.

## Prepare repayments.csv

Required columns:

`application_id,due_date,paid_date,amount_due,amount_paid`

Include **every scheduled instalment**, including unpaid ones. Blank paid_date means not fully settled by the observation cutoff; amount_paid must then be zero in this simplified full-settlement dataset. For a paid instalment, paid_date is the date of full settlement and amount_paid must be at least amount_due. Partial-payment allocation is not supported. Combine/reconcile your source payments into full settlement records rather than using the first partial payment date. One record per application and due date. Include the known schedule as well as collection records; an absent schedule must not be interpreted as successful repayment.

The trainer cannot independently establish that omitted payments do not exist. `--confirm-complete-outcomes` is an explicit operator confirmation, not automated verification.

## Train offline

From the backend directory:

```powershell
.\.venv\Scripts\python train_real.py --applications applications.csv --repayments repayments.csv --as-of 2026-10-06 --output artifacts/real-model --confirm-complete-outcomes
```

Use your actual observation cutoff. The cutoff must not precede supplied full-settlement dates. The tool excludes immature applications, validates inputs, trains on older dates, tests on later dates, and excludes test records from businesses represented in training. It requires at least 30 mature records, at least 15 training / 10 test records, and both classes in each split. These are execution guards, not sufficient sample sizes for reliable real lending validation. Substantially larger representative data is needed.

Outputs:
- `credit-model.joblib`: fitted scaler and logistic regression, compatible feature list, version and report.
- `validation-report.json`: AUC, Brier loss, log loss, average PD, observed default rate, approval rate, calibration bins, sector metrics, observation cutoff and excluded counts.
- `labelled-outcomes.csv`: the derived target labels and complete inputs.

Calibration bins compare predicted probability with observed default frequency. AUC measures ranking, not probability calibration or accuracy. Brier loss mixes calibration and discrimination. This tool reports calibration; it does not automatically recalibrate or certify a model. Use separate validation data if you introduce calibration, policy selection or tuning; never tune on the final test set.

## Activate deliberately

After independent review, set the local trusted path before restarting the backend:

```powershell
$env:SME_MODEL_PATH="C:\your-project\backend\artifacts\real-model\credit-model.joblib"
.\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Only load locally trusted artifacts; joblib/pickle loading can execute code. No web model-upload endpoint exists. Model & data shows the active model version and report. Existing assessments preserve the model version that originally scored them; create an application revision or update unresolved policy assessments to obtain a new-model assessment. The fixed Inclusive credit impact card remains labelled as a separate synthetic benchmark, even with a real active model.

## Limits

Previously approved loans are a selected sample; their model may not generalize to rejected applicants. Sector comparisons are not a complete fairness audit. Collect representative data, inspect errors and calibration, document missingness, evaluate temporal drift and stress conditions, validate your outcome coverage, and define appropriate policy thresholds with your lending team. The default 15% / 30% thresholds remain illustrative constants until separately validated.
