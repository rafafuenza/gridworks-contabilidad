"""Regresion de los parsers sobre facturas reales.

Las fixtures son el texto que pdfplumber saca de los PDF de verdad, no texto
inventado: lo que rompe los parsers son las rarezas de la extraccion (el digito
verificador separado del RUT, la copia CEDIBLE que duplica el documento entero,
las dos convenciones de separador decimal en un mismo PDF), y ninguna de esas
aparece si uno se escribe el caso a mano.

Para agregar una fixture: dejar el PDF en facturas_pendientes/, correr
`python -m app.tasks.diagnosticar facturas_pendientes` y copiar el .txt que deja
al lado a tests/fixtures/.
"""
from pathlib import Path

import pytest

from app import mantenedor
from app.parsers import registry
from app.parsers.base import parse_monto

FIXTURES = Path(__file__).parent / "fixtures"


def texto(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


# --- Conversion de montos -------------------------------------------------

@pytest.mark.parametrize("crudo, esperado", [
    ("$6,800.00", 6800.0),      # anglosajon: la coma es de miles
    ("6.800,00", 6800.0),       # chileno con decimales: el punto es de miles
    ("2.273.680", 2273680.0),   # CLP sin decimales
    ("431.999", 431999.0),
    ("6.80", 6.8),              # un solo separador con 2 decimales: es decimal
    ("1,234,567.89", 1234567.89),
    ("$ 0", 0.0),
    ("-1.500,50", -1500.5),
])
def test_parse_monto_respeta_la_convencion(crudo, esperado):
    assert parse_monto(crudo) == esperado


def test_parse_monto_devuelve_none_en_vez_de_adivinar():
    """Sin numero no se inventa un 0: None hace que el registry marque
    revision_manual, y un 0 se declararia como si fuera un monto real."""
    assert parse_monto(None) is None
    assert parse_monto("") is None
    assert parse_monto("N/A") is None


def test_regresion_del_monto_leido_como_decimal():
    """El bug que motivo todo esto: "$6,800.00" se leia como 6.8."""
    assert parse_monto("$6,800.00") == 6800.0


# --- PRAXEDIS SPA: DTE nacional afecto, en CLP ----------------------------

def test_praxedis_parsea_completo():
    inv = registry.parse_invoice(texto("praxedis_2244.txt"))

    assert inv.provider_key == "praxedis"
    assert inv.numero == "2244"
    assert inv.fecha.isoformat() == "2026-08-18"
    assert inv.moneda == "CLP"
    assert inv.monto_afecto == 2273680.0
    assert inv.iva == 431999.0
    assert inv.monto_exento == 0.0
    assert inv.total == 2705679.0
    assert inv.revision_manual is False


def test_praxedis_toma_el_rut_del_emisor_no_el_de_gridworks():
    """El DTE trae los dos RUT; el receptor (78.453.294-1) es GridWorks."""
    inv = registry.parse_invoice(texto("praxedis_2244.txt"))
    assert inv.rut_proveedor == "76.188.742-4"


def test_praxedis_cuadra_neto_mas_iva_con_el_total():
    inv = registry.parse_invoice(texto("praxedis_2244.txt"))
    assert inv.monto_afecto + inv.monto_exento + inv.iva == inv.total


# --- BIO ANDES AMERICA DIGITAL LLC: extranjero en USD ---------------------

def test_bio_andes_parsea_completo():
    inv = registry.parse_invoice(texto("bio_andes_1404.txt"))

    assert inv.provider_key == "bio-andes"
    assert inv.numero == "1404"
    assert inv.moneda == "USD"
    assert inv.total == 6800.0
    assert inv.monto_afecto == 6800.0
    assert inv.iva == 0.0
    assert inv.revision_manual is False


def test_bio_andes_usa_la_fecha_de_emision_no_el_vencimiento():
    """El PDF trae "Invoice Date: August 7" y "Payment Due: August 20".
    Al libro va la de emision, y ademas caen en meses distintos si el
    vencimiento cruza fin de mes."""
    inv = registry.parse_invoice(texto("bio_andes_1404.txt"))
    assert inv.fecha.isoformat() == "2026-08-07"


def test_bio_andes_no_toma_el_rut_de_gridworks_como_proveedor():
    """El PDF trae "RUT:78.453.294-1", que es el receptor. El emisor es una LLC
    de Delaware y no tiene RUT en el documento."""
    inv = registry.parse_invoice(texto("bio_andes_1404.txt"))
    assert inv.rut_proveedor != "78.453.294-1"
    assert inv.rut_proveedor is None


# --- Cruce con el mantenedor ---------------------------------------------

def test_praxedis_queda_resuelto_contra_el_mantenedor(db):
    mantenedor.ensure_seed(db)
    inv = mantenedor.aplicar(db, registry.parse_invoice(texto("praxedis_2244.txt")))

    assert inv.rut_proveedor == "76.188.742-4"
    assert inv.falta_proveedor is False


def test_bio_andes_queda_resuelto_con_el_rut_generico(db):
    """No tiene RUT chileno (confirmado ago-2026), asi que le toca el generico de
    extranjero y NO debe quedar marcada `falta_proveedor`: esa marca significa
    "proveedor por averiguar", y aca ya se averiguo."""
    mantenedor.ensure_seed(db)
    inv = mantenedor.aplicar(db, registry.parse_invoice(texto("bio_andes_1404.txt")))

    assert inv.rut_proveedor == mantenedor.RUT_GENERICO_EXTRANJERO
    assert inv.falta_proveedor is False


# --- Formato de montos en la web -----------------------------------------

@pytest.mark.parametrize("valor, moneda, esperado", [
    (2273680.0, "CLP", "2.273.680"),
    (431999.0, "CLP", "431.999"),
    (2705679.0, "CLP", "2.705.679"),
    (999.0, "CLP", "999"),
    (6800.0, "USD", "6,800.00"),
    (6.8, "USD", "6.80"),
    (1234567.89, "USD", "1,234,567.89"),
])
def test_formato_monto_por_moneda(valor, moneda, esperado):
    from app.main import formato_monto
    assert formato_monto(valor, moneda) == esperado


def test_los_pesos_no_muestran_decimales():
    """El SII declara pesos enteros: mostrar centavos sugiere una precision que
    el documento no tiene."""
    from app.main import formato_monto
    assert formato_monto(2273680.4, "CLP") == "2.273.680"
    assert "," not in formato_monto(2273680.0, "CLP")  # la coma no es separador de miles en CLP


def test_formato_monto_sin_valor_ni_moneda():
    from app.main import formato_monto
    assert formato_monto(None) == "-"
    assert formato_monto(None, "CLP") == "-"
    # Sin moneda (totales del tablero) se usa la forma sin decimales
    assert formato_monto(2273680.0) == "2.273.680"
