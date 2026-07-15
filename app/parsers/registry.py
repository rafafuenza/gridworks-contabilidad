"""Registro de proveedores: detecta a que proveedor pertenece un PDF por su
contenido (no solo por el dominio del remitente, asi tambien sirve para
re-parsear PDFs ya guardados) y ejecuta su parser.

El RUT y el tratamiento NO viven aca: los aporta el mantenedor de proveedores
(app/mantenedor.py) cruzando por `provider_key`. Aca solo esta la logica de
deteccion y extraccion. Agregar un proveedor = una entrada en PROVIDERS (si usa
la plantilla Stripe, reusa stripe_invoice.parse) + su fila en el mantenedor.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from app.parsers.base import ParsedInvoice
from app.parsers import stripe_invoice, aws, hetzner, nic, generic


@dataclass
class ProviderConfig:
    key: str                             # slug; cruza con el mantenedor
    display_name: Optional[str]          # nombre de respaldo (el mantenedor manda)
    detect: Callable[[str], bool]        # True si el PDF es de este proveedor
    parser: Callable[[str], ParsedInvoice]
    nota: Optional[str] = None           # nota informativa que se adjunta siempre


# Orden importa: proveedores con config propia primero; el Stripe generico
# (SaaS aun sin entrada) y el fallback generico van al final.
PROVIDERS = [
    ProviderConfig(
        key="anthropic",
        display_name="ANTHROPIC, PBC",
        detect=lambda t: "Anthropic" in t and stripe_invoice.looks_like_stripe(t),
        parser=stripe_invoice.parse,
    ),
    ProviderConfig(
        key="railway",
        display_name="Railway Corporation",
        detect=lambda t: "Railway" in t and stripe_invoice.looks_like_stripe(t),
        parser=stripe_invoice.parse,
    ),
    ProviderConfig(
        key="aws",
        display_name="Amazon Web Services",
        detect=lambda t: aws.FINGERPRINT in t,
        parser=aws.parse,
        nota="AWS cobra IVA 19% (recuperable via autodeclaracion, confirmar con contador).",
    ),
    ProviderConfig(
        key="hetzner",
        display_name="Hetzner Online GmbH",
        detect=lambda t: hetzner.FINGERPRINT in t,
        parser=hetzner.parse,
    ),
    ProviderConfig(
        key="nic-chile",
        display_name="NIC Chile",
        detect=lambda t: nic.FINGERPRINT in t,
        parser=nic.parse,
    ),
    # SaaS con plantilla Stripe pero sin config propia todavia: se parsea igual,
    # pero queda marcado para revision para que se le agregue una entrada arriba.
    ProviderConfig(
        key="stripe_desconocido",
        display_name=None,
        detect=stripe_invoice.looks_like_stripe,
        parser=stripe_invoice.parse,
        nota="Factura Stripe de proveedor sin config dedicada: verificar y agregar al registry.",
    ),
]


def detect_provider(text: str) -> Optional[ProviderConfig]:
    for cfg in PROVIDERS:
        try:
            if cfg.detect(text):
                return cfg
        except Exception:
            continue
    return None


def parse_invoice(text: str, sender_name: str = "", sender_domain: str = "") -> ParsedInvoice:
    """Detecta el proveedor y ejecuta su parser. Deja `provider_key` para que el
    mantenedor complete RUT/tratamiento despues. NO asigna RUT aca."""
    cfg = detect_provider(text)
    if cfg:
        inv = cfg.parser(text)
        inv.provider_key = cfg.key
        inv.proveedor = cfg.display_name or inv.proveedor or sender_name or sender_domain or "Desconocido"
        if cfg.key == "stripe_desconocido":
            inv.revision_manual = True
        if cfg.nota:
            _add_nota(inv, cfg.nota)
    else:
        inv = generic.parse(text, sender_name=sender_name, sender_domain=sender_domain)
        inv.provider_key = None

    _validate(inv)
    return inv


def _add_nota(inv: ParsedInvoice, nota: str):
    inv.notas = f"{inv.notas} | {nota}" if inv.notas else nota


def _validate(inv: ParsedInvoice):
    """Chequeo de sanidad: campos minimos y que afecto+exento+iva cuadre con total."""
    problemas = []
    if not inv.numero:
        problemas.append("sin numero")
    if inv.total is None:
        problemas.append("sin total")
    else:
        suma = (inv.monto_afecto or 0) + (inv.monto_exento or 0) + (inv.iva or 0)
        tol = max(0.05, abs(inv.total) * 0.01)
        if abs(suma - inv.total) > tol:
            problemas.append(f"afecto+exento+iva ({suma:g}) != total ({inv.total:g})")

    if problemas:
        inv.revision_manual = True
        _add_nota(inv, "Revisar: " + "; ".join(problemas))
