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
- **Auth**: multi-usuario con correo y clave propios (ver seccion dedicada
  mas abajo). Cookie firmada con `itsdangerous`. No hay OAuth (se evaluo
  Google OAuth y se descarto por ahora, por simplicidad).
- **Hosting**: Railway, conectado directo al repo de GitHub
  (`rafafuenza/gridworks-contabilidad`, privado). Deploy automatico al hacer
  push a `main`.
- **Cron**: un segundo servicio Railway (tipo Cron Job) corre
  `python -m app.tasks.monthly_cron` mensualmente. Ademas hay un boton
  "Actualizar ahora" en la UI para correrlo on-demand.
- **Dominio**: eventualmente `conta.gridworks.cl` (dominio ya existe, falta
  agregar el CNAME cuando se despliegue).

## Autenticacion (multi-usuario, correo y clave) — decisiones a preservar

Se reemplazo la clave unica compartida (`APP_PASSWORD`) por cuentas
individuales (tabla `usuarios`: `app/models.py`, `app/usuarios.py`,
`app/auth.py`, `app/security.py`). Cada persona entra con su correo y su
propia clave; se invita desde `/usuarios`, se cambia la clave propia desde
`/cuenta`, y hay recuperacion por correo (`/olvide-clave`,
`/restablecer/{token}`). El detalle de uso esta en el README, seccion
"Acceso". Lo que sigue son las decisiones de diseño que **no** son obvias
leyendo el codigo, para que nadie las "arregle" sin saber por que estan asi:

- **No hay tabla de sesiones, a proposito.** La cookie de sesion es un token
  firmado (`itsdangerous`) que lleva `{"uid": ..., "v": ...}`, donde `v` es
  `Usuario.token_version`. Subir esa version (al cambiar la clave o al
  desactivar la cuenta, ver `usuarios.cambiar_clave` / `usuarios.desactivar`)
  invalida de un solo golpe todas las cookies y enlaces de recuperacion
  vigentes de esa cuenta, sin tener que llevar registro de sesiones activas.
- **Riesgo con restauraciones de backup**: si la base se restaura alguna vez
  desde un backup tomado *antes* de un cambio de clave, `token_version`
  vuelve atras y una cookie robada que ya deberia estar muerta podria volver
  a servir por lo que le quede de sus 30 dias de vida. Despues de cualquier
  restauracion de backup, subir a mano el `token_version` de todos los
  usuarios, o rotar `SECRET_KEY` (esto ultimo invalida todas las sesiones de
  todos modos).
- **No hay roles.** Todas las cuentas tienen los mismos permisos: cualquiera
  que pueda entrar puede invitar y dar de baja a otras cuentas desde
  `/usuarios`, excepto la suya propia (no se puede dar de baja a si mismo,
  sin excepcion).
- **El correo de recuperacion se manda DESPUES de responder** (via
  `BackgroundTasks`, en `app/main.py`), pero el turno para enviarlo
  (`usuarios.reclamar_envio_reset`) se reclama de forma sincrona, dentro del
  request, antes de responder. Si el correo se mandara en linea, una
  direccion con cuenta tardaria lo que tarda el SMTP y una sin cuenta
  contestaria al instante — mismo mensaje, pero el tiempo delataria que
  direcciones tienen cuenta.
- **El camino de cuenta bloqueada responde rapido y sin hashear** (ver
  `usuarios.autenticar`). Eso revela que la cuenta existe una vez que ya lleva
  5 intentos fallidos; es una fuga aceptada a proposito, porque hashear ahi le
  regalaria a quien ataca ~650 ms y 64 MB de trabajo por cada request que ya
  esta bloqueado — peor que la fuga.
- **Hasheo de claves con `hashlib.scrypt`** (estandar de Python), no
  bcrypt/argon2, para no sumar una dependencia con binarios compilados.
  `n=2**16` (64 MB por hasheo), no `2**17` (el piso de OWASP, 128 MB), porque
  esto corre en un contenedor chico de Railway. Como mucho 2 hasheos corren en
  paralelo, acotado por un semaforo en `app/security.py`.
- **`sembrar_admin_inicial` tiene una carrera de un solo uso, aceptada**:
  revisa `count() == 0` y despues inserta, sin transaccion que cubra las dos
  cosas, asi que con varios workers en el primer arranque podria correr dos
  veces en paralelo. Se acepta porque la app corre como instancia unica, el
  indice unico de `email` hace que la segunda insercion falle ruidosamente en
  vez de duplicar silenciosamente, y la ventana solo existe una vez en la
  vida del deploy.
- **El log de arranque usa el logger de uvicorn (`logging.getLogger("uvicorn.error")`),
  no uno nuevo**: un logger propio hereda del root logger, que no tiene
  handler configurado y descarta todo lo que sea menos severo que WARNING —
  los avisos de arranque (`BASE_URL para los enlaces de correo`, `Cuenta
  inicial creada`) no habrian salido en el log de Railway con un logger nuevo.

## Mantenedor de proveedores y flujo de aceptacion (importante)

- **Mantenedor** (`app/mantenedor.py` + tabla `proveedores`): es la **fuente de
  verdad** de los datos fiscales de cada empresa (RUT, tratamiento afecto/exento,
  moneda, si esta en la nomina IVA digital, fuente del RUT). Los parsers ya NO
  traen el RUT hardcodeado; lo aporta el mantenedor cruzando por `provider_key`.
  Se siembra solo (`ensure_seed`) con: anthropic (59.250.690-4), aws
  (59.292.930-9, verificado en nomina), railway y hetzner (55.555.555-5 generico,
  verificado que NO estan en la nomina), nic-chile (60.910.000-1, exento),
  praxedis (76.188.742-4, afecto, RUT del propio DTE) y bio-andes
  (55.555.555-5 generico; confirmado ago-2026 que no tiene RUT chileno).
- **`ensure_seed` solo inserta, nunca actualiza.** Si se corrige el RUT o el
  tratamiento de una fila del `SEED` que ya existe en la base, el cambio **no**
  llega: hay que editar la fila a mano (o borrarla y volver a sembrar) y despues
  correr `reparse`. En una base nueva no se nota, por eso es facil creer que el
  seed es la fuente de verdad en runtime -- no lo es, la tabla lo es.
- **El RUT generico en el seed significa "ya lo averiguamos y no tiene".** No es
  un placeholder: railway, hetzner y bio-andes lo tienen porque se verifico que
  no estan en la nomina IVA digital, y va acompañado del `fuente_rut` que dice
  quien y cuando lo confirmo. Para el caso contrario -- proveedor que aparecio y
  todavia nadie lo miro -- lo correcto es dejar la fila con `rut=None`: `aplicar`
  le pone el generico igual pero **marca la factura `falta_proveedor`** (etiqueta
  naranja en la UI), que es justo la distincion entre "sin RUT" y "sin revisar".
  Escribir el generico a mano en una fila sin verificar apaga esa alarma y la
  factura pasa a verse resuelta sin estarlo.
- **Enriquecimiento manual (lo hace el asistente, no un cron):** si aparece un
  proveedor que no esta en el mantenedor, la factura entra igual con RUT generico
  y queda marcada `falta_proveedor`. El enriquecimiento (buscar el RUT en la
  nomina IVA digital del SII -- https://www.sii.cl/vat/dwn_esp.html -- y agregar
  la fila al mantenedor) se hace a mano en una sesion de trabajo, y despues
  `reparse` rellena el RUT. Por decision del usuario esto es manual por ahora.
- **Flujo de labels / estado:**
  - Se procesa todo lo que este en `label:gridworks-contabilidad` (los ya
    guardados se saltan por Message-ID). El sync **ya no** aplica ninguna
    sub-label automaticamente.
  - Cada factura nace `estado="pendiente"`. En la web hay boton **Aceptar** por
    fila y **Aceptar (lote)** por mes (el lote omite las que estan en revision
    manual).
  - Al aceptar: `estado="aceptada"` y en Gmail se **mueve** el correo (se quita
    `gridworks-contabilidad` y se agrega `gridworks-contabilidad-sii-compras-declaradas`).
    Ver `gmail_sync.mover_a_declaradas`. El label de declaradas es configurable
    (`GMAIL_DECLARED_LABEL`).

## Flujo de sincronizacion (IMAP, no Gmail API)

Se probaron 3 caminos para bajar adjuntos de Gmail: (1) el conector Gmail
oficial de Cowork -> no tiene endpoint de descarga de adjuntos, solo metadata;
(2) automatizar el navegador -> Chrome bloqueaba las descargas multiples
automaticas, no confiable; (3) **IMAP con contrasena de aplicacion** -> es lo
que se uso, funciona bien. Por eso `app/gmail_sync.py` usa `imaplib` directo,
no la API REST de Gmail.

Detalles del sync:
- Busca correos con `label:gridworks-contabilidad has:attachment`. Para no
  procesar dos veces se saltan los que ya estan en la base (por Message-ID, con
  un fetch liviano de solo el header). La separacion pendiente/declarada la da el
  movimiento de label al aceptar (ver seccion del mantenedor arriba), no una
  sub-label automatica.
- De cada correo toma **solo el adjunto de la Invoice**, no el Receipt de
  pago (el Receipt es solo comprobante, la Invoice es el documento contable).
- El parser de cada PDF esta en `app/parsers/`. La deteccion es **por contenido
  del PDF** (no por dominio del remitente), en `app/parsers/registry.py`: cada
  proveedor es una `ProviderConfig` con una funcion `detect(text)`. Esto permite
  re-parsear PDFs ya guardados sin el remitente original.
  - `stripe_invoice.py`: parser **compartido** para la plantilla de Stripe, que
    usan Anthropic, Railway y a futuro la mayoria de los SaaS. Maneja la variante
    con IVA (Anthropic) y sin IVA (Railway).
  - `dte_nacional.py`: parser **compartido** para la factura electronica chilena.
    Es el analogo nacional del de Stripe: el formato lo fija el SII, asi que un
    proveedor chileno nuevo normalmente se resuelve con una entrada en
    `PROVIDERS` que reusa este parser, sin escribir codigo. Ojo con dos cosas del
    texto que saca pdfplumber: el digito verificador viene separado del cuerpo
    del RUT (`76.188.742- 4`) y la copia CEDIBLE **duplica todo el documento**,
    por eso las regex usan `search` y nunca `findall`. El emisor se toma del
    primer `R.U.T.` del documento (el segundo es el receptor, o sea GridWorks).
  - Parsers dedicados de formato propio: `aws.py`, `hetzner.py`, `nic.py`
    (NIC Chile es DTE nacional exento, en CLP, con RUT en el documento) y
    `bio_andes.py` (LLC de Delaware que cobra el Congreso America Digital).
  - `generic.py` sigue como fallback best-effort (marca `revision_manual=True`).
  - Agregar un proveedor nuevo = una entrada en `PROVIDERS` (si es Stripe, reusa
    `stripe_invoice.parse`; si es DTE chileno, `dte_nacional.parse`). El flag
    `revisar=True` de `ProviderConfig` es para las plantillas compartidas sin
    emisor identificado (`stripe_desconocido`, `dte_desconocido`): parsean igual
    pero quedan marcadas para que se les agregue su entrada.
  - **Diagnosticar un PDF nuevo**: `python -m app.tasks.diagnosticar <carpeta>`
    dice que proveedor detecta el registry, que extrae el parser, y deja el texto
    crudo en un `.txt` al lado de cada PDF. Ese `.txt` es tambien la fixture que
    se copia a `tests/fixtures/` para dejar el caso cubierto. La carpeta
    `facturas_pendientes/` esta gitignoreada para dejar PDFs reales ahi.
  - **Montos: usar siempre `base.parse_monto`**, nunca `float(...replace(...))`.
    El punto y la coma estan invertidos entre la convencion chilena y la
    anglosajona, y no se puede decidir por la moneda: el PDF de BIO ANDES esta en
    USD y trae las dos en el mismo documento. `parse_monto` decide por estructura
    (manda el separador de mas a la derecha). El `generic.py` viejo asumia
    convencion chilena en cuanto veia una coma y leia `$6,800.00` como **6.8**.
  - Hay una **capa de validacion** en el registry: si falta numero/total o si
    `afecto+exento+iva` no cuadra con `total`, marca `revision_manual`.
  - **Re-parseo del historico**: re-aplica los parsers sobre los PDFs ya
    guardados en la base (util al agregar parsers nuevos o corregir bugs), sin
    volver a Gmail. Hay dos caminos: el boton **Re-parsear** en la web y
    `python -m app.tasks.reparse` por consola. **Es un paso obligatorio**: un
    deploy con un parser nuevo no toca las facturas que ya estan en la base, asi
    que la pagina se sigue viendo igual hasta que se re-parsea.
    `railway run python -m app.tasks.reparse` **no** funciona desde una maquina
    local: `railway run` inyecta las variables pero ejecuta localmente, y el
    `DATABASE_URL` de produccion apunta a `postgres.railway.internal`, que solo
    resuelve dentro de Railway. Por eso existe el boton (que corre dentro del
    contenedor); la alternativa por consola es `railway ssh`.
- **AWS cobra 19% (IVA chileno)** en su factura. Queda con RUT generico
  extranjero y una nota: falta verificar su RUT en la nomina IVA digital del SII
  y confirmar con el contador si ese IVA es recuperable (en B2B el tratamiento
  puede diferir).
- **RUT de Anthropic en Chile**: `59.250.690-4` -- se saco de la nomina oficial
  del SII de contribuyentes extranjeros inscritos en IVA digital
  (https://www.sii.cl/vat/dwn_esp.html). Para proveedores sin parser dedicado
  se usa el RUT generico `55.555.555-5` (factura de compra DTE 46).
- **Moneda y conversion a CLP**: los montos se guardan en la moneda original de
  la factura (USD o CLP). Para las USD se busca el **dolar observado** de la fecha
  de emision y se guarda en `tipo_cambio` + `tipo_cambio_fecha`. El dolar observado
  es el tipo de cambio que el propio SII publica
  (https://www.sii.cl/valores_y_fechas/dolar/) y el que corresponde usar para
  operaciones en moneda extranjera; ese mismo valor lo entrega `mindicador.cl` en
  JSON (viene del Banco Central), por eso se consume de ahi.
  El **Excel** (`excel_export.py`) muestra los montos originales y agrega columnas
  `AFECTO/EXENTO/IVA/TOTAL CLP` convertidas con formula `=ROUND(monto*TC,0)`
  (auditable); el total del libro suma las columnas en CLP.
- Un bug ya resuelto: pdfplumber devuelve caracteres `\x00` en vez de
  espacios en los PDFs de Anthropic (fuente custom) -- se normalizan en
  `_extract_pdf_text` antes de aplicar cualquier regex. Si se agregan
  parsers nuevos y las regex no matchean, revisar esto primero con
  `repr(text)`.

## Estado actual (al momento del handoff)

- Repo scaffolded y **pusheado a GitHub**. Probado localmente end-to-end con
  SQLite. Nota de entorno: en este equipo hay **Python 3.14**, que obligo a
  subir los pins de `sqlalchemy` (2.0.51) y `psycopg2-binary` (2.9.12) para que
  haya wheels; se agrego `python-dotenv` y `config.py` ahora hace `load_dotenv()`.
- **Primer sync real hecho**: se procesaron **102 facturas** reales de la label.
  98 son de Anthropic (parser dedicado, limpias) y 4 de otros proveedores
  (Hetzner, AWS, Railway, NIC Chile) que al principio caian en revision manual.
- Se agregaron parsers dedicados para esos 4 y, tras `reparse`, **las 102 quedan
  parseadas sin revision manual**. Se agregaron ademas la conversion a CLP en el
  Excel y la capa de validacion.
- **Railway: pendiente que el usuario lo despliegue** (instrucciones en
  README.md -- crear servicio web + Postgres + variables de entorno + Cron
  Job). Verificar con el usuario si ya lo hizo. Ojo: la base local (SQLite) tiene
  las 102 facturas; al desplegar en Postgres se parte de cero y hay que re-sincar
  (los correos siguen con la sub-label `procesado`, asi que revisar como migrar).

## Pendientes / proximos pasos sugeridos

1. Confirmar deploy en Railway (el primer sync real ya se corrio local).
   Definir como llevar/re-sincar las 102 facturas a la base de Postgres.
2. Verificar el RUT de AWS en la nomina IVA digital del SII y el tratamiento
   del IVA que cobra (con el contador). Agregar parsers dedicados a nuevos
   proveedores frecuentes que vayan apareciendo (una entrada en `PROVIDERS`).
2b. BIO ANDES AMERICA DIGITAL LLC quedo **resuelto**: no tiene RUT chileno
   (confirmado ago-2026), asi que va con el generico y como factura de compra
   DTE 46, igual que railway y hetzner. Queda solo confirmar con el contador el
   tratamiento del monto (se asumio afecto, mismo criterio que los otros
   extranjeros).
3. UI para editar a mano una fila del "queue de revision" (hoy solo se corrige
   re-parseando). Backfill opcional de `tipo_cambio_fecha` en las facturas que
   reusaron TC del primer sync (cuesta llamadas a la API).
4. Tests de regresion con PDFs reales: **empezado** en `tests/test_parsers.py`,
   con fixtures de texto en `tests/fixtures/` (PRAXEDIS y BIO ANDES). Falta
   sumar los formatos ya cubiertos por parser pero sin fixture: Anthropic/Stripe,
   AWS, Hetzner y NIC. Se sacan con `diagnosticar` (ver arriba).
5. Dashboard: los tiles de totales hoy suman montos mezclando USD+CLP; conviene
   mostrarlos convertidos a CLP como en el Excel.
6. Libro de Ventas (mismo patron: modelo `Sale`, sync propio si aplica,
   export a Excel).
7. Integracion con el SII (aun no definida en detalle -- pendiente de
   conversacion con el usuario sobre alcance: ¿carga de facturas de venta
   electronicas? ¿consulta de estado? etc.)
8. Dominio custom `conta.gridworks.cl` en Railway.
9. Auth multi-usuario con correo y clave ya implementada (branch
   `login-correo-clave`, ver seccion "Autenticacion" arriba) — resuelto, ya
   no hace falta OAuth para que mas de una persona use la app.
10. Primer despliegue con el nuevo login: seguir el orden de "Primer
    despliegue" del README (setear `SECRET_KEY`/`BASE_URL`/`COOKIE_SECURE`/
    `ADMIN_EMAIL`+`ADMIN_PASSWORD`, confirmar en el log, entrar, cambiar
    clave, invitar al contador, y recien ahi borrar `ADMIN_EMAIL`/
    `ADMIN_PASSWORD` y la ya no usada `APP_PASSWORD` de Railway).
11. No hay roles: cualquier cuenta puede invitar y dar de baja a otras. Si
    en algun momento se necesita distinguir permisos (ej. alguien que solo
    pueda ver, no administrar usuarios), hay que agregarlo — hoy no existe.
12. Si alguna vez se restaura la base desde un backup, recordar subir a mano
    el `token_version` de todos los usuarios (o rotar `SECRET_KEY`): si el
    backup es anterior a un cambio de clave, una cookie robada que deberia
    estar muerta podria volver a servir. Ver seccion "Autenticacion" arriba.

## Variables de entorno (ver `.env.example`)

`DATABASE_URL` (la inyecta Railway), `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
`GMAIL_LABEL`, `GMAIL_DECLARED_LABEL`, `SECRET_KEY`, `BASE_URL`,
`COOKIE_SECURE`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` (estas dos ultimas solo para
la primera cuenta, se pueden borrar despues). Ninguna esta en el repo, todas
se configuran como secretos en Railway (o en `.env` local, gitignored).
`APP_PASSWORD` ya no existe: la reemplazo el login multi-usuario.
