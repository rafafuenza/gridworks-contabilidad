import pytest

from app import auth, usuarios


@pytest.fixture
def usuario(db):
    return usuarios.crear(db, "rafael@gridworks.cl", nombre="Rafael", clave="clave-larga-1")


RUTAS_PROTEGIDAS = ["/", "/sync/estado", "/aceptar-mes/estado", "/export.xlsx", "/export/pdfs.zip"]


@pytest.mark.parametrize("ruta", RUTAS_PROTEGIDAS)
def test_sin_sesion_las_rutas_protegidas_mandan_al_login(client, ruta):
    resp = client.get(ruta, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_con_sesion_valida_el_tablero_responde(client, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.get("/", follow_redirects=False)

    assert resp.status_code == 200
    assert "rafael@gridworks.cl" in resp.text


def test_entrar_por_el_formulario_deja_la_cookie(client, usuario):
    resp = client.post("/login", data={"email": "rafael@gridworks.cl", "password": "clave-larga-1"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert auth.COOKIE_NAME in resp.cookies


def test_la_clave_mala_no_deja_cookie(client, usuario):
    resp = client.post("/login", data={"email": "rafael@gridworks.cl", "password": "equivocada"},
                       follow_redirects=False)

    assert resp.status_code == 401
    assert auth.COOKIE_NAME not in resp.cookies


def test_un_correo_inexistente_da_el_mismo_mensaje_que_una_clave_mala(client, usuario):
    """Si los mensajes difirieran, se podria averiguar que correos tienen cuenta."""
    con_cuenta = client.post("/login", data={"email": "rafael@gridworks.cl", "password": "equivocada"})
    sin_cuenta = client.post("/login", data={"email": "nadie@gridworks.cl", "password": "equivocada"})

    assert con_cuenta.status_code == sin_cuenta.status_code == 401
    assert "Correo o clave incorrectos." in con_cuenta.text
    assert "Correo o clave incorrectos." in sin_cuenta.text


def test_salir_borra_la_cookie(client, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.get("/logout", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


# --- Rutas POST tambien protegidas ---
# Estas son las que mueven correos reales de Gmail (sincronizar, aceptar en
# lote, aceptar una), asi que su guarda importa mas que la de cualquier GET.
RUTAS_POST_PROTEGIDAS = [
    ("/sync", {}),
    ("/aceptar-mes", {"mes": ""}),
    ("/aceptar/1", {"mes": ""}),
]


@pytest.mark.parametrize("ruta,datos", RUTAS_POST_PROTEGIDAS)
def test_sin_sesion_las_rutas_post_protegidas_mandan_al_login(client, ruta, datos):
    resp = client.post(ruta, data=datos, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_una_cookie_de_antes_de_cambiar_la_clave_deja_de_servir(client, db, usuario):
    """cambiar_clave sube token_version, lo que revoca cualquier cookie firmada
    con la version anterior. Se prueba a nivel de ruta (no solo en el modulo de
    usuarios) para confirmar que require_login de verdad la rechaza."""
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))
    assert client.get("/", follow_redirects=False).status_code == 200

    usuarios.cambiar_clave(db, usuario, "otra-clave-larga-2")

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_health_es_publica(client):
    resp = client.get("/health", follow_redirects=False)

    assert resp.status_code == 200


@pytest.mark.parametrize("ruta", ["/docs", "/openapi.json"])
def test_docs_y_openapi_estan_desactivados(client, ruta):
    resp = client.get(ruta, follow_redirects=False)

    assert resp.status_code == 404


def test_una_clave_demasiado_larga_se_rechaza_sin_llamar_a_autenticar(client, usuario, monkeypatch):
    """login_submit corta antes de LARGO_MAXIMO_CLAVE (200) sin llamar a
    usuarios.autenticar, que es lo que hashea con scrypt. Se prueba parchando
    autenticar para que reviente si se llegara a invocar: si el test pasa sin
    disparar la excepcion, es porque el corto-circuito de verdad corto camino
    antes de llegar ahi (medir el tiempo de respuesta no lo probaria con la
    misma certeza: una maquina rapida podria hashear una vez por debajo de
    cualquier umbral razonable y dar un falso verde)."""

    def _autenticar_no_deberia_llamarse(*args, **kwargs):
        raise AssertionError("autenticar no deberia llamarse con una clave demasiado larga")

    monkeypatch.setattr(usuarios, "autenticar", _autenticar_no_deberia_llamarse)

    clave_larga = "a" * 5000
    resp = client.post("/login", data={"email": "rafael@gridworks.cl", "password": clave_larga})

    assert resp.status_code == 401
    assert "Correo o clave incorrectos." in resp.text
