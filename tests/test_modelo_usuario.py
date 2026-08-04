from app.models import Usuario, ahora_utc


def test_usuario_nace_activo_y_con_version_uno(db):
    u = Usuario(email="rafael@gridworks.cl", password_hash="x")
    db.add(u)
    db.commit()

    guardado = db.query(Usuario).first()
    assert guardado.activo is True
    assert guardado.token_version == 1
    assert guardado.intentos_fallidos == 0
    assert guardado.bloqueado_hasta is None
    assert guardado.creado_en is not None


def test_ahora_utc_es_naive():
    """Si tuviera zona, compararlo contra lo que devuelve SQLite lanzaria
    TypeError y el bloqueo por intentos fallidos reventaria solo en local."""
    assert ahora_utc().tzinfo is None
