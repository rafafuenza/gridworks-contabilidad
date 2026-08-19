"""Mantenedor de proveedores: la tabla `proveedores` es la fuente de verdad de
los datos fiscales (RUT, tratamiento, etc.). Se puebla manualmente; cuando
aparece un proveedor nuevo sin entrada, la factura entra con RUT generico y
queda marcada `falta_proveedor` para enriquecer despues (buscar el RUT en la
nomina IVA digital del SII y agregarlo aca).
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Proveedor
from app.parsers.base import ParsedInvoice

RUT_GENERICO_EXTRANJERO = "55.555.555-5"  # RUT generico SII para extranjero sin registro

# Datos iniciales conocidos/verificados. Se insertan si no existen (idempotente).
SEED = [
    dict(clave="anthropic", nombre="ANTHROPIC, PBC", rut="59.250.690-4", pais="US",
         moneda_default="USD", tratamiento="afecto", en_nomina_iva_digital=True,
         fuente_rut="Nomina SII IVA digital"),
    dict(clave="aws", nombre="Amazon Web Services, Inc.", rut="59.292.930-9", pais="US",
         moneda_default="USD", tratamiento="afecto", en_nomina_iva_digital=True,
         fuente_rut="Nomina SII IVA digital (verificado jul-2026)",
         notas="IVA 19% recuperable via autodeclaracion (empresa con RUT)."),
    dict(clave="railway", nombre="Railway Corporation", rut=RUT_GENERICO_EXTRANJERO, pais="US",
         moneda_default="USD", tratamiento="afecto", en_nomina_iva_digital=False,
         fuente_rut="Verificado: no aparece en nomina IVA digital (jul-2026)"),
    dict(clave="hetzner", nombre="Hetzner Online GmbH", rut=RUT_GENERICO_EXTRANJERO, pais="DE",
         moneda_default="USD", tratamiento="afecto", en_nomina_iva_digital=False,
         fuente_rut="Verificado: no aparece en nomina IVA digital (jul-2026)"),
    dict(clave="nic-chile", nombre="NIC Chile", rut="60.910.000-1", pais="CL",
         moneda_default="CLP", tratamiento="exento", en_nomina_iva_digital=False,
         fuente_rut="RUT en el propio DTE"),
    dict(clave="praxedis", nombre="PRAXEDIS SPA", rut="76.188.742-4", pais="CL",
         moneda_default="CLP", tratamiento="afecto", en_nomina_iva_digital=False,
         fuente_rut="RUT en el propio DTE",
         notas="Modulacion de stand en Expo America Digital 2026. IVA 19% recuperable."),
    dict(clave="bio-andes", nombre="BIO ANDES AMERICA DIGITAL LLC", rut=RUT_GENERICO_EXTRANJERO,
         pais="US", moneda_default="USD", tratamiento="afecto", en_nomina_iva_digital=False,
         fuente_rut="Confirmado con el usuario (ago-2026): no tiene RUT chileno",
         notas="LLC de Delaware (EIN 38-4191606), factura el Congreso America Digital "
               "sin IVA chileno. Sin RUT chileno: va como factura de compra DTE 46."),
]


def ensure_seed(db: Session):
    cambios = False
    for s in SEED:
        if not db.query(Proveedor).filter_by(clave=s["clave"]).first():
            db.add(Proveedor(**s))
            cambios = True
    if cambios:
        db.commit()


def get(db: Session, clave: Optional[str]) -> Optional[Proveedor]:
    if not clave:
        return None
    return db.query(Proveedor).filter_by(clave=clave).first()


def aplicar(db: Session, parsed: ParsedInvoice) -> ParsedInvoice:
    """Completa RUT/nombre/moneda desde el mantenedor segun parsed.provider_key.
    Si no hay entrada con RUT (proveedor nuevo) deja el RUT generico y marca
    parsed.falta_proveedor. No re-mapea montos (eso lo hace el parser)."""
    prov = get(db, parsed.provider_key)

    if prov:
        parsed.proveedor = prov.nombre or parsed.proveedor
        if not parsed.rut_proveedor:      # nacionales ya traen RUT del doc; extranjeros no
            parsed.rut_proveedor = prov.rut
        if not parsed.moneda:
            parsed.moneda = prov.moneda_default

    # Si quedamos sin RUT real, es un proveedor por enriquecer
    if not parsed.rut_proveedor:
        parsed.rut_proveedor = RUT_GENERICO_EXTRANJERO
        parsed.falta_proveedor = True
    else:
        parsed.falta_proveedor = (parsed.rut_proveedor == RUT_GENERICO_EXTRANJERO and prov is None)

    return parsed
