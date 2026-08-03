from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Date, Numeric, DateTime, Boolean, Text, LargeBinary
from sqlalchemy.sql import func

from app.db import Base


def ahora_utc() -> datetime:
    """UTC sin zona horaria. Las columnas que se comparan en Python usan DateTime
    naive porque SQLite no guarda la zona: si guardaramos un datetime con zona,
    al releerlo vendria sin ella y la comparacion lanzaria TypeError."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Proveedor(Base):
    """Mantenedor de empresas: fuente de verdad de los datos fiscales de cada
    proveedor. Se puebla manualmente (yo lo enriquezco desde la nomina IVA
    digital del SII cuando aparece un proveedor nuevo)."""
    __tablename__ = "proveedores"

    id = Column(Integer, primary_key=True)
    clave = Column(String, unique=True, nullable=False, index=True)  # slug de deteccion, ej "aws"
    nombre = Column(String, nullable=False)
    rut = Column(String, nullable=True)
    pais = Column(String, nullable=True)
    moneda_default = Column(String, nullable=True)          # "USD" | "CLP"
    tratamiento = Column(String, nullable=True)             # "afecto" | "exento"
    en_nomina_iva_digital = Column(Boolean, default=False)
    fuente_rut = Column(String, nullable=True)              # de donde salio el RUT
    notas = Column(Text, nullable=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    actualizado_en = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
    tipo_cambio = Column(Numeric(10, 2), nullable=True)  # USD/CLP (dolar observado) usado para convertir
    tipo_cambio_fecha = Column(Date, nullable=True)  # fecha del dolar observado aplicado (puede ser dia habil previo)

    pdf_filename = Column(String, nullable=True)
    pdf_data = Column(LargeBinary, nullable=True)

    revision_manual = Column(Boolean, default=False)  # True si el parser no pudo extraer todo con confianza
    falta_proveedor = Column(Boolean, default=False)  # True si el proveedor no esta en el mantenedor con RUT real
    estado = Column(String, default="pendiente", index=True)  # "pendiente" | "aceptada"
    notas = Column(Text, nullable=True)

    creado_en = Column(DateTime(timezone=True), server_default=func.now())


class Usuario(Base):
    """Cuenta de acceso a la aplicacion. Todas las cuentas tienen los mismos
    permisos: no hay campo de rol."""
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)  # siempre en minusculas
    nombre = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)

    # Va dentro de la cookie firmada y de los tokens de recuperacion. Subirlo
    # invalida de inmediato todas las sesiones y enlaces de esta cuenta.
    token_version = Column(Integer, nullable=False, default=1)

    activo = Column(Boolean, nullable=False, default=True)
    intentos_fallidos = Column(Integer, nullable=False, default=0)
    bloqueado_hasta = Column(DateTime, nullable=True)
    ultimo_ingreso = Column(DateTime, nullable=True)
    reset_enviado_en = Column(DateTime, nullable=True)  # freno al reenvio de enlaces
    creado_en = Column(DateTime, nullable=False, default=ahora_utc)
