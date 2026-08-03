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


@pytest.mark.parametrize(
    "valor",
    [
        "",  # falta
        "dev-secret-change-me",  # la de ejemplo
        "corta-pero-no-es-la-de-ejemplo",  # 31 caracteres: bajo el piso
    ],
)
def test_secret_key_invalida_no_arranca(monkeypatch, valor):
    """SECRET_KEY es obligatoria, no puede quedar en el valor de ejemplo, y no
    puede ser una frase corta: con una clave adivinable cualquiera se fabrica
    una cookie de sesion valida."""
    assert len(valor) < config_module.LARGO_MINIMO_SECRET_KEY or valor == "dev-secret-change-me"
    monkeypatch.setenv("SECRET_KEY", valor)
    try:
        with pytest.raises(RuntimeError):
            importlib.reload(config_module)
    finally:
        # Se restaura el entorno ANTES de recargar, para dejar app.config en
        # el mismo estado valido con el que arrancaron las demas pruebas.
        monkeypatch.undo()
        importlib.reload(config_module)


def test_un_token_de_reset_identifica_al_usuario(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    encontrado = auth.leer_token_reset(auth.crear_token_reset(u), db)

    assert encontrado is not None
    assert encontrado.email == "rafael@gridworks.cl"


def test_un_token_de_reset_ya_usado_se_rechaza(db):
    """Al cambiar la clave sube token_version, y eso mata el enlace. Es lo que
    lo hace de un solo uso sin llevar registro de tokens gastados."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    token = auth.crear_token_reset(u)

    usuarios.cambiar_clave(db, u, "clave-nueva-larga-2")

    assert auth.leer_token_reset(token, db) is None


def test_un_token_de_reset_vencido_se_rechaza(db, monkeypatch):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    token = auth.crear_token_reset(u)

    monkeypatch.setattr(auth, "RESET_MAX_AGE", -1)

    assert auth.leer_token_reset(token, db) is None


def test_una_cookie_de_sesion_no_sirve_como_token_de_reset(db):
    """Salt distinto: un token no se puede usar en el otro flujo."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    assert auth.leer_token_reset(auth.create_session_cookie(u), db) is None


def test_un_token_de_reset_no_sirve_como_cookie_de_sesion(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    peticion = RequestFalso({auth.COOKIE_NAME: auth.crear_token_reset(u)})

    assert auth.usuario_actual(peticion, db) is None


def test_el_token_de_reset_de_una_cuenta_dada_de_baja_se_rechaza(db):
    u = usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")
    token = auth.crear_token_reset(u)

    usuarios.desactivar(db, u)

    assert auth.leer_token_reset(token, db) is None


def test_el_token_de_reset_identifica_al_usuario_correcto_entre_varios(db):
    a = usuarios.crear(db, "a@gridworks.cl", clave="clave-larga-1")
    b = usuarios.crear(db, "b@gridworks.cl", clave="clave-larga-2")

    assert auth.leer_token_reset(auth.crear_token_reset(a), db).email == "a@gridworks.cl"
    assert auth.leer_token_reset(auth.crear_token_reset(b), db).email == "b@gridworks.cl"


def test_el_token_de_reset_lleva_la_version_real_no_una_fija(db):
    """Todo usuario recien creado parte con token_version == 1, asi que un
    crear_token_reset que hardcodeara "v": 1 pasaria las demas pruebas sin
    que nadie lo note. Este cambia la clave PRIMERO (la version queda en 2)
    y recien ahi emite el token: si la version fuera fija, el enlace
    quedaria muerto para siempre despues del primer cambio de clave."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    usuarios.cambiar_clave(db, u, "clave-intermedia-larga-2")
    assert u.token_version == 2

    token = auth.crear_token_reset(u)

    assert auth.leer_token_reset(token, db) is not None


def test_reset_max_age_es_treinta_minutos(db):
    """Pin de la politica, no del detalle de implementacion: la prueba de
    vencimiento monkeypatchea el valor, asi que nada mas deja constancia de
    cuanto dura realmente un enlace de recuperacion."""
    assert auth.RESET_MAX_AGE == 60 * 30


def test_usuario_de_payload_rechaza_un_payload_que_no_es_diccionario(db):
    """_usuario_de_payload protege ambos flujos (cookie y token de reset) pero
    ninguna de las dos funciones publicas deja pasar un payload que no sea
    dict antes de llegar a ella (itsdangerous solo entrega lo que el propio
    dumps() serializo). Se prueba directo para que la guarda quede cubierta."""
    assert auth._usuario_de_payload(["no", "es", "un", "dict"], db) is None
