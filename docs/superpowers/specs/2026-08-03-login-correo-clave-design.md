# Login con correo y clave

Fecha: 2026-08-03
Estado: diseño aprobado, pendiente de plan de implementación

## Problema

`conta.gridworks.cl` está protegida por una clave compartida única (`APP_PASSWORD`).
Quien tenga esa clave entra, no queda registro de quién fue, y cambiarla obliga a
avisarle a todos. Además hay un atajo peligroso: si `APP_PASSWORD` llega a estar
vacía, `require_login()` no bloquea nada y la aplicación queda pública sin aviso
(`app/auth.py:29-31`).

## Objetivo

Cuentas individuales con correo y clave para 2-3 personas (yo y el contador),
creadas a mano, todas con los mismos permisos.

## Decisiones tomadas

| Decisión | Elegido | Por qué |
|---|---|---|
| Usuarios | 2-3 cuentas, alta manual | No hay auto-registro ni dominio permitido |
| Permisos | Todos iguales | No hay campo de rol; nadie lo necesita hoy |
| Sesión | Cookie firmada sin estado | Reusa `itsdangerous`, ya presente; sin tabla de sesiones |
| Revocación | `token_version` en el payload | Cubre "cerrar todas las sesiones" sin tabla extra |
| Recuperación | Enlace por correo | Vía el SMTP de Gmail ya configurado |
| Hashing | `hashlib.scrypt` (stdlib) | Sin dependencia binaria nueva (Python 3.14 dio problemas de wheels) |
| Duración de sesión | 30 días | Igual que hoy |

Descartado: sesiones en base de datos (una tabla y una consulta por request para
funcionalidad que 2 cuentas no usan) y Google OAuth (amarra al contador a tener
cuenta Google, y se pidió correo y clave explícitamente).

## Modelo de datos

Tabla `usuarios` en `app/models.py`:

| Columna | Tipo | Notas |
|---|---|---|
| `id` | Integer PK | |
| `email` | String, único, indexado | Normalizado a minúsculas al guardar y al comparar |
| `nombre` | String, nullable | Para mostrar quién está conectado |
| `password_hash` | String | Formato autodescriptivo `scrypt$n$r$p$salt_b64$hash_b64` |
| `token_version` | Integer, default 1 | Viaja en la cookie; sube para invalidar sesiones |
| `activo` | Boolean, default True | Baja sin borrar historia |
| `intentos_fallidos` | Integer, default 0 | Reset al entrar bien |
| `bloqueado_hasta` | DateTime, nullable | |
| `ultimo_ingreso` | DateTime, nullable | |
| `reset_enviado_en` | DateTime, nullable | Freno al reenvío de enlaces |
| `creado_en` | DateTime | `server_default=func.now()` |

Sin campo de rol.

`token_version` es la pieza central: sube al cambiar la clave y al desactivar la
cuenta. Como va dentro de la cookie firmada, cualquier sesión previa deja de
validar de inmediato. El mismo mecanismo hace que un enlace de recuperación sea
de un solo uso sin llevar registro de tokens gastados.

## Componentes

Tres módulos con una responsabilidad cada uno, para que `auth.py` no termine
mezclando cripto, base de datos y HTTP.

### `app/security.py` (nuevo)

Solo criptografía. No conoce la base ni el request.

- `hash_password(plain: str) -> str`
- `verify_password(plain: str, guardado: str) -> bool` — devuelve `False` ante un
  hash malformado en vez de lanzar excepción
- `generar_clave_aleatoria() -> str` — para invitaciones

Parámetros de scrypt: `n=2**14, r=8, p=1`, salt de 16 bytes de `secrets.token_bytes`.
Comparación con `hmac.compare_digest`.

### `app/usuarios.py` (nuevo)

Operaciones sobre la tabla. No sabe de cookies.

- `autenticar(db, email, plain) -> ResultadoAuth` — devuelve un resultado explícito
  (`OK`, `CREDENCIALES_INVALIDAS`, `BLOQUEADO`, `INACTIVO`) y el usuario si aplica.
  Un booleano obligaría a la capa web a adivinar el motivo.
- `crear(db, email, nombre, plain=None)` — sin clave, genera una aleatoria
- `cambiar_clave(db, usuario, nueva)` — guarda el hash y sube `token_version`
- `desactivar(db, usuario)` — `activo=False` y sube `token_version`
- `registrar_intento_fallido(db, usuario)` — incrementa y bloquea al quinto
- `sembrar_admin_inicial(db)` — ver "Arranque"

Umbrales: 5 intentos, bloqueo de 15 minutos.

### `app/auth.py` (reescrito)

Todo lo que es token firmado y guarda de ruta. Pasa a necesitar la sesión de base
de datos, así que `require_login` se vuelve una dependencia de FastAPI.

- `create_session_cookie(usuario) -> str` — payload `{"uid": id, "v": token_version}`
- `usuario_actual(request, db) -> Usuario | None` — valida firma, vencimiento,
  que el usuario exista, esté activo, y que `v` calce con la base
- `require_login(request, db) -> Usuario` — redirige a `/login` si no hay sesión.
  **Sin atajo**: se elimina el bypass por `APP_PASSWORD` vacía.
- `crear_token_reset(usuario) -> str` — serializer con salt propio (`"reset"`),
  payload `{"uid", "v"}`
- `leer_token_reset(token, db) -> Usuario | None` — `max_age` de 30 minutos, y
  exige que `v` calce con la base (esto lo hace de un solo uso)

### Cambio en `app/gmail_sync.py`

`enviar_correo_con_etiqueta()` (línea 255) mezcla el envío SMTP con el etiquetado
por IMAP, que reintenta varios segundos. Para el correo de recuperación ese
etiquetado sobra y lo haría lento.

Se extrae `enviar_correo(to, asunto, cuerpo, adjunto=None)` con el envío plano, y
`enviar_correo_con_etiqueta()` pasa a llamarla antes de etiquetar. Mismo
comportamiento donde ya se usa.

### Rutas en `app/main.py`

| Ruta | Sesión | Qué hace |
|---|---|---|
| `GET/POST /login` | no | Correo y clave |
| `GET /logout` | no | Borra la cookie |
| `GET/POST /olvide-clave` | no | Pide correo, dispara el enlace |
| `GET/POST /restablecer/{token}` | no | Define clave nueva |
| `GET/POST /cuenta` | sí | Cambiar mi propia clave |
| `GET/POST /usuarios` | sí | Listar, invitar, dar de baja |

Las 9 llamadas existentes a `require_login(request)` pasan a la forma con
dependencia.

### Templates

Modificado: `login.html` (agrega campo de correo y enlace a "olvidé mi clave"),
`base.html` (muestra el correo conectado junto a "Salir").
Nuevos: `olvide_clave.html`, `restablecer.html`, `cuenta.html`, `usuarios.html`.

## Flujos

### Entrar

Credenciales malas, correo inexistente y cuenta dada de baja devuelven todos el
mismo mensaje: *"Correo o clave incorrectos"*. Cuando el correo no existe se
verifica igual un hash de descarte, para que el tiempo de respuesta no delate qué
cuentas existen.

Al quinto intento fallido la cuenta se bloquea 15 minutos y ahí sí se muestra el
motivo real. Esto revela que el correo existe; es un intercambio aceptado a
cambio de que la persona entienda por qué no entra.

Éxito: resetea `intentos_fallidos`, graba `ultimo_ingreso`, emite la cookie,
redirige a `/`.

### Olvidé mi clave

Responde siempre lo mismo — *"Si el correo está registrado, te enviamos un
enlace"* — exista o no la cuenta. Si existe y está activa, se envía el token.

Con freno: no se envía más de un enlace por cuenta cada 5 minutos. Sin esto,
cualquiera que conozca el correo puede llenarle la bandeja y quemar la cuota SMTP
de la cuenta de Gmail. El intento sofocado devuelve el mismo mensaje neutro, así
que desde afuera no se distingue. Se controla con una columna `reset_enviado_en`
en `usuarios`.

### Restablecer

Valida firma, vencimiento y que `v` calce. Pide la clave nueva dos veces, mínimo
10 caracteres, validado en el servidor. Al guardar sube `token_version`, lo que
mata el enlace y cierra todas las sesiones de esa cuenta. Redirige a `/login`.

### Cambiar mi clave (`/cuenta`)

Pide la clave actual. Sube `token_version`, lo que cierra las sesiones en otros
dispositivos, y **reemite la cookie propia** con la versión nueva para no echar a
quien está haciendo el cambio.

### Invitar y dar de baja (`/usuarios`)

Invitar crea la cuenta con una clave aleatoria que nadie ve y envía el enlace de
"define tu clave" (el mismo token de recuperación). Ninguna clave viaja nunca por
correo ni por mensajería.

Dar de baja marca `activo=False` y sube `token_version`: la persona queda afuera
en el acto. No se puede desactivar la cuenta propia.

Como todas las cuentas tienen los mismos permisos, cualquiera que entre puede
invitar y dar de baja. Es consecuencia directa de no tener roles, y es aceptable
entre dos personas de confianza; si mañana entra alguien más, esto es lo primero
que habría que revisar.

## Arranque y migración

La tabla la crea `create_all()` en el próximo arranque (`app/db.py:20-23`). No se
necesita migración manual: la tabla es nueva, y `_run_light_migrations()` solo
aplica a columnas agregadas a tablas existentes.

**Siembra de la primera cuenta**: en el `startup`, si la tabla `usuarios` está
vacía y existen `ADMIN_EMAIL` y `ADMIN_PASSWORD` en el entorno, se crea esa
cuenta. Si ya hay usuarios, las variables se ignoran.

**Orden del despliegue, para no quedar afuera**:

1. Poner `ADMIN_EMAIL` y `ADMIN_PASSWORD` en Railway
2. Desplegar
3. Entrar con esas credenciales
4. Cambiar la clave desde `/cuenta`
5. Borrar las dos variables de Railway

**Efectos secundarios esperados**:

- Las sesiones actuales se cortan. La cookie vieja lleva `{"ok": true}`, sin
  `uid`, y pasa a ser inválida. Hay que entrar de nuevo.
- `APP_PASSWORD` deja de usarse y se elimina de `config.py`, de `main.py` y del
  `.env`.
- Verificar que `COOKIE_SECURE=true` esté puesto en Railway: ahora viajan
  credenciales reales, no una clave compartida.
- En local ya no hay modo abierto. Se entra con la cuenta sembrada, igual que en
  producción.

## Manejo de errores

| Situación | Respuesta |
|---|---|
| SMTP caído en una recuperación | Mensaje neutro a la persona, error al log; el admin reenvía desde `/usuarios` |
| Token vencido o ya usado | Página que explica qué pasó, con botón para pedir otro. No un 400 crudo |
| Clave nueva menor a 10 caracteres | Error en el formulario, validado en el servidor |
| Las dos claves nuevas no coinciden | Error en el formulario |
| Hash malformado en la base | `verify_password` devuelve `False`, no lanza excepción |

## Pruebas

El proyecto no tiene pruebas hoy. No se propone cubrirlo entero, solo esta pieza,
que es donde un error silencioso deja la puerta abierta sin que nada se vea roto.

Con `pytest` y el `TestClient` de FastAPI contra SQLite en memoria. Hay que sumar
`pytest` a `requirements.txt`.

Casos:

1. `hash_password` / `verify_password` hacen ida y vuelta; dos hashes de la misma
   clave salen distintos (salt)
2. `verify_password` devuelve `False` ante un hash malformado
3. Cinco intentos fallidos bloquean; el sexto es rechazado aunque la clave sea
   correcta
4. Entrar bien resetea `intentos_fallidos`
5. Una cookie con `token_version` vieja es rechazada
6. Un token de reset vencido es rechazado
7. Un token de reset ya usado es rechazado
8. Una ruta protegida sin cookie redirige a `/login`
9. Una cuenta con `activo=False` no puede entrar

## Fuera de alcance

- Roles y permisos diferenciados
- Segundo factor
- Registro de auditoría de quién aceptó cada factura
- Listado y cierre de sesiones individuales
- Auto-registro

## Nota de estilo

Los comentarios en el código de este repositorio van sin tildes (ver
`app/models.py`). La implementación debe seguir esa convención, aunque este
documento use ortografía normal.
