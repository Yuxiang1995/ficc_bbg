import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


MID_MODEL = os.getenv("MID_MODEL", "qwen3.7-plus")
BIG_MODEL = os.getenv("BIG_MODEL", "qwen3.7-max")
DEFAULT_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://llm-qrtckkbz37hh4ftl.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
)

DB_SERVER = os.getenv("DB_SERVER", "172.30.10.78")
DB_PORT = int(os.getenv("DB_PORT", "1433"))
DB_DATABASE = os.getenv("DB_DATABASE", "FICC_BBG_IB_PRD")
DB_TABLE = os.getenv("DB_TABLE", "BBG_IB_CHAT_EMAIL_INFO")
DB_USERNAME = os.getenv("DB_USERNAME", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DATABASE = os.getenv("POSTGRES_DATABASE", "ficc_bbg")
POSTGRES_SCHEMA = os.getenv("POSTGRES_SCHEMA", "ficc")
POSTGRES_USERNAME = os.getenv("POSTGRES_USERNAME", "ficc_app")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_SSLMODE = os.getenv("POSTGRES_SSLMODE", "prefer")
