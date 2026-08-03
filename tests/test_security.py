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
