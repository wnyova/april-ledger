import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit

    user = quote_plus(os.getenv("MYSQL_USER", "april_ledger"))
    password = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    database = os.getenv("MYSQL_DATABASE", "april_ledger")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "12")) * 1024 * 1024
    UPLOAD_ROOT = str(BASE_DIR / "storage")
    CURRENCY = os.getenv("CURRENCY", "IDR")
