from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import usuarios
from app import models  # noqa: F401  (registra los modelos en Base)
from app.db import Base
from app.models import ahora_utc
from app.usuarios import Resultado


def test_crear_normaliza_el_correo_a_minusculas(db):
    u = usuarios.crear(db, "Rafael@GridWorks.CL", nombre="Rafael", clave="clave-larga-1")

    assert u.email == "rafael@gridworks.cl"


def test_autenticar_con_la_clave_correcta(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    resultado, u = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")

    assert resultado is Resultado.OK
    assert u.email == "rafael@gridworks.cl"
    assert u.ultimo_ingreso is not None


def test_autenticar_ignora_mayusculas_del_correo(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    resultado, _ = usuarios.autenticar(db, "  Rafael@GridWorks.CL  ", "clave-larga-1")

    assert resultado is Resultado.OK


def test_autenticar_con_la_clave_mala(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    resultado, u = usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")

    assert resultado is Resultado.CREDENCIALES_INVALIDAS
    assert u is None


def test_autenticar_con_un_correo_que_no_existe(db):
    resultado, u = usuarios.autenticar(db, "nadie@gridworks.cl", "lo-que-sea")

    assert resultado is Resultado.CREDENCIALES_INVALIDAS
    assert u is None


def test_una_cuenta_dada_de_baja_no_entra(db):
    u = usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")
    usuarios.desactivar(db, u)

    resultado, _ = usuarios.autenticar(db, "contador@gridworks.cl", "clave-larga-1")

    assert resultado is Resultado.INACTIVO


def test_crear_sin_clave_genera_una_al_azar(db):
    """Las invitaciones crean la cuenta sin que nadie escriba una clave."""
    u = usuarios.crear(db, "contador@gridworks.cl")

    assert u.password_hash


def test_cambiar_clave_revoca_la_anterior_y_habilita_la_nueva(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-vieja-1")

    usuarios.cambiar_clave(db, u, "clave-nueva-1")

    resultado_vieja, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-vieja-1")
    resultado_nueva, u2 = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-nueva-1")

    assert resultado_vieja is Resultado.CREDENCIALES_INVALIDAS
    assert resultado_nueva is Resultado.OK
    assert u2.email == "rafael@gridworks.cl"


def test_el_bloqueo_no_se_pierde_con_intentos_en_paralelo(tmp_path, hasheo_real):
    """_registrar_intento_fallido incrementa con una version no atomica
    (leer en Python, sumar 1, escribir): como verify_password tarda ~650 ms,
    varios intentos fallidos simultaneos leerian todos el mismo valor viejo
    y se pisarian al escribir, dejando el contador en 1 para siempre y la
    cuenta jamas se bloquea. El fixture db no sirve aqui porque entrega una
    sola Session; se arma un engine propio contra un archivo SQLite real
    (no StaticPool: eso comparte una sola conexion entre los 8 hilos, y
    sqlite3 no soporta cursor.execute concurrente sobre una misma conexion
    aunque se pase check_same_thread=False) para que cada hilo abra su propia
    conexion, como pasaria con Sessions reales por-request en FastAPI."""
    engine = create_engine(f"sqlite:///{tmp_path}/concurrencia.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    sesion_inicial = Session()
    usuarios.crear(sesion_inicial, "concurrencia@gridworks.cl", clave="clave-larga-1")
    sesion_inicial.close()

    def intento_fallido(_):
        sesion = Session()
        try:
            resultado, _ = usuarios.autenticar(sesion, "concurrencia@gridworks.cl", "clave-mala")
            return resultado
        finally:
            sesion.close()

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(intento_fallido, range(8)))

    sesion_verificacion = Session()
    try:
        u = usuarios.por_email(sesion_verificacion, "concurrencia@gridworks.cl")
        # con el bug, esto queda en None porque el contador nunca supera 1
        assert u.bloqueado_hasta is not None
    finally:
        sesion_verificacion.close()
        engine.dispose()


def test_cinco_intentos_fallidos_bloquean_la_cuenta(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")

    for _ in range(usuarios.MAX_INTENTOS):
        resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")
        assert resultado is Resultado.CREDENCIALES_INVALIDAS

    # La clave correcta tampoco entra mientras dure el bloqueo
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is Resultado.BLOQUEADO

    # El bloqueo es por cuenta: la otra persona sigue entrando sin problema
    resultado_otro, _ = usuarios.autenticar(db, "contador@gridworks.cl", "clave-larga-1")
    assert resultado_otro is Resultado.OK


def test_el_bloqueo_se_suelta_cuando_vence(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    for _ in range(usuarios.MAX_INTENTOS):
        usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")

    # Se confirma lo que produccion realmente escribio (un mutante que deje
    # el bloqueo en, por ejemplo, timedelta(seconds=1) pasaria las pruebas
    # de todos modos si esto no se revisa antes de pisar el valor).
    assert timedelta(minutes=14) < u.bloqueado_hasta - ahora_utc() <= timedelta(minutes=15)

    u.bloqueado_hasta = ahora_utc() - timedelta(seconds=1)
    db.commit()

    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is Resultado.OK
    assert u.bloqueado_hasta is None


def test_entrar_bien_resetea_el_contador(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    for _ in range(usuarios.MAX_INTENTOS - 1):
        usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")

    usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    u = usuarios.por_email(db, "rafael@gridworks.cl")
    assert u.intentos_fallidos == 0

    # Y el contador parte de cero de nuevo, no queda a un paso del bloqueo
    for _ in range(usuarios.MAX_INTENTOS - 1):
        usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is Resultado.OK


def test_cambiar_la_clave_sube_la_version_y_suelta_el_bloqueo(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    version_inicial = u.token_version
    for _ in range(usuarios.MAX_INTENTOS):
        usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")

    usuarios.cambiar_clave(db, u, "clave-nueva-larga-2")

    assert u.token_version == version_inicial + 1
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-nueva-larga-2")
    assert resultado is Resultado.OK


def test_desactivar_sube_la_version(db):
    u = usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")
    version_inicial = u.token_version

    usuarios.desactivar(db, u)

    assert u.token_version == version_inicial + 1


def test_no_se_puede_pedir_dos_enlaces_seguidos(db):
    """Sin esto, cualquiera que conozca el correo llena la bandeja y quema
    la cuota SMTP de la cuenta de Gmail."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    assert usuarios.puede_enviar_reset(u) is True
    usuarios.marcar_reset_enviado(db, u)
    assert usuarios.puede_enviar_reset(u) is False


def test_el_freno_se_suelta_al_pasar_la_espera(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    usuarios.marcar_reset_enviado(db, u)

    u.reset_enviado_en = ahora_utc() - timedelta(minutes=usuarios.RESET_ESPERA_MINUTOS, seconds=1)
    db.commit()

    assert usuarios.puede_enviar_reset(u) is True
