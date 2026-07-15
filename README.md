# GridWorks Contabilidad — Libro de Compras

App interna (FastAPI + HTMX) que sincroniza los correos de Gmail con la label
`gridworks-contabilidad`, extrae los datos de cada factura (PDF) y arma el
Libro de Compras, con exportacion a Excel filtrable por mes.

## Como funciona

1. Se conecta a Gmail por IMAP (contraseña de aplicacion, no la clave normal de la cuenta).
2. Busca correos con la label configurada que aun no tengan la sub-label
   `gridworks-contabilidad/procesado` y que tengan adjuntos.
3. De cada correo toma el adjunto de la **Invoice** (no el Receipt de pago).
4. Extrae numero, fecha, RUT/proveedor, montos e IVA con un parser dedicado
   por dominio del remitente (`app/parsers/`); si no hay parser dedicado, usa
   un parser generico best-effort y marca la fila para revision manual.
5. Si la moneda es USD, busca el tipo de cambio del dia via la API publica
   del Banco Central (mindicador.cl) y lo guarda como referencia (los montos
   quedan en USD, no se convierten automaticamente).
6. Guarda todo en Postgres (incluido el PDF original) y marca el correo como
   procesado en Gmail, para no volver a tocarlo.

## Variables de entorno

Ver `.env.example`. Railway inyecta `DATABASE_URL` automaticamente al agregar
el addon de Postgres; el resto hay que configurarlo a mano en el servicio.

## Deploy en Railway

1. **New Project → Deploy from GitHub repo** → elegir `gridworks-contabilidad`.
2. **Add a Database → PostgreSQL** dentro del mismo proyecto (inyecta `DATABASE_URL` solo).
3. En el servicio web, pestaña **Variables**, agregar:
   - `GMAIL_ADDRESS`
   - `GMAIL_APP_PASSWORD`
   - `GMAIL_LABEL` (default `gridworks-contabilidad`)
   - `APP_PASSWORD` (clave para entrar a la web)
   - `SECRET_KEY` (cualquier string largo random)
4. Railway detecta el `Procfile` y corre `uvicorn` solo.
5. **Cron Job**: crear un nuevo servicio en el mismo proyecto, tipo *Cron Job*,
   mismo repo, comando `python -m app.tasks.monthly_cron`, schedule mensual
   (ej. `0 9 1 * *` = dia 1 de cada mes a las 9am). Le tienen que llegar las
   mismas variables de entorno (Railway permite compartir variables entre
   servicios del mismo proyecto).
6. (Opcional) Dominio custom: Settings → Networking → agregar
   `conta.gridworks.cl` y crear el CNAME que indique Railway en el DNS de
   gridworks.cl.

## Uso

- Entrar con la clave (`APP_PASSWORD`).
- Boton **Actualizar ahora**: corre la sincronizacion on-demand.
- Selector de mes + **Descargar Excel**: genera el Libro de Compras del mes
  elegido (o todos).
- **Descargar PDFs**: zip con las facturas originales del mes elegido.
- Filas en amarillo con etiqueta "revisar": el parser no tuvo un dominio
  dedicado o no pudo extraer todos los campos con confianza — revisar a mano.

## Agregar un proveedor nuevo con parser dedicado

Crear `app/parsers/<proveedor>.py` con una funcion `parse(text: str) -> ParsedInvoice`,
y agregar el dominio del remitente en `app/parsers/registry.py`.

## Desarrollo local

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # completar valores
uvicorn app.main:app --reload
```

Sin `DATABASE_URL` configurado usa SQLite local (`local.db`) — util para probar.
