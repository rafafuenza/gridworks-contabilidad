"""Sesion y guardas de ruta. Todo lo que es token firmado vive aqui."""

from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from app.config import SECRET_KEY
from app.db import get_db
from app.models import Usuario

COOKIE_NAME = "session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 dias

_serializer = URLSafeTimedSerializer(SECRET_KEY)


def create_session_cookie(u: Usuario) -> str:
    """El payload lleva la version del usuario: cuando esa version sube en la
    base, esta cookie deja de validar sin necesidad de tabla de sesiones."""
    return _serializer.dumps({"uid": u.id, "v": u.token_version})


def usuario_actual(request: Request, db: Session):
    """Usuario de la sesion, o None. Rechaza firma invalida, vencimiento,
    usuario borrado, cuenta dada de baja y version desactualizada."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        datos = _serializer.loads(token, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(datos, dict):
        return None  # cookie del formato viejo ({"ok": true})

    u = db.query(Usuario).filter(Usuario.id == datos.get("uid")).first()
    if u is None or not u.activo:
        return None
    if datos.get("v") != u.token_version:
        return None
    return u


def require_login(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """Dependencia de FastAPI. Sin sesion valida manda al login: no hay ningun
    atajo que deje pasar sin autenticacion."""
    u = usuario_actual(request, db)
    if u is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return u
