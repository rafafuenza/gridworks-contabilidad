import re
from dateutil import parser as dateparser

from app.parsers.base import ParsedInvoice

RUT_GENERICO_EXTRANJERO = "55.555.555-5"  # RUT generico SII para proveedor extranjero sin registro


def parse(text: str, sender_name: str = "", sender_domain: str = "") -> ParsedInvoice:
    """Best-effort para proveedores sin parser dedicado. Siempre queda marcado para revision manual."""
    result = ParsedInvoice(
        proveedor=sender_name or sender_domain or "Desconocido",
        rut_proveedor=RUT_GENERICO_EXTRANJERO,
        revision_manual=True,
        notas="Proveedor sin parser dedicado, extraccion best-effort. Revisar montos manualmente.",
    )

    # moneda: si aparece USD explicito, asumimos USD; si no, CLP
    result.moneda = "USD" if re.search(r"\bUSD\b", text) else "CLP"

    # fecha: primer patron tipo "18 de abril de 2026", "April 18, 2026" o "18-04-2026" que aparezca
    date_patterns = [
        r"\b([A-Za-z]+ \d{1,2},\s*\d{4})\b",
        r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{4})\b",
        r"\b(\d{1,2} de [a-zA-Z]+ de \d{4})\b",
    ]
    for pat in date_patterns:
        m = re.search(pat, text)
        if m:
            try:
                result.fecha = dateparser.parse(m.group(1), dayfirst=True, fuzzy=True).date()
                break
            except (ValueError, OverflowError):
                continue

    # numero de factura/folio: busca "Invoice", "Factura", "N°", "No." seguido de digitos/codigo
    m = re.search(r"(?:Invoice|Factura|Folio|N[°º]?\.?)\s*[:#]?\s*([A-Z0-9\-]{3,})", text, re.I)
    if m:
        result.numero = m.group(1)

    # total: ultima ocurrencia de "Total" seguida de un monto en la misma linea
    totals = re.findall(r"Total\D{0,15}?([\d.,]+\d)", text)
    if totals:
        try:
            result.total = float(totals[-1].replace(".", "").replace(",", ".")) if "," in totals[-1] else float(totals[-1].replace(",", ""))
        except ValueError:
            pass

    return result
