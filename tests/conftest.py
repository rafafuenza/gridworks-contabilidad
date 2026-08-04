import os

# Debe ir antes de importar cualquier cosa de app: app.auth se niega a cargar
# sin SECRET_KEY, y no queremos que la suite dependa del .env de la maquina.
# Se pisa sin condicion, no con setdefault: con setdefault una SECRET_KEY corta
# en el ambiente rompe la suite entera al recolectar, y una larga se cuela como
# la clave con que se firman las cookies de prueba.
os.environ["SECRET_KEY"] = "clave-solo-para-pruebas-con-largo-suficiente-abcdefgh"

# El fixture 'db' ya usa su propio engine SQLite en memoria via dependency_overrides,
# pero eso descansa en que nada dispare el lifespan de la app real. Fijar esto
# aqui es una segunda red: aunque algo importe app.db y toque el engine a nivel
# de modulo, no puede llegar a local.db ni, peor, a un Postgres real de Railway.
os.environ["DATABASE_URL"] = "sqlite://"

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


@pytest.fixture
def client(db):
    """Cliente HTTP contra la app real, con la base de pruebas inyectada.

    raise_server_exceptions=False deja que el 303 que lanza require_login se
    vea como respuesta en vez de propagarse como excepcion.

    OJO: no se usa 'with TestClient(...)'. El context manager dispara los
    eventos de startup, y on_startup llama a init_db() y ensure_seed() contra
    el engine real: las pruebas escribirian en local.db (o peor, en el Postgres
    de Railway si DATABASE_URL esta apuntando alla). Sin el 'with', el lifespan
    no corre y la app usa solo la base inyectada por dependency_overrides."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
