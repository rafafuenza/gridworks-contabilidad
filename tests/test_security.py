import base64
import unicodedata

from app.security import hash_password, verify_password, generar_clave_aleatoria


def test_hash_y_verify_hacen_ida_y_vuelta():
    guardado = hash_password("clave-secreta-123")

    assert verify_password("clave-secreta-123", guardado) is True
    assert verify_password("otra-clave", guardado) is False


def test_la_misma_clave_produce_hashes_distintos():
    """Si dos hashes de la misma clave salieran iguales, no habria salt y
    una tabla precalculada rompería todas las cuentas de una vez."""
    uno = hash_password("misma-clave")
    otro = hash_password("misma-clave")

    assert uno != otro
    assert verify_password("misma-clave", uno) is True
    assert verify_password("misma-clave", otro) is True


def test_verify_rechaza_un_hash_malformado_sin_reventar():
    """Una fila corrupta en la base no debe tumbar el login con un 500."""
    for basura in ["", "no-es-un-hash", "scrypt$mal", "scrypt$a$b$c$d$e"]:
        assert verify_password("cualquiera", basura) is False


def test_la_clave_aleatoria_tiene_largo_util():
    clave = generar_clave_aleatoria()

    assert len(clave) >= 16
    assert clave != generar_clave_aleatoria()


def test_la_codificacion_es_la_que_decimos():
    guardado = hash_password("x")
    partes = guardado.split("$")

    assert len(partes) == 6
    assert tuple(partes[:4]) == ("scrypt", "65536", "8", "1")
    assert len(base64.b64decode(partes[5])) == 32


def test_verify_rechaza_un_algoritmo_distinto():
    assert verify_password("x", "bcrypt$65536$8$1$AAAA$AAAA") is False


def test_verify_rechaza_parametros_fuera_de_rango_sin_asignar_memoria():
    guardado = hash_password("x")
    partes = guardado.split("$")
    partes[1] = "99999999"
    manipulado = "$".join(partes)

    assert verify_password("x", manipulado) is False


def test_una_clave_vacia_hace_ida_y_vuelta():
    assert verify_password("", hash_password("")) is True


def test_una_clave_con_tildes_hace_ida_y_vuelta_en_cualquier_forma_unicode():
    original = "clave-nñandú"
    nfc = unicodedata.normalize("NFC", original)
    nfd = unicodedata.normalize("NFD", original)

    guardado = hash_password(nfc)

    assert verify_password(nfd, guardado) is True
    assert verify_password(nfc, hash_password(nfd)) is True


def test_generar_clave_aleatoria_usa_bytes_no_caracteres():
    clave = generar_clave_aleatoria(32)

    assert len(clave) > 32
