"""Entrypoint para el Cron Job de Railway. Corre la sincronizacion sin contexto web.
Uso: python -m app.tasks.monthly_cron
"""
import logging

from app.db import SessionLocal, init_db
from app import gmail_sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("monthly_cron")


def main():
    init_db()
    db = SessionLocal()
    try:
        stats = gmail_sync.sync(db)
        logger.info("Sync completado: %s", stats)
    finally:
        db.close()


if __name__ == "__main__":
    main()
