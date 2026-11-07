"""Load data/marketing_AB.csv.gz into BigQuery as <dataset>.raw_marketing_ab (replaces the table).

Usage: python src/load_to_bigquery.py
"""
import pandas as pd
from google.cloud import bigquery

from bq import DATASET, LOCATION, PROJECT, RAW_TABLE, ROOT, client

SCHEMA = [
    bigquery.SchemaField("user_id", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("test_group", "STRING", mode="REQUIRED", description="ad = treatment, psa = control"),
    bigquery.SchemaField("converted", "INT64", mode="REQUIRED", description="1 if the user converted"),
    bigquery.SchemaField("total_ads", "INT64", mode="REQUIRED", description="ads or PSAs the user saw"),
    bigquery.SchemaField("most_ads_day", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("most_ads_hour", "INT64", mode="REQUIRED"),
]


def main():
    bq = client()
    dataset = bigquery.Dataset(f"{PROJECT}.{DATASET}")
    dataset.location = LOCATION
    bq.create_dataset(dataset, exists_ok=True)

    df = pd.read_csv(ROOT / "data" / "marketing_AB.csv.gz")
    df = df[[f.name for f in SCHEMA]]
    job = bq.load_table_from_dataframe(
        df, RAW_TABLE,
        job_config=bigquery.LoadJobConfig(schema=SCHEMA, write_disposition="WRITE_TRUNCATE"),
    )
    job.result()
    table = bq.get_table(RAW_TABLE)
    print(f"loaded {table.num_rows} rows into {RAW_TABLE}")


if __name__ == "__main__":
    main()
