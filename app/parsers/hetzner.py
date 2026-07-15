"""Parser para facturas de Hetzner Online GmbH (Alemania, formato propio, en USD).

Hetzner factura como proveedor extranjero sin IVA chileno (Tax 0%). Los montos
vienen con simbolo "$" y esta cuenta se factura en USD.
"""
import re

from app.parsers.base import ParsedInvoice

FINGERPRINT = "Hetzner Online"


def _money(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse(text: str) -> ParsedInvoice:
    result = ParsedInvoice(moneda="USD", monto_exento=0.0)

    m = re.search(r"Invoice no\.:\s*(\d+)", text)
    if m:
        result.numero = m.group(1)

    # "Invoice date: 05/07/2026" (DD/MM/YYYY)
    m = re.search(r"Invoice date:\s*(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        from datetime import date
        try:
            result.fecha = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # Fila de totales del Overview: "Total $ 0.29 $ 0.00 $ 0.29" (excl.VAT, Tax, Total)
    m = re.search(
        r"\nTotal \$ ([\d,]+\.\d{2}) \$ ([\d,]+\.\d{2}) \$ ([\d,]+\.\d{2})",
        text,
    )
    if m:
        result.monto_afecto = _money(m.group(1))
        result.iva = _money(m.group(2))
        result.total = _money(m.group(3))

    return result
