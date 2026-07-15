"""Parser para facturas de Amazon Web Services (formato propio, en USD).

Nota tributaria: AWS cobra 19% en la linea "Tax" (IVA chileno). Se extrae tal cual;
si ese IVA es o no recuperable como credito fiscal es criterio del contador.
"""
import re

from app.parsers.base import ParsedInvoice

FINGERPRINT = "Amazon Web Services"


def _money(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse(text: str) -> ParsedInvoice:
    result = ParsedInvoice(moneda="USD", monto_exento=0.0)

    m = re.search(r"Invoice Number:\s*(\d+)", text)
    if m:
        result.numero = m.group(1)

    # "Invoice Date: July 1 , 2026" (a veces con espacio antes de la coma)
    m = re.search(r"Invoice Date:\s*([A-Za-z]+ \d{1,2}\s*,\s*\d{4})", text)
    if m:
        from dateutil import parser as dateparser
        limpio = re.sub(r"\s+,", ",", m.group(1))
        try:
            result.fecha = dateparser.parse(limpio).date()
        except (ValueError, OverflowError):
            pass

    # Bloque Summary: Charges (afecto) / [Credits] / Tax (iva) / Total for this invoice
    m = re.search(
        r"Charges USD ([\d,]+\.\d{2})\s*\n(?:Credits USD [\d,]+\.\d{2}\s*\n)?"
        r"Tax USD ([\d,]+\.\d{2})\s*\nTotal for this invoice USD ([\d,]+\.\d{2})",
        text,
    )
    if m:
        result.monto_afecto = _money(m.group(1))
        result.iva = _money(m.group(2))
        result.total = _money(m.group(3))

    return result
