import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local.db")
# Railway a veces entrega postgres:// en vez de postgresql://, SQLAlchemy 2.x requiere el segundo
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_LABEL = os.environ.get("GMAIL_LABEL", "gridworks-contabilidad")
PROCESSED_LABEL = f"{GMAIL_LABEL}/procesado"

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
