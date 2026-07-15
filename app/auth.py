from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
from starlette.responses import RedirectResponse

from app.config import APP_PASSWORD, SECRET_KEY

COOKIE_NAME = "session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 dias

_serializer = URLSafeTimedSerializer(SECRET_KEY)


def create_session_cookie() -> str:
    return _serializer.dumps({"ok": True})


def is_valid_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def require_login(request: Request):
    if not APP_PASSWORD:
        # Si no se configuro clave, no bloqueamos (util solo para desarrollo local)
        return
    if not is_valid_session(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
