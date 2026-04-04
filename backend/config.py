"""Centralized environment configuration for ZetaBridge."""

import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SNOWFLAKE_ACCOUNT = os.environ.get("SNOWFLAKE_ACCOUNT", "")
    SNOWFLAKE_USER = os.environ.get("SNOWFLAKE_USER", "")
    SNOWFLAKE_PASSWORD = os.environ.get("SNOWFLAKE_PASSWORD", "")
    SNOWFLAKE_WAREHOUSE = os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")
    SNOWFLAKE_DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "")
    SNOWFLAKE_SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC")

    DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "")
    DATABRICKS_HTTP_PATH = os.environ.get("DATABRICKS_HTTP_PATH", "")
    DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
    DATABRICKS_CATALOG = os.environ.get("DATABRICKS_CATALOG", "main")
    DATABRICKS_SCHEMA = os.environ.get("DATABRICKS_SCHEMA", "default")

    GRAVITINO_URL = os.environ.get("GRAVITINO_URL", "http://localhost:8090").rstrip("/")
    MARQUEZ_URL = os.environ.get("MARQUEZ_URL", "http://localhost:5000").rstrip("/")
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql://zetabridge:zetabridge@localhost:5432/zetabridge",
    )
    DUCKDB_PATH = os.environ.get("DUCKDB_PATH", "./data/zetabridge.duckdb")

    HF_API_TOKEN = os.environ.get("HF_API_TOKEN", "")
    HF_TEXT2SQL_MODEL = os.environ.get(
        "HF_TEXT2SQL_MODEL",
        os.environ.get("HF_MODEL_ID", "Snowflake/Arctic-Text2SQL-R1-7B"),
    )

    USE_LEGACY_STORE = os.environ.get("USE_LEGACY_STORE", "1") == "1"
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


cfg = Config()
