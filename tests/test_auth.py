from app import auth, usuarios


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
