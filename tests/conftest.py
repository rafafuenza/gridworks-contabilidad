import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base


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
