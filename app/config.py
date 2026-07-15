import os

from dotenv import load_dotenv

# Carga el .env local si existe (en Railway las variables se inyectan y esto no hace nada)
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local.db")
# Railway a veces entrega postgres:// en vez de postgresql://, SQLAlchemy 2.x requiere el segundo
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
# Label de entrada: documentos a procesar. Al aceptarlos manualmente en la web
# se mueven al label de declaradas (se quita el de entrada y se agrega este).
# OJO: para agregar/quitar labels (X-GM-LABELS) Gmail exige el nombre EXACTO del
# label (con mayusculas, '/' de anidado y espacios), no la forma normalizada de
# busqueda. Estos son los labels reales de la cuenta.
GMAIL_LABEL = os.environ.get("GMAIL_LABEL", "Gridworks/Contabilidad")
GMAIL_DECLARED_LABEL = os.environ.get(
    "GMAIL_DECLARED_LABEL", "Gridworks/Contabilidad/SII/Compras Declaradas"
)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
# En produccion (HTTPS, ej. Railway) poner COOKIE_SECURE=true para que la cookie
# de sesion nunca viaje en claro. En local (http) dejar sin setear.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
