from sqlalchemy import Column, Integer, String, Date, Numeric, DateTime, Boolean, Text, LargeBinary
from sqlalchemy.sql import func

from app.db import Base


class Purchase(Base):
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True)

    # Identidad del correo/adjunto de origen, para no procesar dos veces
    gmail_message_id = Column(String, unique=True, nullable=False, index=True)
    gmail_thread_id = Column(String, nullable=True)

    # Datos del documento
    numero = Column(String, nullable=True)
    fecha = Column(Date, nullable=True, index=True)
    periodo = Column(String, nullable=True, index=True)  # "YYYY-MM", para filtrar por mes
    rut_proveedor = Column(String, nullable=True)
    proveedor = Column(String, nullable=True)
    descripcion = Column(Text, nullable=True)

    moneda = Column(String, nullable=True)  # "USD" o "CLP"
    monto_afecto = Column(Numeric(14, 2), nullable=True)
    monto_exento = Column(Numeric(14, 2), nullable=True)
    iva = Column(Numeric(14, 2), nullable=True)
    total = Column(Numeric(14, 2), nullable=True)
    tipo_cambio = Column(Numeric(10, 2), nullable=True)  # USD/CLP del dia de la factura, informativo

    pdf_filename = Column(String, nullable=True)
    pdf_data = Column(LargeBinary, nullable=True)

    revision_manual = Column(Boolean, default=False)  # True si el parser no pudo extraer todo con confianza
    notas = Column(Text, nullable=True)

    creado_en = Column(DateTime(timezone=True), server_default=func.now())
