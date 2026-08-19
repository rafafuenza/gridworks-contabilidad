"""Parser para BIO ANDES AMERICA DIGITAL LLC (Delaware, US), factura en USD.

Es quien cobra la participacion en el Congreso America Digital. Factura como
proveedor extranjero: no hay IVA chileno en el documento, asi que el neto es el
total y el IVA va en 0, igual que Railway y Hetzner.

Cuidado con el RUT: el documento **si** trae un RUT chileno, pero es el de
GridWorks como receptor ("RUT:78.453.294-1"), no el del emisor -- el emisor se
identifica con su EIN de EE.UU. Por eso este parser nunca escribe
`rut_proveedor`: BIO ANDES no tiene RUT chileno (confirmado ago-2026), asi que
el mantenedor le pone el generico de extranjero y la compra va como factura de
compra DTE 46.
"""
import re

from dateutil import parser as dateparser

from app.parsers.base import ParsedInvoice, parse_monto

FINGERPRINT = "BIO ANDES AMERICA DIGITAL"


def parse(text: str) -> ParsedInvoice:
    result = ParsedInvoice(moneda="USD", monto_exento=0.0, iva=0.0)

    m = re.search(r"Invoice\s+Number\s*:?\s*([A-Z0-9\-]+)", text, re.I)
    if m:
        result.numero = m.group(1)

    # "Invoice Date: August 7, 2026". Se ancla a la etiqueta a proposito: el
    # documento tambien trae "Payment Due: August 20, 2026", que es el
    # vencimiento y no la fecha de emision que va al libro.
    m = re.search(r"Invoice\s+Date\s*:?\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})", text, re.I)
    if m:
        try:
            result.fecha = dateparser.parse(m.group(1)).date()
        except (ValueError, OverflowError):
            pass

    # "Total: $6,800.00", con "Amount Due (USD): $6,800.00" como respaldo
    m = re.search(r"(?:^|\n)\s*Total\s*:?\s*\$?\s*([\d.,]+)", text, re.I)
    if not m:
        m = re.search(r"Amount\s+Due\s*\(USD\)\s*:?\s*\$?\s*([\d.,]+)", text, re.I)
    if m:
        result.total = parse_monto(m.group(1))

    # Extranjero sin IVA chileno: el neto es el total (mismo criterio que Hetzner)
    result.monto_afecto = result.total

    # Primera linea del detalle, sin las columnas de cantidad y montos
    m = re.search(r"Items\s+Quantity\s+Price\s+Amount\n(.+)", text)
    if m:
        result.descripcion = re.sub(r"\s+\d+(\s+\$[\d.,]+)+\s*$", "", m.group(1)).strip()

    return result
