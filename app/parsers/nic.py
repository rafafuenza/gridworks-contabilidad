"""Parser para facturas de NIC Chile (dominios .cl), DTE nacional.

Es una "Factura No Afecta o Exenta Electronica" (DTE 34), en CLP: no lleva IVA,
el monto va todo a Exento. El RUT del emisor viene en el propio documento.
"""
import re
from datetime import date

from app.parsers.base import ParsedInvoice

FINGERPRINT = "NIC Chile"

RUT_NIC = "60.910.000-1"  # emisor: Corporacion Educacional y Servicios Profesionales

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _clp(raw: str) -> float:
    # CLP en formato chileno: el punto es separador de miles, sin decimales
    return float(raw.replace(".", "").replace(",", ""))


def parse(text: str) -> ParsedInvoice:
    result = ParsedInvoice(moneda="CLP", proveedor="NIC Chile", rut_proveedor=RUT_NIC)

    # RUT del emisor (primer R.U.T. del documento, antes del receptor)
    m = re.search(r"R\.U\.T\.:\s*([\d\.]+-[\dkK])", text)
    if m:
        result.rut_proveedor = m.group(1)

    # Folio: "N° 1317722"
    m = re.search(r"N[°º]\s*(\d+)", text)
    if m:
        result.numero = m.group(1)

    # Fecha: "Santiago, 29 de Junio de 2026"
    m = re.search(r"(\d{1,2}) de ([A-Za-zéáíóú]+) de (\d{4})", text, re.I)
    if m:
        mes = MESES.get(m.group(2).lower())
        if mes:
            try:
                result.fecha = date(int(m.group(3)), mes, int(m.group(1)))
            except ValueError:
                pass

    # Montos: es exenta, todo va a Monto Exento; IVA y afecto en 0
    m = re.search(r"Monto Exento\s+([\d.]+)", text)
    if m:
        result.monto_exento = _clp(m.group(1))
    result.monto_afecto = 0.0
    result.iva = 0.0

    m = re.search(r"(?:^|\n)Total\s+([\d.]+)", text)
    if m:
        result.total = _clp(m.group(1))
    elif result.monto_exento is not None:
        result.total = result.monto_exento

    return result
