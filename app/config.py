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

# A quien se le avisa por correo cuando se acepta un lote (compras declaradas)
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "contabilidad@gridworks.cl")

# Sin default a proposito: con una clave conocida o adivinable cualquiera puede
# fabricarse una cookie de sesion valida y entrar como cualquier usuario. Se
# exige largo minimo porque una frase legible, aunque sea larga, tiene poca
# entropia real; una clave generada al azar pasa este piso de sobra.
LARGO_MINIMO_SECRET_KEY = 32

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if len(SECRET_KEY) < LARGO_MINIMO_SECRET_KEY or SECRET_KEY == "dev-secret-change-me":
    raise RuntimeError(
        "SECRET_KEY falta, es la de ejemplo, o es demasiado corta. "
        "Generala con: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )
# En produccion (HTTPS, ej. Railway) poner COOKIE_SECURE=true para que la cookie
# de sesion nunca viaje en claro. En local (http) dejar sin setear.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")

# Siembra de la primera cuenta. Solo se usan si la tabla usuarios esta vacia;
# una vez creada la cuenta se pueden borrar del entorno.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# URL publica, para armar el enlace absoluto del correo de recuperacion. No se
# deriva del request porque detras del proxy de Railway no es confiable.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
