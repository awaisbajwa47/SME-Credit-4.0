"""Logistic credit model with direct standardized linear contributions.

Factors show one-at-a-time probability sensitivity against the training mean.
They are not additive probability-space SHAP values. Path-to-approval searches
bounded hypothetical changes using illustrative effort costs.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

FEATURES = ["loan_ratio", "existing_debt_ratio", "revenue_volatility", "customer_concentration",
            "revenue_gap", "cash_reserve_months", "digital_payment_share", "utility_on_time_rate",
            "business_age_years", "revenue_growth_6m", "log_revenue"]

LABELS = {
    "loan_ratio": "Loan size vs annual revenue",
    "existing_debt_ratio": "Existing debt payments",
    "revenue_volatility": "Revenue volatility",
    "customer_concentration": "Reliance on top customer",
    "revenue_gap": "Declared vs bank-observed revenue gap",
    "cash_reserve_months": "Cash reserves",
    "digital_payment_share": "Share of digital payments",
    "utility_on_time_rate": "Utility bills paid on time",
    "business_age_years": "Business age",
    "revenue_growth_6m": "Six-month revenue growth",
    "log_revenue": "Business size",
    "loan_amount": "Requested loan amount",
}

APPROVE_PD = 0.15   # default probability at or below this: approve
DECLINE_PD = 0.30   # above this: decline; in between: refer to committee


def fmt(key: str, v: float) -> str:
    if key in ("existing_debt_ratio", "digital_payment_share", "utility_on_time_rate",
               "customer_concentration", "revenue_gap", "revenue_volatility", "revenue_growth_6m"):
        return f"{v * 100:.0f}%"
    if key == "loan_ratio":
        return f"{v * 100:.0f}% of annual revenue"
    if key == "cash_reserve_months":
        return f"{v:.1f} months"
    if key == "business_age_years":
        return f"{v:.1f} years"
    if key in ("loan_amount", "monthly_revenue"):
        return f"Rs. {v:,.0f}"
    if key == "log_revenue":
        return f"Rs. {np.exp(v):,.0f} per month"
    return str(v)


def to_features(rec: dict) -> pd.DataFrame:
    row = dict(rec)
    row["loan_ratio"] = rec["loan_amount"] / (rec["monthly_revenue"] * 12)
    row["log_revenue"] = float(np.log(rec["monthly_revenue"]))
    return pd.DataFrame([row])[FEATURES]


def decision_for(pd_: float) -> str:
    if pd_ <= APPROVE_PD:
        return "approve"
    return "decline" if pd_ > DECLINE_PD else "refer"


def score_for(pd_: float) -> int:
    return int(round(300 + 550 * (1 - pd_)))


class CreditModel:
    def fit(self, df: pd.DataFrame):
        X = pd.concat([to_features(r) for r in df.to_dict("records")], ignore_index=True)
        y = df["defaulted"].astype(int).values
        self.scaler = StandardScaler().fit(X)
        self.clf = LogisticRegression(C=1.0, max_iter=1000).fit(self.scaler.transform(X), y)
        self.coef = self.clf.coef_[0]
        return self

    def auc(self, df: pd.DataFrame) -> float:
        X = pd.concat([to_features(r) for r in df.to_dict("records")], ignore_index=True)
        return float(roc_auc_score(df["defaulted"].astype(int), self.clf.predict_proba(self.scaler.transform(X))[:, 1]))

    def _z(self, rec: dict):
        xs = self.scaler.transform(to_features(rec))[0]
        contribs = self.coef * xs
        return float(self.clf.intercept_[0] + contribs.sum()), contribs

    def predict_pd(self, rec: dict) -> float:
        z, _ = self._z(rec)
        return float(1 / (1 + np.exp(-z)))

    def assess(self, rec: dict) -> dict:
        z, contribs = self._z(rec)
        pd_ = float(1 / (1 + np.exp(-z)))
        feats = to_features(rec).iloc[0]
        factors = []
        for name, c in zip(FEATURES, contribs):
            # Change in default probability vs an average applicant on this one factor.
            delta = pd_ - float(1 / (1 + np.exp(-(z - c))))
            factors.append({"key": name, "label": LABELS[name], "value": fmt(name, float(feats[name])),
                            "impact_pts": round(delta * 100, 1)})
        factors.sort(key=lambda f: abs(f["impact_pts"]), reverse=True)
        return {"pd": round(pd_, 4), "score": score_for(pd_), "decision": decision_for(pd_),
                "factors": factors[:7]}

    def path_to_approval(self, rec: dict) -> dict:
        """Greedy search over levers the owner can actually pull, cheapest effort per point of risk removed."""
        orig = dict(rec)

        def loan(r):
            new = round(r["loan_amount"] * 0.9, -3)
            return {"loan_amount": new} if new >= orig["loan_amount"] * 0.5 else None

        def cash(r):
            new = round(r["cash_reserve_months"] + 0.5, 2)
            return {"cash_reserve_months": new} if new <= orig["cash_reserve_months"] + 4 else None

        def debt(r):
            new = round(r["existing_debt_ratio"] - 0.03, 3)
            return {"existing_debt_ratio": new} if new >= orig["existing_debt_ratio"] * 0.5 else None

        def digital(r):
            new = round(r["digital_payment_share"] + 0.05, 3)
            return {"digital_payment_share": new} if new <= min(0.9, orig["digital_payment_share"] + 0.4) else None

        def utility(r):
            new = round(r["utility_on_time_rate"] + 0.03, 3)
            return {"utility_on_time_rate": new} if new <= 1.0 else None

        def conc(r):
            new = round(r["customer_concentration"] - 0.05, 3)
            return {"customer_concentration": new} if new >= max(0.05, orig["customer_concentration"] - 0.25) else None

        # (key, illustrative effort cost per step, mover, plain-language action)
        levers = [
            ("loan_amount", 1.5, loan, "Ask for a smaller amount"),
            ("cash_reserve_months", 1.0, cash, "Build up cash reserves"),
            ("existing_debt_ratio", 1.2, debt, "Pay down existing debt"),
            ("digital_payment_share", 0.6, digital, "Route more sales through digital payments"),
            ("utility_on_time_rate", 0.5, utility, "Pay utility bills on time"),
            ("customer_concentration", 1.0, conc, "Spread sales across more customers"),
        ]

        cur = dict(rec)
        cur_pd = self.predict_pd(cur)
        picked = []
        for _ in range(80):
            if cur_pd <= APPROVE_PD:
                break
            best = None
            for key, cost, mover, label in levers:
                change = mover(cur)
                if not change:
                    continue
                p = self.predict_pd({**cur, **change})
                gain = cur_pd - p
                if gain <= 0:
                    continue
                if best is None or gain / cost > best[0]:
                    best = (gain / cost, key, label, change, p, cost)
            if not best:
                break
            _, key, label, change, p, cost = best
            picked.append((key, label, change, cost))
            cur = {**cur, **change}
            cur_pd = p

        # Merge repeated moves on the same lever, then replay in order for honest step-by-step risk.
        merged: dict = {}
        for key, label, change, cost in picked:
            if key not in merged:
                merged[key] = {"label": label, "effort": 0.0}
            merged[key]["to"] = change[key]
            merged[key]["effort"] += cost
        steps, replay = [], dict(rec)
        for key, m in merged.items():
            before = replay[key]
            replay[key] = m["to"]
            steps.append({"key": key, "action": m["label"], "from": fmt(key, before), "to": fmt(key, m["to"]),
                          "pd_after": round(self.predict_pd(replay), 4),
                          "score_after": score_for(self.predict_pd(replay)), "effort": round(m["effort"], 1), "target_value": m["to"]})
        final_pd = self.predict_pd(replay)
        return {"reachable": final_pd <= APPROVE_PD, "steps": steps,
                "final": {"pd": round(final_pd, 4), "score": score_for(final_pd),
                          "decision": decision_for(final_pd), "loan_amount": replay["loan_amount"]}}
