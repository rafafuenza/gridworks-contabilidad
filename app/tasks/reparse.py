"""Re-aplica los parsers a los PDFs YA guardados en la base, sin volver a Gmail.

Sirve para: (1) corregir facturas que quedaron mal parseadas, y (2) aplicar
parsers nuevos al historico cuando se agrega un proveedor al registry.

**Por defecto solo toca las pendientes.** Una factura aceptada ya se declaro al
SII con los montos que tenia; recalcularselos por detras dejaria la base
diciendo una cosa y la declaracion presentada otra, sin rastro de la diferencia.
Antes se re-parseaba todo y se preservaba `estado`, que respeta la marca pero no
las cifras -- que es justo lo que hay que respetar. Para el caso raro en que de
verdad haya que corregir una ya declarada (un parser que estaba equivocado y hay
que rectificar ante el SII) esta `--todas`, que es explicito a proposito.

La deteccion es por contenido del PDF (registry.parse_invoice), asi que no
necesita el remitente original. El tipo de cambio ya calculado se reutiliza
para no volver a golpear la API por cada factura.

Uso:  python -m app.tasks.reparse [--todas]
"""
import sys

from sqlalchemy import or_

from app.db import SessionLocal, init_db
from app.exchange_rate import get_usd_clp
from app.gmail_sync import _extract_pdf_text
from app.models import Purchase
from app.parsers import registry
from app import mantenedor

ESTADO_ACEPTADA = "aceptada"


def reparse_all(progress: dict = None, solo_pendientes: bool = True) -> dict:
    """progress: dict opcional que se va actualizando en vivo (mismo contrato que
    gmail_sync.sync), para que la barra de la web pueda seguir el avance. El
    total se sabe de entrada porque las facturas ya estan en la base.

    solo_pendientes: deja fuera las ya declaradas al SII (ver el docstring del
    modulo). El boton de la web siempre usa el default."""
    init_db()
    db = SessionLocal()
    mantenedor.ensure_seed(db)
    stats = {"total": 0, "procesados": 0, "revision_manual": 0, "falta_proveedor": 0,
             "sin_pdf": 0, "errores": 0, "tc_consultados": 0, "omitidas_declaradas": 0}

    def avisar():
        if progress is not None:
            progress.update(stats)

    tc_cache = {}
    try:
        query = db.query(Purchase)
        if solo_pendientes:
            # `estado != 'aceptada'` a secas dejaria fuera las filas con estado
            # NULL: en SQL, NULL != 'aceptada' no es verdadero sino NULL. Una
            # pendiente antigua sin estado se saltaria en silencio.
            query = query.filter(or_(Purchase.estado.is_(None),
                                     Purchase.estado != ESTADO_ACEPTADA))
            stats["omitidas_declaradas"] = (
                db.query(Purchase).filter(Purchase.estado == ESTADO_ACEPTADA).count()
            )

        filas = query.order_by(Purchase.id).all()
        stats["total"] = len(filas)
        avisar()
        for p in filas:
            stats["procesados"] += 1
            if not p.pdf_data:
                stats["sin_pdf"] += 1
                avisar()
                continue
            try:
                text = _extract_pdf_text(p.pdf_data)
                parsed = registry.parse_invoice(text, sender_name=p.proveedor or "")
                mantenedor.aplicar(db, parsed)

                # Tipo de cambio: reusar el que ya estaba si aplica; si falta, consultarlo
                tipo_cambio = p.tipo_cambio
                tipo_cambio_fecha = p.tipo_cambio_fecha
                if parsed.moneda == "USD" and parsed.fecha:
                    # (re)consultar si falta el valor O la fecha del TC (backfill)
                    if tipo_cambio is None or tipo_cambio_fecha is None:
                        if parsed.fecha not in tc_cache:
                            tc_cache[parsed.fecha] = get_usd_clp(parsed.fecha)
                            stats["tc_consultados"] += 1
                        tipo_cambio, tipo_cambio_fecha = tc_cache[parsed.fecha]
                else:
                    tipo_cambio, tipo_cambio_fecha = None, None

                p.numero = parsed.numero
                p.fecha = parsed.fecha
                p.periodo = parsed.fecha.strftime("%Y-%m") if parsed.fecha else None
                p.rut_proveedor = parsed.rut_proveedor
                p.proveedor = parsed.proveedor
                p.descripcion = parsed.descripcion
                p.moneda = parsed.moneda
                p.monto_afecto = parsed.monto_afecto
                p.monto_exento = parsed.monto_exento
                p.iva = parsed.iva
                p.total = parsed.total
                p.tipo_cambio = tipo_cambio
                p.tipo_cambio_fecha = tipo_cambio_fecha
                p.revision_manual = parsed.revision_manual
                p.falta_proveedor = parsed.falta_proveedor
                p.notas = parsed.notas
                # no se toca p.estado: si ya estaba 'aceptada' se respeta

                if parsed.revision_manual:
                    stats["revision_manual"] += 1
                if parsed.falta_proveedor:
                    stats["falta_proveedor"] += 1
            except Exception as e:
                stats["errores"] += 1
                print(f"  ! error en id={p.id}: {e}")
            avisar()
        db.commit()
    finally:
        db.close()
    return stats


if __name__ == "__main__":
    todas = "--todas" in sys.argv
    if todas:
        print("Re-parseando TODAS las facturas, incluidas las ya declaradas al SII...")
    else:
        print("Re-parseando las facturas pendientes...")
    s = reparse_all(solo_pendientes=not todas)
    print(
        f"Listo. Total {s['total']}, en revision {s['revision_manual']}, "
        f"falta proveedor {s['falta_proveedor']}, sin pdf {s['sin_pdf']}, "
        f"errores {s['errores']}, tipos de cambio consultados {s['tc_consultados']}."
    )
    if s["omitidas_declaradas"]:
        print(f"Se omitieron {s['omitidas_declaradas']} ya declaradas (usar --todas para incluirlas).")
