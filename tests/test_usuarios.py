from app import usuarios
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
