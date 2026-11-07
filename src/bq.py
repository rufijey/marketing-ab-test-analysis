"""Shared BigQuery settings and helpers."""
import os
from pathlib import Path

from google.cloud import bigquery

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
PROJECT = os.environ.get("BQ_PROJECT", "stone-booking-511312-g0")
DATASET = os.environ.get("BQ_DATASET", "ab_test")
LOCATION = os.environ.get("BQ_LOCATION", "US")
RAW_TABLE = f"{PROJECT}.{DATASET}.raw_marketing_ab"

if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ and (ROOT / "credentials.json").exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(ROOT / "credentials.json")


def client():
    return bigquery.Client(project=PROJECT, location=LOCATION)


def run_sql(name, bq=None):
    """Run sql/<name>.sql with {raw_table} filled in and return a DataFrame."""
    query = (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8").format(raw_table=RAW_TABLE)
    df = (bq or client()).query(query).to_dataframe()
    # BigQuery returns pandas nullable types (Int64, Float64); SciPy and statsmodels need plain NumPy types
    for col in df.columns:
        if str(df[col].dtype) == "Int64":
            df[col] = df[col].astype("int64")
        elif str(df[col].dtype) == "Float64":
            df[col] = df[col].astype("float64")
    return df
