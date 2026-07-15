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
    revision_manual: bool = False
    notas: Optional[str] = None
