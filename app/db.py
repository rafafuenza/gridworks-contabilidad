from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401  (registra los modelos en Base)
    Base.metadata.create_all(bind=engine)
    _run_light_migrations()


# Columnas agregadas despues del primer deploy. create_all() no altera tablas
# existentes, asi que las agregamos a mano si faltan (idempotente, SQLite y Postgres).
_ADDED_COLUMNS = {
    "purchases": {
        "tipo_cambio_fecha": "DATE",
        "falta_proveedor": "BOOLEAN DEFAULT FALSE",
        "estado": "VARCHAR DEFAULT 'pendiente'",
    },
}


def _run_light_migrations():
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl_type in cols.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl_type}'))
