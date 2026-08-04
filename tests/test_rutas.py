import re

import pytest

from app import auth, main, usuarios


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


# --- Recuperacion de clave ---

def _parchar_envio(monkeypatch, cuerpos=None):
    """Reemplaza gmail_sync.enviar_correo por un grabador. Si se pasa una
    lista, guarda ahi el cuerpo de cada correo (para sacar el token del
    enlace en las pruebas de recorrido completo)."""
    llamadas = []

    def _grabar(to, asunto, cuerpo, adjunto=None):
        llamadas.append(to)
        if cuerpos is not None:
            cuerpos.append(cuerpo)

    monkeypatch.setattr(main.gmail_sync, "enviar_correo", _grabar)
    return llamadas


def _token_del_correo(cuerpo):
    m = re.search(r"/restablecer/(\S+)", cuerpo)
    assert m, "el cuerpo del correo no trae el enlace de restablecer"
    return m.group(1)


def test_olvide_clave_responde_igual_con_o_sin_cuenta(client, usuario, monkeypatch):
    """El mensaje y el status deben ser identicos exista o no la cuenta; solo
    debe haberse agendado un envio real para la que existe."""
    llamadas = _parchar_envio(monkeypatch)

    con_cuenta = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})
    sin_cuenta = client.post("/olvide-clave", data={"email": "nadie@gridworks.cl"})

    assert con_cuenta.status_code == sin_cuenta.status_code == 200
    assert main.MENSAJE_ENLACE in con_cuenta.text
    assert main.MENSAJE_ENLACE in sin_cuenta.text
    assert llamadas == ["rafael@gridworks.cl"]


def test_pedir_el_enlace_dos_veces_seguidas_no_reenvia(client, usuario, monkeypatch):
    """El freno de 5 minutos se toma dentro del request; la segunda peticion
    no debe agendar un segundo envio, pero igual debe mostrar el mismo mensaje
    generico, sin delatar que el freno actuo."""
    llamadas = _parchar_envio(monkeypatch)

    primera = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})
    segunda = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})

    assert primera.status_code == segunda.status_code == 200
    assert main.MENSAJE_ENLACE in segunda.text
    assert llamadas == ["rafael@gridworks.cl"]


def test_si_el_smtp_revienta_igual_responde_200_con_el_mismo_mensaje(client, usuario, monkeypatch):
    def _reventar(to, asunto, cuerpo, adjunto=None):
        raise RuntimeError("smtp caido")

    monkeypatch.setattr(main.gmail_sync, "enviar_correo", _reventar)

    resp = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})

    assert resp.status_code == 200
    assert main.MENSAJE_ENLACE in resp.text


def test_recuperar_clave_de_principio_a_fin(client, db, usuario, monkeypatch):
    cuerpos = []
    _parchar_envio(monkeypatch, cuerpos)

    resp_pedido = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})
    assert resp_pedido.status_code == 200
    assert len(cuerpos) == 1
    token = _token_del_correo(cuerpos[0])

    resp_form = client.get(f"/restablecer/{token}")
    assert resp_form.status_code == 200
    assert "rafael@gridworks.cl" in resp_form.text

    resp_post = client.post(
        f"/restablecer/{token}",
        data={"password": "clave-nueva-larga-9", "password2": "clave-nueva-larga-9"},
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert resp_post.headers["location"] == "/login"

    resultado_nueva, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-nueva-larga-9")
    assert resultado_nueva is usuarios.Resultado.OK

    resultado_vieja, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado_vieja is not usuarios.Resultado.OK


def test_reusar_el_token_tras_cambiar_la_clave_muestra_enlace_invalido(client, db, usuario, monkeypatch):
    cuerpos = []
    _parchar_envio(monkeypatch, cuerpos)
    client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})
    token = _token_del_correo(cuerpos[0])

    primer_uso = client.post(
        f"/restablecer/{token}",
        data={"password": "clave-nueva-larga-9", "password2": "clave-nueva-larga-9"},
        follow_redirects=False,
    )
    assert primer_uso.status_code == 303

    resp = client.get(f"/restablecer/{token}")
    assert resp.status_code == 400
    assert "no válido" in resp.text.lower()


def test_clave_muy_corta_se_rechaza_y_no_cambia_nada(client, db, usuario, monkeypatch):
    cuerpos = []
    _parchar_envio(monkeypatch, cuerpos)
    client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})
    token = _token_del_correo(cuerpos[0])

    resp = client.post(f"/restablecer/{token}", data={"password": "corta", "password2": "corta"})

    assert resp.status_code == 400
    assert "al menos" in resp.text
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is usuarios.Resultado.OK


def test_claves_que_no_coinciden_se_rechazan_y_no_cambia_nada(client, db, usuario, monkeypatch):
    cuerpos = []
    _parchar_envio(monkeypatch, cuerpos)
    client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})
    token = _token_del_correo(cuerpos[0])

    resp = client.post(
        f"/restablecer/{token}",
        data={"password": "clave-nueva-larga-9", "password2": "otra-clave-larga-distinta"},
    )

    assert resp.status_code == 400
    assert "no coinciden" in resp.text
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is usuarios.Resultado.OK


def test_token_basura_en_la_url_da_400_no_500(client):
    resp = client.get("/restablecer/esto-no-es-un-token-valido")
    assert resp.status_code == 400


def test_post_con_token_basura_da_400_no_500(client):
    resp = client.post(
        "/restablecer/esto-no-es-un-token-valido",
        data={"password": "clave-nueva-larga-9", "password2": "clave-nueva-larga-9"},
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("ruta", ["/olvide-clave", "/restablecer/token-cualquiera"])
def test_las_rutas_de_recuperacion_son_publicas(client, ruta):
    """Son la puerta de vuelta cuando no se puede iniciar sesion, asi que no
    pueden estar detras de require_login (eso las mandaria al login otra vez)."""
    resp = client.get(ruta, follow_redirects=False)
    assert resp.status_code in (200, 400)
