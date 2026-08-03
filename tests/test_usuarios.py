from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import usuarios
from app import models  # noqa: F401  (registra los modelos en Base)
from app.db import Base
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


def test_el_bloqueo_no_se_pierde_con_intentos_en_paralelo(tmp_path):
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
