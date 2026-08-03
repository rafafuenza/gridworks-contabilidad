"""Sesion y guardas de ruta. Todo lo que es token firmado vive aqui."""

from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy.orm import Session

from app.config import SECRET_KEY
from app.db import get_db
from app.models import Usuario

COOKIE_NAME = "session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 dias
RESET_MAX_AGE = 60 * 30  # 30 minutos
SALT_RESET = "reset-clave"

_serializer = URLSafeTimedSerializer(SECRET_KEY)


def create_session_cookie(u: Usuario) -> str:
    """El payload lleva la version del usuario: cuando esa version sube en la
    base, esta cookie deja de validar sin necesidad de tabla de sesiones."""
    return _serializer.dumps({"uid": u.id, "v": u.token_version})


def _usuario_de_payload(datos, db: Session):
    """Reglas comunes para validar el payload {"uid", "v"} de un token
    firmado, ya sea cookie de sesion o token de reset. Usado por
    usuario_actual y leer_token_reset para que las reglas no se dupliquen
    y puedan divergir con el tiempo."""
    # Se exige la forma exacta del payload. La cookie del formato viejo
    # ({"ok": true}) tampoco pasa por aqui: no trae uid ni v. Ojo con los
    # booleanos, que en Python y en SQL valen 1 y calzarian con el id 1.
    if not isinstance(datos, dict):
        return None
    uid = datos.get("uid")
    version = datos.get("v")
    if not isinstance(uid, int) or isinstance(uid, bool):
        return None
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    # Un entero valido pero enorme llega igual al driver y revienta con un 500
    # (OverflowError en SQLite, DataError en Postgres). El id es un INTEGER.
    if not 0 < uid < 2 ** 31 or not 0 < version < 2 ** 31:
        return None

    u = db.query(Usuario).filter(Usuario.id == uid).first()
    if u is None or not u.activo:
        return None
    if version != u.token_version:
        return None
    return u


def usuario_actual(request: Request, db: Session):
    """Usuario de la sesion, o None. Rechaza firma invalida, vencimiento,
    usuario borrado, cuenta dada de baja y version desactualizada."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        datos = _serializer.loads(token, max_age=MAX_AGE)
    except BadData:
        return None  # firma invalida, vencida, o payload corrupto

    return _usuario_de_payload(datos, db)


def require_login(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """Dependencia de FastAPI. Sin sesion valida manda al login: no hay ningun
    atajo que deje pasar sin autenticacion."""
    u = usuario_actual(request, db)
    if u is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return u


def crear_token_reset(u: Usuario) -> str:
    """Salt propio para que un token de recuperacion no sirva como cookie de
    sesion ni al reves."""
    return _serializer.dumps({"uid": u.id, "v": u.token_version}, salt=SALT_RESET)


def leer_token_reset(token: str, db: Session):
    """Usuario del token, o None. Que la version tenga que calzar hace que el
    enlace sirva una sola vez: al cambiar la clave, sube y el token muere."""
    try:
        datos = _serializer.loads(token, max_age=RESET_MAX_AGE, salt=SALT_RESET)
    except BadData:
        return None  # firma invalida, vencido, corrupto, o de otro salt

    return _usuario_de_payload(datos, db)
