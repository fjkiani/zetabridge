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
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    HF_TEXT2SQL_MODEL = os.environ.get(
        "HF_TEXT2SQL_MODEL",
        os.environ.get("HF_MODEL_ID", "Snowflake/Arctic-Text2SQL-R1-7B"),
    )

    USE_LEGACY_STORE = os.environ.get("USE_LEGACY_STORE", "1") == "1"
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # --- Federated Neo4j knowledge graph (Session 12 agent access layer) ---
    # Credentials stay server-side; consumer agents authenticate with a scoped
    # API key (ZETA_GRAPH_API_KEY), never with the Neo4j password.
    NEO4J_URI = os.environ.get("NEO4J_URI", "")
    NEO4J_USER = os.environ.get("NEO4J_USER", "")
    NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
    ZETA_GRAPH_API_KEY = os.environ.get("ZETA_GRAPH_API_KEY", "")

    # --- Live source endpoints (Session 15) -------------------------------
    # SERVER-SIDE ONLY. These tokens authenticate the backend to the three
    # federated *source* systems. They are NEVER returned to a caller; an
    # external agent authenticates with ZETA_GRAPH_API_KEY and the backend
    # performs live extraction on its behalf. Keep them in deploy secrets or a
    # git-ignored env file — do not commit real values.
    #
    # A_MSK — Synapse (synapseclient, personal-access JWT)
    SYNAPSE_AUTH_TOKEN = os.environ.get("SYNAPSE_AUTH_TOKEN", "")
    # B_SAS — Project Data Sphere / SAS Viya CAS (swat)
    SAS_CAS_HOST = os.environ.get("SAS_CAS_HOST", "mpmprodvdmml.ondemand.sas.com")
    SAS_CAS_PORT = int(os.environ.get("SAS_CAS_PORT", "443"))
    SAS_CAS_PROTOCOL = os.environ.get("SAS_CAS_PROTOCOL", "https")
    SAS_CAS_TOKEN = os.environ.get("SAS_CAS_TOKEN", "")
    SAS_CAS_USER = os.environ.get("SAS_CAS_USER", "")
    SAS_CAS_PASSWORD = os.environ.get("SAS_CAS_PASSWORD", "")
    # optional path to a TLS CA bundle for the CAS endpoint (known cert quirk)
    SAS_CAS_CADATA = os.environ.get("SAS_CAS_CADATA", "")
    # C_EGA — European Genome-phenome Archive (pyega3 / EGA REST)
    EGA_USERNAME = os.environ.get("EGA_USERNAME", "")
    EGA_PASSWORD = os.environ.get("EGA_PASSWORD", "")
    EGA_CREDENTIALS_FILE = os.environ.get("EGA_CREDENTIALS_FILE", "")
    EGA_DEFAULT_DATASET = os.environ.get("EGA_DEFAULT_DATASET", "EGAD00001011049")


cfg = Config()
