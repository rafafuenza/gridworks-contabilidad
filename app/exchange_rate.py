"""Tipo de cambio USD/CLP via la API publica del Banco Central (mindicador.cl)."""
from datetime import date, timedelta
from typing import Optional

import httpx

BASE_URL = "https://mindicador.cl/api/dolar/{fecha}"


def get_usd_clp(fecha: date, max_retrocesos: int = 5) -> Optional[float]:
    """Busca el valor del dolar observado para una fecha. Si cae en fin de semana/feriado
    (serie vacia), retrocede dia a dia hasta max_retrocesos veces."""
    d = fecha
    for _ in range(max_retrocesos + 1):
        url = BASE_URL.format(fecha=d.strftime("%d-%m-%Y"))
        try:
            resp = httpx.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            serie = data.get("serie") or []
            if serie:
                return float(serie[0]["valor"])
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        d -= timedelta(days=1)
    return None
