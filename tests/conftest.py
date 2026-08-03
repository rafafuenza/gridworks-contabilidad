import os

# Debe ir antes de importar cualquier cosa de app: config.py se niega a cargar
# sin SECRET_KEY, y no queremos que la suite dependa del .env de la maquina.
os.environ.setdefault("SECRET_KEY", "clave-solo-para-pruebas")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base

import app.security

# Las pruebas hashean decenas de veces y scrypt real cuesta ~600 ms por
# llamada. Se baja el costo para la suite, no para produccion. El piso es
# 2**12 porque verify_password rechaza cualquier n menor, asi que bajar mas
# no acelera en silencio: rompe todas las pruebas de login de golpe.
#
# Esto debe correr antes de que se importe app.usuarios: ese modulo calcula
# _HASH_DESCARTE con hash_password() al importarse, usando el _N vigente en
# ese momento. Si el parche llegara despues de ese import, el hash de
# descarte quedaria al costo de produccion y casi no se ahorraria nada.
# conftest.py se importa antes que los modulos de prueba, asi que esto
# corre a tiempo.
N_PRODUCCION = app.security._N
app.security._N = 2 ** 12


@pytest.fixture
def hasheo_real():
    """Para las pruebas que necesitan probar el costo real de produccion:
    el round-trip de hash/verify y la codificacion de los parametros."""
    app.security._N = N_PRODUCCION
    yield
    app.security._N = 2 ** 12


@pytest.fixture
def db():
    """Base SQLite en memoria, nueva para cada prueba.

    StaticPool hace que todas las conexiones compartan la misma base en
    memoria; sin eso cada conexion abriria una base vacia distinta."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app import models  # noqa: F401  (registra los modelos en Base)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    sesion = Session()
    try:
        yield sesion
    finally:
        sesion.close()
        engine.dispose()
