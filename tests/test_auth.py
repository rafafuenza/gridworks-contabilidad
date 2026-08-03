import importlib

import pytest
from itsdangerous import URLSafeTimedSerializer

import app.config as config_module
from app import auth, usuarios
from app.config import SECRET_KEY


class RequestFalso:
    """Lo minimo que mira usuario_actual: las cookies."""

    def __init__(self, cookies=None):
        self.cookies = cookies or {}


def _request_con_sesion(u):
    return RequestFalso({auth.COOKIE_NAME: auth.create_session_cookie(u)})


def test_una_cookie_valida_identifica_al_usuario(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    encontrado = auth.usuario_actual(_request_con_sesion(u), db)

    assert encontrado is not None
    assert encontrado.email == "rafael@gridworks.cl"


def test_sin_cookie_no_hay_usuario(db):
    assert auth.usuario_actual(RequestFalso(), db) is None


def test_una_cookie_falsificada_se_rechaza(db):
    peticion = RequestFalso({auth.COOKIE_NAME: "esto-no-viene-firmado"})

    assert auth.usuario_actual(peticion, db) is None


def test_una_cookie_con_version_vieja_se_rechaza(db):
    """Es el mecanismo que cierra las sesiones al cambiar la clave."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    peticion = _request_con_sesion(u)

    usuarios.cambiar_clave(db, u, "clave-nueva-larga-2")

    assert auth.usuario_actual(peticion, db) is None


def test_la_cookie_de_una_cuenta_dada_de_baja_se_rechaza(db):
    u = usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")
    peticion = _request_con_sesion(u)

    usuarios.desactivar(db, u)

    assert auth.usuario_actual(peticion, db) is None


def test_la_cookie_de_un_usuario_borrado_se_rechaza(db):
    u = usuarios.crear(db, "temporal@gridworks.cl", clave="clave-larga-1")
    peticion = _request_con_sesion(u)

    db.delete(u)
    db.commit()

    assert auth.usuario_actual(peticion, db) is None


def test_la_cookie_identifica_al_usuario_correcto_entre_varios(db):
    """No basta con que exista sesion: tiene que ser la sesion de ESE usuario.
    Con un solo usuario en la base, ignorar el uid tambien pasaria esta prueba
    por accidente (ver la verificacion de mutacion en el reporte)."""
    a = usuarios.crear(db, "a@gridworks.cl", clave="clave-larga-1")
    b = usuarios.crear(db, "b@gridworks.cl", clave="clave-larga-2")

    encontrado_a = auth.usuario_actual(_request_con_sesion(a), db)
    encontrado_b = auth.usuario_actual(_request_con_sesion(b), db)

    assert encontrado_a is not None and encontrado_a.email == "a@gridworks.cl"
    assert encontrado_b is not None and encontrado_b.email == "b@gridworks.cl"
    assert encontrado_a.id != encontrado_b.id


def test_una_cookie_vencida_se_rechaza(db, monkeypatch):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    peticion = _request_con_sesion(u)

    monkeypatch.setattr(auth, "MAX_AGE", -1)

    assert auth.usuario_actual(peticion, db) is None


def test_la_cookie_vieja_de_clave_compartida_se_rechaza(db):
    """Es la cookie que hoy mismo tiene puesta cualquiera que ya haya entrado
    con el password unico anterior: debe fallar cerrado, no crashear."""
    serializador_viejo = URLSafeTimedSerializer(SECRET_KEY)
    cookie_vieja = serializador_viejo.dumps({"ok": True})
    peticion = RequestFalso({auth.COOKIE_NAME: cookie_vieja})

    assert auth.usuario_actual(peticion, db) is None


def test_sin_secret_key_o_con_el_valor_de_ejemplo_no_arranca(monkeypatch):
    """SECRET_KEY es obligatoria y no puede quedar en el valor de ejemplo:
    con una clave conocida cualquiera se fabrica una cookie de sesion valida."""
    monkeypatch.setenv("SECRET_KEY", "dev-secret-change-me")
    try:
        with pytest.raises(RuntimeError):
            importlib.reload(config_module)
    finally:
        # Se restaura el entorno ANTES de recargar, para dejar app.config en
        # el mismo estado valido con el que arrancaron las demas pruebas.
        monkeypatch.undo()
        importlib.reload(config_module)
