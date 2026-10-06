"""Synthetic SME dataset generator.

The data is synthetic: outcomes are drawn from a hand-written risk formula plus
noise, so model accuracy on it says nothing about real-world accuracy. That is
fine for a practice build, but say so out loud in the demo.
"""
import numpy as np
import pandas as pd

SECTORS = ["Retail", "Textile", "Food and beverage", "Agri-trading", "Services", "Light manufacturing"]
CITIES = ["Lahore", "Karachi", "Faisalabad", "Rawalpindi", "Multan", "Peshawar", "Sialkot", "Gujranwala"]
PREFIX = ["Al-Noor", "Bismillah", "Madina", "Rehman", "Zam Zam", "Punjab", "Indus", "Star", "Royal",
          "Habib", "Usman", "Falcon", "Ahmed", "Crescent", "Sunrise", "Kohistan"]
SUFFIX = {
    "Retail": ["General Store", "Mart", "Traders"],
    "Textile": ["Textiles", "Fabrics", "Garments"],
    "Food and beverage": ["Bakers", "Foods", "Catering"],
    "Agri-trading": ["Agro Traders", "Seeds and Feed", "Dairy Supply"],
    "Services": ["Logistics", "Printing", "Repair Works"],
    "Light manufacturing": ["Plastics", "Engineering Works", "Packaging"],
}


def _sigmoid(z):
    return 1 / (1 + np.exp(-z))


def generate(n: int = 1000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    monthly_revenue = np.clip(rng.lognormal(np.log(350_000), 0.7, n), 60_000, 3_000_000).round(-3)
    business_age = np.clip(rng.gamma(2.2, 2.0, n) + 0.5, 0.5, 25)
    growth = np.clip(rng.normal(0.05, 0.15, n), -0.4, 0.6)
    volatility = rng.beta(2, 6, n) * 0.8 + 0.05
    cash_months = np.clip(rng.gamma(2, 0.8, n), 0, 8)
    debt_ratio = np.clip(rng.beta(2, 5, n), 0, 0.9)
    digital = rng.beta(2.2, 2.5, n)
    utility = np.clip(rng.beta(6, 1.5, n), 0.3, 1)
    concentration = np.clip(rng.beta(2, 4, n) + 0.05, 0.05, 0.95)
    gap = np.clip(np.abs(rng.normal(0.04, 0.08, n)), 0, 0.5)
    loan_amount = (monthly_revenue * rng.uniform(1.5, 9, n)).round(-3)
    has_history = rng.random(n) < np.clip(0.10 + 0.05 * business_age, 0.1, 0.8)
    loan_ratio = loan_amount / (monthly_revenue * 12)

    z = (-3.9 + 3.2 * loan_ratio + 2.8 * debt_ratio + 2.0 * volatility + 1.6 * concentration
         + 5.0 * gap - 0.30 * cash_months - 1.6 * digital - 2.4 * (utility - 0.8)
         - 0.06 * business_age - 1.5 * growth + rng.normal(0, 0.5, n))
    defaulted = rng.random(n) < _sigmoid(z)

    sector = rng.choice(SECTORS, n)
    names = [f"{rng.choice(PREFIX)} {rng.choice(SUFFIX[s])}" for s in sector]
    return pd.DataFrame({
        "name": names, "sector": sector, "city": rng.choice(CITIES, n),
        "monthly_revenue": monthly_revenue, "loan_amount": loan_amount,
        "business_age_years": business_age.round(1), "revenue_growth_6m": growth.round(3),
        "revenue_volatility": volatility.round(3), "cash_reserve_months": cash_months.round(1),
        "existing_debt_ratio": debt_ratio.round(3), "digital_payment_share": digital.round(3),
        "utility_on_time_rate": utility.round(3), "customer_concentration": concentration.round(3),
        "revenue_gap": gap.round(3), "has_credit_history": has_history, "defaulted": defaulted,
    })
