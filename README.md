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

- `SECRET_KEY` — **obligatoria para la aplicacion web, sin default: se niega a
  arrancar sin ella.** Firma las cookies de sesion y los enlaces de
  recuperacion. Debe tener al menos 32 caracteres y no puede ser el valor de
  ejemplo del repo. Quien la conozca puede fabricarse una cookie de sesion
  valida y entrar como cualquier usuario. La comprobacion vive en
  `app/auth.py`, no en `app/config.py`, a proposito: el Cron Job
  (`app.tasks.monthly_cron`) importa la configuracion pero no firma nada, asi
  que no necesita `SECRET_KEY` para correr. Generarla con:
  ```
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — solo se usan para crear la primera
  cuenta, y solo si la tabla `usuarios` esta vacia. Se necesitan las dos
  juntas (con solo una no se crea nada). Una vez que existe esa cuenta se
  pueden borrar del entorno.
- `BASE_URL` — URL publica de la app, se usa para armar el enlace del correo
  de recuperacion de clave. Default `http://localhost:8000`. Si queda mal en
  produccion, todos los enlaces de recuperacion salen rotos. En Railway:
  `https://conta.gridworks.cl`.
- `COOKIE_SECURE` — poner en `true` en produccion (HTTPS), para que la cookie
  de sesion nunca viaje en claro. En local (http) dejar sin setear.

## Deploy en Railway

1. **New Project → Deploy from GitHub repo** → elegir `gridworks-contabilidad`.
2. **Add a Database → PostgreSQL** dentro del mismo proyecto (inyecta `DATABASE_URL` solo).
3. En el servicio web, pestaña **Variables**, agregar:
   - `GMAIL_ADDRESS`
   - `GMAIL_APP_PASSWORD`
   - `GMAIL_LABEL` (default `gridworks-contabilidad`)
   - `SECRET_KEY` (generada, ver arriba)
   - `BASE_URL` (`https://conta.gridworks.cl`)
   - `COOKIE_SECURE=true`
   - `ADMIN_EMAIL` / `ADMIN_PASSWORD` (solo para el primer arranque, ver
     "Primer despliegue" abajo)
4. Railway detecta el `Procfile` y corre `uvicorn` solo.
5. **Cron Job**: crear un nuevo servicio en el mismo proyecto, tipo *Cron Job*,
   mismo repo, comando `python -m app.tasks.monthly_cron`, schedule mensual
   (ej. `0 9 1 * *` = dia 1 de cada mes a las 9am). Le tienen que llegar las
   mismas variables de entorno (Railway permite compartir variables entre
   servicios del mismo proyecto).
6. (Opcional) Dominio custom: Settings → Networking → agregar
   `conta.gridworks.cl` y crear el CNAME que indique Railway en el DNS de
   gridworks.cl.

## Acceso

Cada persona entra con su propio correo y clave. No hay clave compartida.

- Las cuentas se crean desde `/usuarios`: se ingresa el correo de la persona
  y la app le manda un enlace para que ella misma defina su clave. Nadie
  escribe la clave de otra persona, y ninguna clave viaja por correo ni por
  chat.
- `/cuenta` permite cambiar la propia clave. Al cambiarla se cierran las
  sesiones abiertas en otros dispositivos.
- Dar de baja a alguien desde `/usuarios` le quita el acceso de inmediato
  (incluida cualquier sesion ya abierta); el boton "Reactivar" lo deshace.
- Cinco intentos fallidos bloquean la cuenta por 15 minutos.
- Los enlaces de recuperacion de clave duran 30 minutos, sirven una sola vez,
  y se puede pedir uno nuevo por cuenta cada 5 minutos como maximo.

## Primer despliegue

1. En Railway, configurar `SECRET_KEY` (generada, no inventada), `BASE_URL`,
   `COOKIE_SECURE=true`, y `ADMIN_EMAIL` + `ADMIN_PASSWORD`.
2. Deploy. La tabla `usuarios` se crea sola (`create_all()`), no hace falta
   migracion manual.
3. Revisar el log de Railway: debe aparecer `BASE_URL para los enlaces de
   correo: ...` y `Cuenta inicial creada: ...`.
4. Entrar con `ADMIN_EMAIL` / `ADMIN_PASSWORD`.
5. Cambiar la clave desde `/cuenta`.
6. Invitar al contador/a desde `/usuarios`.
7. Borrar `ADMIN_EMAIL` y `ADMIN_PASSWORD` de las variables de Railway.
8. Borrar `APP_PASSWORD` de Railway si todavia esta — ya no la lee nada.

## Si te quedas sin acceso

Con acceso a la base de datos: borrar todas las filas de `usuarios`, volver a
poner `ADMIN_EMAIL` / `ADMIN_PASSWORD` en Railway, y reiniciar el servicio. La
siembra de la cuenta inicial se vuelve a correr porque la tabla queda vacia
otra vez.

## Pruebas

```bash
venv/Scripts/python -m pytest tests/ -v
```

151 pruebas. Para que corran rapido, la suite baja el costo de scrypt durante
la ejecucion; un par de pruebas puntuales lo suben de vuelta al costo real de
produccion para verificar ese camino tambien.

## Uso

- Entrar con tu correo y tu clave (ver "Acceso" arriba).
- Boton **Actualizar ahora**: corre la sincronizacion on-demand (va a Gmail a
  buscar facturas nuevas).
- Boton **Re-parsear pendientes**: vuelve a leer los PDF **ya guardados** de las
  facturas pendientes y recalcula sus datos. No consulta Gmail. Es lo que hay que
  apretar despues de agregar o corregir un parser: el deploy solo cambia como se
  leen las facturas *futuras*, las que ya estan en la base conservan lo que
  extrajo el parser viejo hasta que se re-parsean.
  **Las ya declaradas al SII no se tocan**: se presentaron con los montos que
  tenian, y recalcularselos dejaria la base diciendo una cosa y la declaracion
  otra. El resumen final dice cuantas se dejaron fuera.
  Equivale a `python -m app.tasks.reparse`, pero corriendo dentro del contenedor.
  Para rectificar una ya declarada (parser equivocado + rectificacion ante el
  SII) hay que ser explicito por consola: `python -m app.tasks.reparse --todas`.
  Ojo: `railway run python -m app.tasks.reparse` **no** sirve desde una maquina
  local, porque ejecuta ahi y el `DATABASE_URL` apunta a la red privada de
  Railway; para eso esta el boton (o `railway ssh`).
- Los montos se muestran en la convencion de su moneda: los pesos sin decimales
  y con separador de miles (`2.273.680`), los dolares con sus dos decimales
  (`6,800.00`). Lo mismo en el Excel, con el formato numerico por fila.
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
