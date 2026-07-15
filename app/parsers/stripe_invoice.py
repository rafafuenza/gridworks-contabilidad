"""Parser para facturas generadas con la plantilla de Stripe.

Muchos SaaS facturan con Stripe y comparten exactamente el mismo layout
(Anthropic, Railway, y a futuro la mayoria de los servicios cloud). Un solo
parser cubre a todos; lo que cambia por proveedor (RUT, nombre, tratamiento)
vive en la config del registry, no aca.

Maneja las dos variantes de la plantilla:
- con IVA/VAT explicito (ej. Anthropic): hay linea "Total excluding tax" y "VAT ..."
- sin impuesto (ej. Railway): solo Subtotal / Total / Amount due
"""
import re

from app.parsers.base import ParsedInvoice

# Marcadores que identifican la plantilla Stripe (usado por el registry para detectar)
FINGERPRINTS = ("Invoice number", "Date of issue", "Amount due")


def looks_like_stripe(text: str) -> bool:
    return all(fp in text for fp in FINGERPRINTS)


def _money(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse(text: str) -> ParsedInvoice:
    result = ParsedInvoice(moneda="USD", monto_exento=0.0)

    # Numero: "Invoice number AHJUD2FR 0046" -> "AHJUD2FR-0046"
    m = re.search(r"Invoice number\s+([A-Z0-9]+)\s+(\d+)", text)
    if m:
        result.numero = f"{m.group(1)}-{m.group(2)}"

    # Fecha de emision: "Date of issue July 2, 2026"
    m = re.search(r"Date of issue\s+([A-Za-z]+ \d{1,2},\s*\d{4})", text)
    if m:
        from dateutil import parser as dateparser
        try:
            result.fecha = dateparser.parse(m.group(1)).date()
        except (ValueError, OverflowError):
            pass

    # Descripcion: lineas de items entre el header de la tabla y "Subtotal"
    m = re.search(r"Description Qty Unit price(?: Tax)? Amount\n(.*?)\nSubtotal", text, re.S)
    if m:
        items = []
        for line in m.group(1).splitlines():
            line = line.strip()
            if "$" not in line:
                continue
            # deja solo el texto del item, sacando la cola de qty/precio/monto
            item = re.split(r"\s+[\d,]+\s+\$", line)[0]
            item = re.sub(r"\s+\$[\d,]+\.\d+.*$", "", item).strip()
            if item:
                items.append(item)
        if items:
            result.descripcion = "; ".join(items)

    # Total: "Amount due $68.25" (ambas variantes lo traen)
    m = re.search(r"Amount due\s+\$([\d,]+\.\d{2})", text)
    if not m:
        m = re.search(r"(?:^|\n)Total \$([\d,]+\.\d{2})(?:\s|$)", text)
    if m:
        result.total = _money(m.group(1))

    # IVA: linea "VAT ... $tasa $monto" (solo si el proveedor lo cobra)
    m = re.search(r"VAT[^\n]*\$[\d,]+\.\d{2}\s+\$([\d,]+\.\d{2})", text)
    if m:
        result.iva = _money(m.group(1))
    else:
        result.iva = 0.0

    # Afecto: "Total excluding tax $X"; si no existe, es total - iva
    m = re.search(r"Total excluding tax\s+\$([\d,]+\.\d{2})", text)
    if m:
        result.monto_afecto = _money(m.group(1))
    elif result.total is not None:
        result.monto_afecto = round(result.total - (result.iva or 0.0), 2)

    return result
