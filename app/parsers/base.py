from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class ParsedInvoice:
    numero: Optional[str] = None
    fecha: Optional[date] = None
    rut_proveedor: Optional[str] = None
    proveedor: Optional[str] = None
    descripcion: Optional[str] = None
    moneda: Optional[str] = None  # "USD" | "CLP"
    monto_afecto: Optional[float] = None
    monto_exento: Optional[float] = None
    iva: Optional[float] = None
    total: Optional[float] = None
    provider_key: Optional[str] = None   # slug del proveedor detectado, para cruzar con el mantenedor
    revision_manual: bool = False
    falta_proveedor: bool = False        # True si el proveedor no esta en el mantenedor con RUT real
    notas: Optional[str] = None
