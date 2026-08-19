import re

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from app import auth, main, usuarios


@pytest.fixture
def usuario(db):
    return usuarios.crear(db, "rafael@gridworks.cl", nombre="Rafael", clave="clave-larga-1")


RUTAS_PROTEGIDAS = ["/", "/sync/estado", "/aceptar-mes/estado", "/reparse/estado",
                    "/export.xlsx", "/export/pdfs.zip", "/pdf/1"]


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
    ("/reparse", {}),
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


def _request_falso():
    """Un Request real construido a mano, no un stub: TemplateResponse (via
    Jinja2Templates) necesita un Request de verdad, y TestClient no sirve aqui
    porque drena las BackgroundTasks antes de devolver la respuesta, lo que
    esconde justo la diferencia que esta prueba necesita ver."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/olvide-clave",
        "headers": [],
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "client": ("testclient", 123),
        "app": main.app,
    }
    return Request(scope)


def test_el_envio_queda_agendado_y_no_ocurre_dentro_del_request(db, monkeypatch):
    """Si el correo se mandara dentro del request, un correo con cuenta
    tardaria lo que tarda el SMTP y uno sin cuenta contestaria al instante:
    el tiempo delataria cuales existen aunque el mensaje sea el mismo.

    Se llama a la funcion de ruta directamente, sin pasar por TestClient: el
    cliente de pruebas drena las BackgroundTasks antes de devolver la
    respuesta, asi que desde su punto de vista mandar en linea o agendar se
    ven identicos. Esta prueba existe para que una edicion futura que mueva
    el envio adentro del request la rompa."""
    llamadas = []
    monkeypatch.setattr(main.gmail_sync, "enviar_correo", lambda *a, **k: llamadas.append(a))
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    tareas = BackgroundTasks()

    main.olvide_clave_submit(_request_falso(), tareas, email="rafael@gridworks.cl", db=db)

    assert llamadas == []          # todavia no se mando nada
    assert len(tareas.tasks) == 1  # quedo agendado para despues de responder


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


def test_cuenta_dada_de_baja_no_recibe_enlace_pero_ve_el_mismo_mensaje(client, db, usuario, monkeypatch):
    """Quitar el chequeo de u.activo de la ruta dejaria todas las demas
    pruebas en verde igual: esta prueba existe para cubrir justo esa rama."""
    llamadas = _parchar_envio(monkeypatch)
    usuarios.desactivar(db, usuario)

    resp = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})

    assert resp.status_code == 200
    assert main.MENSAJE_ENLACE in resp.text
    assert llamadas == []


def test_correo_demasiado_largo_no_consulta_la_base(client, monkeypatch):
    """Un correo mas largo que LARGO_MAXIMO_EMAIL no puede ser una cuenta real,
    asi que la ruta debe cortar antes de llamar a por_email. Se prueba
    parchando por_email para que reviente si se llegara a invocar, igual que
    se hace con autenticar en /login."""
    def _por_email_no_deberia_llamarse(*args, **kwargs):
        raise AssertionError("por_email no deberia llamarse con un correo demasiado largo")

    monkeypatch.setattr(usuarios, "por_email", _por_email_no_deberia_llamarse)

    correo_larguisimo = "a" * 400 + "@gridworks.cl"
    resp = client.post("/olvide-clave", data={"email": correo_larguisimo})

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


# --- Mi cuenta (/cuenta) ---

def test_cuenta_sin_sesion_manda_al_login(client):
    resp = client.get("/cuenta", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_cambiar_la_clave_propia_funciona(client, db, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.post(
        "/cuenta",
        data={"actual": "clave-larga-1", "password": "clave-nueva-larga-9",
              "password2": "clave-nueva-larga-9"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/cuenta?ok=1"
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-nueva-larga-9")
    assert resultado is usuarios.Resultado.OK


def test_cambiar_la_clave_propia_no_cierra_la_sesion_actual(client, usuario):
    """cambiar_clave sube token_version, lo que invalida la cookie con la que
    se llego. La ruta debe reemitir la cookie propia, o la persona quedaria
    afuera justo al cambiar su clave."""
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.post(
        "/cuenta",
        data={"actual": "clave-larga-1", "password": "clave-nueva-larga-9",
              "password2": "clave-nueva-larga-9"},
        follow_redirects=False,
    )
    assert auth.COOKIE_NAME in resp.cookies

    assert client.get("/", follow_redirects=False).status_code == 200


def test_cambiar_la_clave_propia_cierra_las_otras_sesiones(client, usuario):
    """Una cookie tomada ANTES del cambio (otro dispositivo) debe dejar de
    servir despues, aunque la sesion propia siga viva.

    Se usa un TestClient nuevo para el "otro dispositivo": reutilizar el
    mismo cliente causa un conflicto de cookies en httpx (el Set-Cookie de la
    respuesta y el valor puesto a mano quedan con dominios distintos y no se
    pisan entre si), lo que enmascararia justo lo que esta prueba busca
    comprobar. Los dos clientes comparten la misma base de pruebas porque
    el override de get_db ya esta activo a nivel de app."""
    from fastapi.testclient import TestClient
    from app.main import app as app_real

    cookie_otro_dispositivo = auth.create_session_cookie(usuario)
    otro_dispositivo = TestClient(app_real, raise_server_exceptions=False)
    otro_dispositivo.cookies.set(auth.COOKIE_NAME, cookie_otro_dispositivo)
    assert otro_dispositivo.get("/", follow_redirects=False).status_code == 200

    client.cookies.set(auth.COOKIE_NAME, cookie_otro_dispositivo)
    client.post(
        "/cuenta",
        data={"actual": "clave-larga-1", "password": "clave-nueva-larga-9",
              "password2": "clave-nueva-larga-9"},
        follow_redirects=False,
    )

    resp = otro_dispositivo.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_cuenta_con_clave_actual_mala_no_cambia_nada(client, db, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.post(
        "/cuenta",
        data={"actual": "equivocada", "password": "clave-nueva-larga-9",
              "password2": "clave-nueva-larga-9"},
    )

    assert resp.status_code == 400
    assert "clave actual" in resp.text.lower()
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is usuarios.Resultado.OK


def test_cuenta_clave_nueva_muy_corta_no_cambia_nada(client, db, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.post(
        "/cuenta",
        data={"actual": "clave-larga-1", "password": "corta", "password2": "corta"},
    )

    assert resp.status_code == 400
    assert "al menos" in resp.text
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is usuarios.Resultado.OK


def test_cuenta_claves_nuevas_no_coinciden_no_cambia_nada(client, db, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.post(
        "/cuenta",
        data={"actual": "clave-larga-1", "password": "clave-nueva-larga-9",
              "password2": "otra-clave-larga-distinta"},
    )

    assert resp.status_code == 400
    assert "no coinciden" in resp.text
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is usuarios.Resultado.OK


# --- Administracion de usuarios (/usuarios) ---

def _con_sesion(client, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))
    return client


def test_usuarios_sin_sesion_manda_al_login(client):
    resp = client.get("/usuarios", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_invitar_crea_la_cuenta_y_agenda_el_correo(client, db, usuario, monkeypatch):
    cuerpos = []
    llamadas = _parchar_envio(monkeypatch, cuerpos)
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "invitar", "email": "nueva@gridworks.cl",
                                          "nombre": "Nueva Persona"})

    assert resp.status_code == 200
    assert "nueva@gridworks.cl" in resp.text
    creado = usuarios.por_email(db, "nueva@gridworks.cl")
    assert creado is not None
    assert creado.nombre == "Nueva Persona"
    assert creado.activo is True
    # El destinatario debe ser la cuenta recien creada, no quien la invito: si
    # la ruta mandara el enlace al correo de quien esta logueado (bug de
    # copiar/pegar entre nuevo.email y usuario.email), esta aseveracion es la
    # unica que lo detecta.
    assert llamadas == ["nueva@gridworks.cl"]
    assert len(cuerpos) == 1


def test_la_cuenta_invitada_no_tiene_clave_usable_hasta_usar_el_enlace(client, db, usuario, monkeypatch):
    cuerpos = []
    _parchar_envio(monkeypatch, cuerpos)
    _con_sesion(client, usuario)

    client.post("/usuarios", data={"accion": "invitar", "email": "nueva@gridworks.cl", "nombre": ""})

    # Nadie sabe la clave con la que se creo (es aleatoria): ni siquiera un
    # valor obvio como el correo o una clave vacia debe autenticar.
    for intento in ("", "nueva@gridworks.cl", "clave", "123456789012"):
        resultado, _ = usuarios.autenticar(db, "nueva@gridworks.cl", intento)
        assert resultado is not usuarios.Resultado.OK

    token = _token_del_correo(cuerpos[0])
    resp_post = client.post(
        f"/restablecer/{token}",
        data={"password": "clave-nueva-larga-9", "password2": "clave-nueva-larga-9"},
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    resultado, _ = usuarios.autenticar(db, "nueva@gridworks.cl", "clave-nueva-larga-9")
    assert resultado is usuarios.Resultado.OK


def test_invitar_con_correo_duplicado_no_crea_una_segunda_fila(client, db, usuario, monkeypatch):
    _parchar_envio(monkeypatch)
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "invitar", "email": "rafael@gridworks.cl", "nombre": ""})

    assert resp.status_code == 400
    assert "ya tiene cuenta" in resp.text
    filas = db.query(main.Usuario).filter(main.Usuario.email == "rafael@gridworks.cl").count()
    assert filas == 1


def test_invitar_con_correo_invalido_no_crea_cuenta(client, db, usuario, monkeypatch):
    llamadas = _parchar_envio(monkeypatch)
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "invitar", "email": "no-es-un-correo", "nombre": ""})

    assert resp.status_code == 400
    assert usuarios.por_email(db, "no-es-un-correo") is None
    assert llamadas == []


def test_invitar_con_nombre_demasiado_largo_no_crea_cuenta(client, db, usuario, monkeypatch):
    _parchar_envio(monkeypatch)
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "invitar", "email": "nueva@gridworks.cl",
                                          "nombre": "a" * 500})

    assert resp.status_code == 400
    assert usuarios.por_email(db, "nueva@gridworks.cl") is None


def test_desactivar_deja_a_la_persona_sin_acceso_de_inmediato(client, db, usuario):
    """La cookie de la persona dada de baja debe dejar de servir apenas se la
    desactiva, sin esperar a que expire."""
    from fastapi.testclient import TestClient
    from app.main import app as app_real

    objetivo = usuarios.crear(db, "otra@gridworks.cl", nombre="Otra", clave="clave-larga-2")
    cookie_objetivo = auth.create_session_cookie(objetivo)
    cliente_objetivo = TestClient(app_real, raise_server_exceptions=False)
    cliente_objetivo.cookies.set(auth.COOKIE_NAME, cookie_objetivo)
    assert cliente_objetivo.get("/", follow_redirects=False).status_code == 200

    _con_sesion(client, usuario)
    resp = client.post("/usuarios", data={"accion": "desactivar", "usuario_id": str(objetivo.id)})
    assert resp.status_code == 200
    assert "quedó sin acceso" in resp.text

    resp2 = cliente_objetivo.get("/", follow_redirects=False)
    assert resp2.status_code == 303
    assert resp2.headers["location"] == "/login"


def test_no_se_puede_dar_de_baja_la_propia_cuenta(client, db, usuario):
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "desactivar", "usuario_id": str(usuario.id)})

    assert resp.status_code == 400
    assert "propia cuenta" in resp.text
    db.refresh(usuario)
    assert usuario.activo is True


def test_desactivar_con_usuario_id_no_numerico_da_no_encontrado_no_500(client, usuario):
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "desactivar", "usuario_id": "abc"})

    assert resp.status_code == 404
    assert "no encontrada" in resp.text.lower()


def test_desactivar_con_id_inexistente_da_no_encontrado(client, usuario):
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "desactivar", "usuario_id": "999999"})

    assert resp.status_code == 404


def test_desactivar_con_usuario_id_gigante_da_no_encontrado_no_500(client, usuario):
    """int("9" * 30) no lanza ValueError (Python soporta enteros arbitrarios),
    pero pasado tal cual a la consulta contra un INTEGER de la base revienta
    (OverflowError en SQLite). Debe tratarse igual que un id que no existe,
    no como un 500."""
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "desactivar", "usuario_id": "9" * 30})

    assert resp.status_code == 404
    assert "no encontrada" in resp.text.lower()


def test_accion_desconocida_da_400(client, usuario):
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "volar"})

    assert resp.status_code == 400


def test_el_correo_invitado_no_puede_cerrar_el_string_de_js_en_confirm(client, db, usuario, monkeypatch):
    """usuarios.html interpola u.email dentro de un string de JS dentro del
    atributo onsubmit. Jinja escapa la comilla simple a &#39;, pero el
    navegador decodifica esa entidad ANTES de interpretar el atributo como
    JS: la comilla vuelve a ser real y cierra el string dentro de confirm().
    Un correo como a'-alert(1)-'@x.cl pasa _PATRON_EMAIL igual (solo excluye
    '@' y espacios). Se prueba que la pagina no contenga la secuencia que
    cerraria el string de JS ni su version en entidad HTML."""
    _parchar_envio(monkeypatch)
    _con_sesion(client, usuario)
    payload = "a'-alert(1)-'@x.cl"

    resp_post = client.post("/usuarios", data={"accion": "invitar", "email": payload, "nombre": ""})
    assert resp_post.status_code == 200

    resp = client.get("/usuarios")
    assert resp.status_code == 200
    assert usuarios.por_email(db, payload) is not None  # la cuenta si se creo

    # El correo tambien aparece escapado con &#39; en la celda de texto plano
    # de la tabla (eso es normal y no es explotable: ahi no hay JS). Lo que
    # importa es el contenido del atributo onsubmit, asi que se aisla ese
    # atributo en vez de buscar en la pagina entera.
    m = re.search(r"onsubmit='(.*?)'", resp.text, re.DOTALL)
    assert m, "no se encontro el atributo onsubmit del boton Dar de baja"
    atributo = m.group(1)

    # Si el correo se interpolara sin |tojson (o con la variante que usa un
    # atributo onsubmit entre comillas dobles), apareceria aqui la secuencia
    # que cierra el string de JS: una comilla real o &#39; (que el navegador
    # decodifica a comilla real ANTES de interpretar el atributo como JS).
    assert "&#39;" not in atributo
    assert "\\u0027" in atributo  # el payload sigue presente, pero escapado por tojson


def test_reactivar_devuelve_el_acceso_y_manda_un_enlace_nuevo(client, db, usuario, monkeypatch):
    llamadas = _parchar_envio(monkeypatch)
    objetivo = usuarios.crear(db, "otra@gridworks.cl", nombre="Otra", clave="clave-larga-2")
    usuarios.desactivar(db, objetivo)
    resultado_dado_de_baja, _ = usuarios.autenticar(db, "otra@gridworks.cl", "clave-larga-2")
    assert resultado_dado_de_baja is usuarios.Resultado.INACTIVO

    _con_sesion(client, usuario)
    resp = client.post("/usuarios", data={"accion": "reactivar", "usuario_id": str(objetivo.id)})

    assert resp.status_code == 200
    assert "recuperó el acceso" in resp.text
    # La clave anterior sigue sirviendo: reactivar no la toca, solo vuelve a
    # habilitar la cuenta y ofrece un enlace nuevo por si se perdio el viejo.
    resultado, _ = usuarios.autenticar(db, "otra@gridworks.cl", "clave-larga-2")
    assert resultado is usuarios.Resultado.OK
    assert llamadas == ["otra@gridworks.cl"]


def test_reactivar_con_id_inexistente_da_no_encontrado(client, usuario):
    _con_sesion(client, usuario)

    resp = client.post("/usuarios", data={"accion": "reactivar", "usuario_id": "999999"})

    assert resp.status_code == 404


# --- Re-parseo de los PDF guardados ---
# Se prueba la maquina de estados sin lanzar el hilo: _run_reparse_bg se llama
# derecho, con reparse_all reemplazado. Asi no hay que esperar a un thread ni
# dejar que toque la base, y el resultado es determinista.

@pytest.fixture(autouse=True)
def _reparse_limpio():
    """El estado del reparse es un dict de modulo, o sea global entre pruebas."""
    original = dict(main._reparse_state)
    yield
    main._reparse_state.clear()
    main._reparse_state.update(original)


def test_reparse_deja_el_estado_terminado_y_el_resumen(monkeypatch):
    def falso_reparse(progress=None, solo_pendientes=True):
        stats = {"total": 104, "procesados": 104, "revision_manual": 0,
                 "falta_proveedor": 1, "sin_pdf": 0, "errores": 0, "tc_consultados": 2,
                 "omitidas_declaradas": 2}
        if progress is not None:
            progress.update(stats)
        return stats

    monkeypatch.setattr("app.tasks.reparse.reparse_all", falso_reparse)
    main._run_reparse_bg()

    assert main._reparse_state["running"] is False
    assert main._reparse_state["terminado"] is True
    assert main._reparse_state["total"] == 104
    assert "Pendientes re-parseadas 104" in main._reparse_state["mensaje"]
    assert "falta proveedor 1" in main._reparse_state["mensaje"]
    # Lo omitido se dice: callarlo haria leer el resumen como cobertura total
    assert "2 ya declaradas" in main._reparse_state["mensaje"]


def test_el_boton_nunca_pide_re_parsear_las_declaradas(monkeypatch):
    """El default de reparse_all ya es solo_pendientes=True, pero el boton no
    debe depender de eso: si alguien cambia el default por consola, la web
    seguiria sin tocar lo ya declarado."""
    recibido = {}

    def falso_reparse(progress=None, solo_pendientes=True):
        recibido["solo_pendientes"] = solo_pendientes
        return {"total": 0, "procesados": 0, "revision_manual": 0, "falta_proveedor": 0,
                "sin_pdf": 0, "errores": 0, "tc_consultados": 0, "omitidas_declaradas": 0}

    monkeypatch.setattr("app.tasks.reparse.reparse_all", falso_reparse)
    main._run_reparse_bg()

    assert recibido["solo_pendientes"] is True


def test_si_reparse_revienta_el_estado_no_queda_colgado(monkeypatch):
    """Sin el finally, running se quedaria en True para siempre y el boton
    nunca volveria a habilitarse en ninguna sesion."""
    def reventar(progress=None, solo_pendientes=True):
        raise RuntimeError("base caida")

    monkeypatch.setattr("app.tasks.reparse.reparse_all", reventar)
    main._reparse_state["running"] = True
    main._run_reparse_bg()

    assert main._reparse_state["running"] is False
    assert main._reparse_state["terminado"] is True
    assert "base caida" in main._reparse_state["mensaje"]


def test_no_se_puede_lanzar_un_reparse_sobre_otro_en_curso(client, usuario):
    """Dos reparse en paralelo escribirian las mismas filas a la vez."""
    _con_sesion(client, usuario)
    main._reparse_state["running"] = True

    resp = client.post("/reparse")

    assert resp.status_code == 200
    assert resp.json() == {"running": True, "ya_en_curso": True}


def _sembrar_estados(db):
    """Tres facturas sin PDF: una pendiente, una declarada y una con estado NULL.
    Sin pdf_data el reparse las cuenta y las salta, que es suficiente para
    verificar A CUALES eligio, sin necesitar PDFs de verdad.

    El estado NULL se fuerza por SQL a proposito: asignar estado=None en el
    modelo no sirve, porque SQLAlchemy no distingue "None explicito" de "sin
    asignar" y le aplica igual el default 'pendiente' de la columna."""
    from sqlalchemy import text as sql

    from app.models import Purchase

    db.add_all([
        Purchase(gmail_message_id="m-pendiente", estado="pendiente"),
        Purchase(gmail_message_id="m-declarada", estado="aceptada"),
        Purchase(gmail_message_id="m-sin-estado", estado="pendiente"),
    ])
    db.commit()
    db.execute(sql("update purchases set estado = NULL where gmail_message_id = 'm-sin-estado'"))
    db.commit()


def _reparse_contra(db, monkeypatch, **kwargs):
    from app.tasks import reparse as mod

    monkeypatch.setattr(mod, "SessionLocal", lambda: db)
    monkeypatch.setattr(mod, "init_db", lambda: None)
    monkeypatch.setattr(mod.mantenedor, "ensure_seed", lambda s: None)
    return mod.reparse_all(**kwargs)


def test_reparse_deja_fuera_las_ya_declaradas(db, monkeypatch):
    """Una factura aceptada ya se declaro al SII con los montos que tenia:
    recalcularselos dejaria la base y la declaracion presentada diciendo cosas
    distintas."""
    _sembrar_estados(db)

    stats = _reparse_contra(db, monkeypatch)

    assert stats["total"] == 2                  # la pendiente y la de estado NULL
    assert stats["omitidas_declaradas"] == 1


def test_reparse_no_se_salta_las_pendientes_con_estado_nulo(db, monkeypatch):
    """`estado != 'aceptada'` a secas las dejaria fuera, porque en SQL
    NULL != 'aceptada' no es verdadero sino NULL."""
    _sembrar_estados(db)

    stats = _reparse_contra(db, monkeypatch)

    assert stats["total"] == 2


def test_reparse_con_todas_incluye_las_declaradas(db, monkeypatch):
    """La valvula de escape para rectificar una ya declarada, solo por consola."""
    _sembrar_estados(db)

    stats = _reparse_contra(db, monkeypatch, solo_pendientes=False)

    assert stats["total"] == 3
    assert stats["omitidas_declaradas"] == 0
