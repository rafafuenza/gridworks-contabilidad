# Contexto del proyecto (para retomar en Claude Code)

Este proyecto se empezo a construir en una sesion de Claude Cowork con Rafael.
Este archivo resume las decisiones tomadas para que se pueda seguir trabajando
sin perder contexto.

## Que es esto

App interna para GridWorks que arma el **Libro de Compras** automaticamente a
partir de correos de Gmail. Mas adelante se va a sumar **Libro de Ventas** e
**integracion con el SII**.

## Arquitectura (decidida y confirmada con el usuario)

- **Backend**: FastAPI + HTML renderizado por el servidor (Jinja2) + HTMX para
  interactividad. Se eligio esto en vez de React+API separada por ser una
  herramienta interna de un solo usuario: menos piezas, un solo deploy.
- **DB**: Postgres (Railway managed addon). El PDF original de cada factura se
  guarda como `bytea` en la tabla `purchases` (no hay volumen de archivos
  separado).
- **Auth**: clave unica compartida (`APP_PASSWORD`), cookie firmada con
  `itsdangerous`. No hay multi-usuario ni OAuth (se evaluo Google OAuth y se
  descarto por ahora, por simplicidad).
- **Hosting**: Railway, conectado directo al repo de GitHub
  (`rafafuenza/gridworks-contabilidad`, privado). Deploy automatico al hacer
  push a `main`.
- **Cron**: un segundo servicio Railway (tipo Cron Job) corre
  `python -m app.tasks.monthly_cron` mensualmente. Ademas hay un boton
  "Actualizar ahora" en la UI para correrlo on-demand.
- **Dominio**: eventualmente `conta.gridworks.cl` (dominio ya existe, falta
  agregar el CNAME cuando se despliegue).

## Flujo de sincronizacion (IMAP, no Gmail API)

Se probaron 3 caminos para bajar adjuntos de Gmail: (1) el conector Gmail
oficial de Cowork -> no tiene endpoint de descarga de adjuntos, solo metadata;
(2) automatizar el navegador -> Chrome bloqueaba las descargas multiples
automaticas, no confiable; (3) **IMAP con contrasena de aplicacion** -> es lo
que se uso, funciona bien. Por eso `app/gmail_sync.py` usa `imaplib` directo,
no la API REST de Gmail.

Detalles del sync:
- Busca correos con `label:gridworks-contabilidad has:attachment` que **no**
  tengan la sub-label `gridworks-contabilidad/procesado` (asi nunca se
  procesa dos veces; la sub-label se crea sola en Gmail al aplicarla).
- De cada correo toma **solo el adjunto de la Invoice**, no el Receipt de
  pago (el Receipt es solo comprobante, la Invoice es el documento contable).
- El parser de cada PDF esta en `app/parsers/`, registrado por dominio del
  remitente en `app/parsers/registry.py`. Hoy solo existe un parser dedicado:
  `anthropic.py`. Todo lo demas cae al parser generico
  (`generic.py`, best-effort) y queda marcado `revision_manual=True` (se ve
  en amarillo en la UI).
- **RUT de Anthropic en Chile**: `59.250.690-4` -- se saco de la nomina oficial
  del SII de contribuyentes extranjeros inscritos en IVA digital
  (https://www.sii.cl/vat/dwn_esp.html). Para proveedores sin parser dedicado
  se usa el RUT generico `55.555.555-5` (factura de compra DTE 46).
- **Moneda**: si el texto del PDF menciona "USD" se guarda `moneda=USD` y
  **los montos quedan en USD, no se convierten a CLP** (decision explicita
  del usuario). Igual se busca el tipo de cambio del dia via la API publica
  `mindicador.cl` y se guarda en `tipo_cambio` como referencia.
- Un bug ya resuelto: pdfplumber devuelve caracteres `\x00` en vez de
  espacios en los PDFs de Anthropic (fuente custom) -- se normalizan en
  `_extract_pdf_text` antes de aplicar cualquier regex. Si se agregan
  parsers nuevos y las regex no matchean, revisar esto primero con
  `repr(text)`.

## Estado actual (al momento del handoff)

- Repo scaffolded y **pusheado a GitHub**, probado localmente end-to-end
  (dashboard, export a Excel con formulas de totales, export de PDFs en zip,
  parser de Anthropic) con SQLite y 2 facturas de prueba reales.
- **Railway: pendiente que el usuario lo despliegue** (instrucciones en
  README.md -- crear servicio web + Postgres + variables de entorno + Cron
  Job). Verificar con el usuario si ya lo hizo.
- Solo se probo con 2 correos de prueba. **Falta correr el sync completo**
  contra los ~90 correos reales que tiene la label `gridworks-contabilidad`
  y revisar cuantos caen en "revision manual" (proveedores sin parser
  dedicado, ej. Hetzner que tambien aparecia en la label).

## Pendientes / proximos pasos sugeridos

1. Confirmar deploy en Railway y correr el primer sync real.
2. Agregar parsers dedicados para otros proveedores frecuentes (ej. Hetzner,
   que emite un formato de invoice distinto).
3. Libro de Ventas (mismo patron: modelo `Sale`, sync propio si aplica,
   export a Excel).
4. Integracion con el SII (aun no definida en detalle -- pendiente de
   conversacion con el usuario sobre alcance: ¿carga de facturas de venta
   electronicas? ¿consulta de estado? etc.)
5. Dominio custom `conta.gridworks.cl` en Railway.
6. Revisar si vale la pena mover el shared-secret auth a algo mas robusto
   (Google OAuth) si mas de una persona va a usar la app.

## Variables de entorno (ver `.env.example`)

`DATABASE_URL` (la inyecta Railway), `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
`GMAIL_LABEL`, `APP_PASSWORD`, `SECRET_KEY`. Ninguna esta en el repo, todas
se configuran como secretos en Railway (o en `.env` local, gitignored).
