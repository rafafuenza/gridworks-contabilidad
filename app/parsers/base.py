import re
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


_BASURA = re.compile(r"[^\d.,-]")


def parse_monto(raw: Optional[str]) -> Optional[float]:
    """Convierte un monto escrito a float, sin importar la convencion de separadores.

    El punto y la coma estan invertidos entre la convencion chilena
    ("2.273.680") y la anglosajona ("6,800.00"), y no se puede decidir por la
    moneda de la factura: el PDF de BIO ANDES esta en USD y trae las dos
    convenciones en el mismo documento ("$6,800.00" y "USD 6.800,00"). La regla
    que se aplica es estructural, no por moneda: **manda el separador que este
    mas a la derecha**, que es siempre el decimal.

    Caso ambiguo aceptado: con un solo separador seguido de exactamente 3
    digitos ("6.800", "1,500") se asume miles, porque es lo correcto para CLP y
    para los miles anglosajones. Eso lee mal un hipotetico "1.500" que quisiera
    decir un dolar y medio, pero las facturas escriben eso como "1.50".

    Devuelve None si no hay nada convertible, para que la capa de validacion del
    registry marque `revision_manual` en vez de dejar pasar un numero plausible
    pero equivocado (el bug que hacia leer "$6,800.00" como 6.8).
    """
    if raw is None:
        return None

    s = _BASURA.sub("", str(raw)).strip()
    negativo = s.startswith("-")
    s = s.lstrip("-").rstrip(".,")
    if not s or not any(c.isdigit() for c in s):
        return None

    ult_punto, ult_coma = s.rfind("."), s.rfind(",")

    if ult_punto >= 0 and ult_coma >= 0:
        if ult_punto > ult_coma:          # 6,800.00 -> la coma es de miles
            s = s.replace(",", "")
        else:                             # 6.800,00 -> el punto es de miles
            s = s.replace(".", "").replace(",", ".")
    elif ult_punto >= 0 or ult_coma >= 0:
        sep = "." if ult_punto >= 0 else ","
        decimales = len(s) - s.rfind(sep) - 1
        if s.count(sep) > 1 or decimales == 3:   # 2.273.680 / 6.800 -> miles
            s = s.replace(sep, "")
        else:                                     # 6.80 / 6,8 -> decimal
            s = s.replace(sep, ".")

    try:
        valor = float(s)
    except ValueError:
        return None
    return -valor if negativo else valor
