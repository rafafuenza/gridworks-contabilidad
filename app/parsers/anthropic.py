import re
from dateutil import parser as dateparser

from app.parsers.base import ParsedInvoice

RUT_ANTHROPIC = "59.250.690-4"  # Nomina SII contribuyentes IVA digital: https://www.sii.cl/vat/dwn_esp.html


def parse(text: str) -> ParsedInvoice:
    result = ParsedInvoice(proveedor="ANTHROPIC, PBC", rut_proveedor=RUT_ANTHROPIC, moneda="USD")

    m = re.search(r"Invoice number\s+([A-Z0-9]+)\s+(\d+)", text)
    if m:
        result.numero = f"{m.group(1)}-{m.group(2)}"

    m = re.search(r"Date of issue\s+([A-Za-z]+ \d{1,2},\s*\d{4})", text)
    if m:
        try:
            result.fecha = dateparser.parse(m.group(1)).date()
        except (ValueError, OverflowError):
            pass

    # Descripcion: lineas de items entre el header de la tabla y "Subtotal",
    # quitando qty/precio/tax/monto para dejar solo el texto del item
    m = re.search(r"Description Qty Unit price Tax Amount\n(.*?)\nSubtotal", text, re.S)
    if m:
        lines = [l.strip() for l in m.group(1).splitlines() if "$" in l]
        cleaned = []
        for line in lines:
            item_m = re.match(r"^(.*?)\s+\d+\s+\$[\d,]+\.\d{2}\s+\d+%\s+\$[\d,]+\.\d{2}$", line)
            cleaned.append(item_m.group(1).strip() if item_m else line)
        result.descripcion = "; ".join(cleaned) if cleaned else None

    m = re.search(r"Total excluding tax\s+\$([\d,]+\.\d{2})", text)
    if m:
        result.monto_afecto = float(m.group(1).replace(",", ""))

    m = re.search(r"VAT[^\n]*\$[\d,]+\.\d{2}\s+\$([\d,]+\.\d{2})", text)
    if m:
        result.iva = float(m.group(1).replace(",", ""))

    # "Total $23.80" como linea propia (no "Total excluding tax" ni "Amount due")
    m = re.search(r"(?:^|\n)Total \$([\d,]+\.\d{2})(?:\n|$)", text)
    if m:
        result.total = float(m.group(1).replace(",", ""))

    result.monto_exento = 0.0

    if result.numero is None or result.total is None:
        result.revision_manual = True
        result.notas = "No se pudieron extraer todos los campos automaticamente."

    return result
