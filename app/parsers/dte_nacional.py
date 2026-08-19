"""Parser compartido para el DTE nacional impreso (factura electronica del SII).

Es el analogo nacional de `stripe_invoice.py`: no es de un proveedor, es de un
*formato*. Todos los emisores chilenos imprimen la misma estructura porque la
fija el SII -- recuadro rojo con R.U.T. y folio, "Fecha Emision", y el pie con
MONTO NETO / I.V.A. / TOTAL -- asi que un proveedor nacional nuevo normalmente
se resuelve con una entrada en PROVIDERS que reusa este parser, sin escribir
codigo.

Dos detalles del texto que saca pdfplumber y que hay que respetar:

- El digito verificador viene separado del cuerpo ("76.188.742- 4"), asi que el
  RUT se normaliza al armarlo, no se toma tal cual.
- El documento trae la copia CEDIBLE, asi que **todo el texto aparece dos
  veces**. Por eso todas las regex usan `search` (primera ocurrencia) y nunca
  `findall`: la segunda copia es la misma factura, no una segunda linea.

El emisor se toma del **primer** R.U.T. del documento. En el DTE el bloque del
emisor va siempre arriba y el del receptor ("SEÑOR(ES):") mas abajo, asi que el
orden alcanza para distinguirlos sin depender de la etiqueta.
"""
import re
from datetime import date
from typing import Optional

from app.parsers.base import ParsedInvoice, parse_monto

FINGERPRINTS = ("R.U.T.", "www.sii.cl")

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def looks_like_dte(text: str) -> bool:
    return all(fp in text for fp in FINGERPRINTS)


def _monto(text: str, patron: str) -> Optional[float]:
    m = re.search(patron, text, re.I)
    return parse_monto(m.group(1)) if m else None


def parse(text: str) -> ParsedInvoice:
    result = ParsedInvoice(moneda="CLP")

    # RUT del emisor: "R.U.T.:76.188.742- 4" (con o sin espacios alrededor del guion)
    m = re.search(r"R\.U\.T\.\s*:?\s*([\d][\d.]*)\s*-\s*([\dkK])", text)
    if m:
        result.rut_proveedor = f"{m.group(1)}-{m.group(2).upper()}"
        # Razon social: la linea siguiente al RUT del emisor
        resto = text[m.end():].split("\n")
        for linea in resto[1:3]:
            linea = linea.strip()
            if linea and not linea.upper().startswith(("GIRO", "R.U.T")):
                result.proveedor = linea
                break

    # Folio: "N°:2244" / "Nº 2244"
    m = re.search(r"N[°ºo]\s*:?\s*(\d{1,10})", text)
    if m:
        result.numero = m.group(1)

    # "Fecha Emision:18 de Agosto del 2026" (tambien "de 2026")
    m = re.search(
        r"Fecha\s*Emisi[oó]n\s*:?\s*(\d{1,2})\s*de\s*([A-Za-zÁÉÍÓÚáéíóú]+)\s*del?\s*(\d{4})",
        text, re.I,
    )
    if m:
        mes = MESES.get(m.group(2).lower())
        if mes:
            try:
                result.fecha = date(int(m.group(3)), mes, int(m.group(1)))
            except ValueError:
                pass

    result.monto_afecto = _monto(text, r"MONTO\s+NETO\s*\$?\s*([\d.,]+)")
    result.monto_exento = _monto(text, r"MONTO\s+EXENTO\s*\$?\s*([\d.,]+)")
    # El "19%" va entre la etiqueta y el monto, por eso el grupo opcional
    result.iva = _monto(text, r"I\.?V\.?A\.?\s*(?:\d+\s*%)?\s*\$?\s*([\d.,]+)")
    result.total = _monto(text, r"(?:^|\n)\s*TOTAL\s*\$?\s*([\d.,]+)")

    # Una factura afecta no imprime la linea de exento; para el libro es 0, no nulo
    if result.monto_exento is None and result.monto_afecto is not None:
        result.monto_exento = 0.0
    if result.iva is None and result.monto_afecto is not None:
        result.iva = 0.0

    # Impuesto adicional: no tiene columna propia en el libro. Si viene con monto
    # hay que mirarlo a mano antes de declarar, porque el total no cuadraria.
    adicional = _monto(text, r"IMPUESTO\s+ADICIONAL\s*\$?\s*([\d.,]+)")
    if adicional:
        result.revision_manual = True
        result.notas = f"Trae impuesto adicional por {adicional:,.0f}: revisar como declararlo."

    return result
