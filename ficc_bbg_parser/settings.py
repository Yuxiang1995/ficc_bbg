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
DB_USERNAME = os.getenv("DB_USERNAME", "ficc_bbg_ib_prd")
DB_PASSWORD = os.getenv("DB_PASSWORD", "ficc_bbg_ib_prd@#*78341grec")
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_TRUST_SERVER_CERTIFICATE = os.getenv("DB_TRUST_SERVER_CERTIFICATE", "yes")
DB_ENCRYPT = os.getenv("DB_ENCRYPT", "yes")
