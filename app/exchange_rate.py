"""Tipo de cambio USD/CLP para convertir facturas en moneda extranjera.

Usa el "dolar observado", que es el tipo de cambio que el propio SII publica en
https://www.sii.cl/valores_y_fechas/dolar/ y el que corresponde usar para operaciones
en moneda extranjera (dolar observado vigente a la fecha de emision de la factura).
Ese mismo valor lo entrega la API publica de mindicador.cl (viene del Banco Central),
en JSON, por eso lo consumimos desde ahi.
"""
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import httpx

BASE_URL = "https://mindicador.cl/api/dolar/{fecha}"


def get_usd_clp(fecha: date, max_retrocesos: int = 5) -> Tuple[Optional[float], Optional[date]]:
    """Devuelve (valor, fecha_del_valor) del dolar observado para una fecha.

    Si la fecha cae en fin de semana/feriado (serie vacia), retrocede dia a dia
    hasta max_retrocesos veces y devuelve la fecha del valor efectivamente usado,
    para dejar trazabilidad de que dia se aplico. Si no encuentra nada, (None, None).
    """
    d = fecha
    for _ in range(max_retrocesos + 1):
        url = BASE_URL.format(fecha=d.strftime("%d-%m-%Y"))
        try:
            resp = httpx.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            serie = data.get("serie") or []
            if serie:
                punto = serie[0]
                valor = float(punto["valor"])
                fecha_valor = _parse_fecha(punto.get("fecha")) or d
                return valor, fecha_valor
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        d -= timedelta(days=1)
    return None, None


def _parse_fecha(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        # mindicador entrega ISO 8601, ej "2026-07-01T04:00:00.000Z"
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None
