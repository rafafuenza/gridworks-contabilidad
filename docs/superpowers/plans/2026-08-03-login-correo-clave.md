# Login con correo y clave — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar la clave compartida única de conta.gridworks.cl por cuentas individuales con correo y clave, con recuperación por enlace de correo.

**Architecture:** Tabla `usuarios` con hash scrypt. La sesión sigue siendo una cookie firmada con `itsdangerous`, pero el payload pasa de `{"ok": true}` a `{"uid", "v"}`, donde `v` es un contador guardado en la fila del usuario. Subir ese contador invalida en el acto todas las cookies y enlaces de esa cuenta, lo que da revocación sin tabla de sesiones. La criptografía, el acceso a la tabla y las guardas HTTP viven en tres módulos separados.

**Tech Stack:** FastAPI, SQLAlchemy 2.x, itsdangerous, Jinja2, `hashlib.scrypt` (stdlib), pytest.

**Spec:** `docs/superpowers/specs/2026-08-03-login-correo-clave-design.md`

---

## Convenciones de este repositorio

Léelas antes de escribir código:

1. **Los comentarios y docstrings van sin tildes.** Ver `app/models.py`. El texto visible al usuario en templates sí puede llevarlas.
2. **No hay migraciones.** `app/db.py:20-23` usa `Base.metadata.create_all()` en el arranque. La tabla `usuarios` es nueva, así que se crea sola. No toques `_run_light_migrations()`, que solo sirve para agregar columnas a tablas ya existentes.
3. **Fechas sin zona horaria.** Las columnas nuevas que se comparan en Python usan `DateTime` naive en UTC, no `DateTime(timezone=True)`. SQLite no guarda la zona, así que al leer devuelve un datetime naive, y compararlo contra uno con zona lanza `TypeError`. Como el proyecto corre SQLite en local y Postgres en Railway, esto reventaría solo en una de las dos. Se usa el helper `ahora_utc()`.
4. **`datetime.utcnow()` está deprecado** en la versión de Python de este proyecto. Usa siempre `ahora_utc()`.

## Estructura de archivos

**Se crean:**

| Archivo | Responsabilidad |
|---|---|
| `app/security.py` | Solo criptografía: hashear, verificar, generar claves aleatorias. No conoce la base ni el request. |
| `app/usuarios.py` | Operaciones sobre la tabla `usuarios`. No conoce cookies ni HTTP. |
| `tests/conftest.py` | Fixtures de pytest: base en memoria y cliente HTTP. |
| `tests/test_security.py` | Pruebas de hashing. |
| `tests/test_usuarios.py` | Pruebas de autenticación y bloqueo. |
| `tests/test_auth.py` | Pruebas de cookies y tokens de recuperación. |
| `tests/test_rutas.py` | Pruebas de las guardas de ruta. |
| `app/templates/olvide_clave.html` | Formulario para pedir el enlace. |
| `app/templates/restablecer.html` | Formulario de clave nueva. |
| `app/templates/cuenta.html` | Cambiar la clave propia. |
| `app/templates/usuarios.html` | Listar, invitar, dar de baja. |

**Se modifican:**

| Archivo | Cambio |
|---|---|
| `app/models.py` | Agrega `Usuario` y el helper `ahora_utc()`. |
| `app/auth.py` | Reescrito: cookie con identidad, tokens de reset, `require_login` como dependencia. |
| `app/config.py` | Quita `APP_PASSWORD`; agrega `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `BASE_URL`. |
| `app/gmail_sync.py` | Extrae `enviar_correo()` de `enviar_correo_con_etiqueta()`. |
| `app/main.py` | Rutas nuevas; las 9 llamadas a `require_login(request)` pasan a dependencia. |
| `app/templates/login.html` | Agrega campo de correo y enlace de recuperación. |
| `app/templates/base.html` | Muestra el correo conectado. |
| `requirements.txt` | Agrega `pytest`. |
| `README.md`, `CONTEXT.md` | Variables de entorno y orden de despliegue. |

**Nota sobre `BASE_URL`:** el correo de recuperación necesita una URL absoluta, y no se puede derivar del request de forma confiable detrás del proxy de Railway. No estaba en el spec; es una necesidad de la implementación. Default `http://localhost:8000`, y en Railway se pone `https://conta.gridworks.cl`.

---

### Task 1: Andamiaje de pruebas

El proyecto no tiene pruebas. Esto monta la infraestructura mínima para que las tareas siguientes puedan hacer TDD.

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_andamiaje.py`

- [ ] **Step 1: Agregar pytest a requirements.txt**

Agrega esta línea al final de `requirements.txt`:

```
pytest==8.3.4
```

- [ ] **Step 2: Instalar**

```bash
venv/Scripts/pip install pytest==8.3.4
```

Expected: `Successfully installed pytest-8.3.4` (o que ya esté satisfecho).

- [ ] **Step 3: Crear `tests/__init__.py` vacío**

```python
```

Archivo vacío. Existe para que `tests` sea un paquete y no choque con nombres de módulos del proyecto.

- [ ] **Step 4: Crear `tests/conftest.py`**

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base


@pytest.fixture
def db():
    """Base SQLite en memoria, nueva para cada prueba.

    StaticPool hace que todas las conexiones compartan la misma base en
    memoria; sin eso cada conexion abriria una base vacia distinta."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app import models  # noqa: F401  (registra los modelos en Base)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    sesion = Session()
    try:
        yield sesion
    finally:
        sesion.close()
        engine.dispose()
```

- [ ] **Step 5: Crear `tests/test_andamiaje.py`**

```python
def test_la_base_de_pruebas_levanta(db):
    from app.models import Purchase

    assert db.query(Purchase).count() == 0
```

- [ ] **Step 6: Correr y verificar que pasa**

```bash
venv/Scripts/python -m pytest tests/ -v
```

Expected: PASS, 1 passed.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt tests/
git commit -m "test: andamiaje de pruebas con pytest y SQLite en memoria"
```

---

### Task 2: Módulo de criptografía

**Files:**
- Create: `app/security.py`
- Create: `tests/test_security.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Crea `tests/test_security.py`:

```python
from app.security import hash_password, verify_password, generar_clave_aleatoria


def test_hash_y_verify_hacen_ida_y_vuelta():
    guardado = hash_password("clave-secreta-123")

    assert verify_password("clave-secreta-123", guardado) is True
    assert verify_password("otra-clave", guardado) is False


def test_la_misma_clave_produce_hashes_distintos():
    """Si dos hashes de la misma clave salieran iguales, no habria salt y
    una tabla precalculada rompería todas las cuentas de una vez."""
    uno = hash_password("misma-clave")
    otro = hash_password("misma-clave")

    assert uno != otro
    assert verify_password("misma-clave", uno) is True
    assert verify_password("misma-clave", otro) is True


def test_verify_rechaza_un_hash_malformado_sin_reventar():
    """Una fila corrupta en la base no debe tumbar el login con un 500."""
    for basura in ["", "no-es-un-hash", "scrypt$mal", "scrypt$a$b$c$d$e"]:
        assert verify_password("cualquiera", basura) is False


def test_la_clave_aleatoria_tiene_largo_util():
    clave = generar_clave_aleatoria()

    assert len(clave) >= 16
    assert clave != generar_clave_aleatoria()
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_security.py -v
```

Expected: FAIL con `ModuleNotFoundError: No module named 'app.security'`.

- [ ] **Step 3: Escribir la implementación**

Crea `app/security.py`:

```python
"""Hasheo de claves. Este modulo no conoce la base de datos ni el request:
solo convierte texto plano en un hash y verifica el par.

Se usa hashlib.scrypt de la biblioteca estandar en vez de bcrypt o argon2
para no sumar una dependencia con binarios compilados."""

import base64
import hashlib
import hmac
import secrets

_ALGORITMO = "scrypt"
_N = 2 ** 16      # costo de CPU/memoria: 64 MB por hasheo (128*r*N)
_R = 8
_P = 1
_DKLEN = 32
_MAXMEM = 256 * 1024 * 1024  # holgura real sobre los 64 MB que pide n=2**16


def hash_password(plain: str) -> str:
    """Devuelve 'scrypt$n$r$p$salt_b64$hash_b64'. El formato lleva sus propios
    parametros para poder subirlos mas adelante sin invalidar los hashes viejos."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(plain.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P,
                        dklen=_DKLEN, maxmem=_MAXMEM)
    return "$".join([
        _ALGORITMO, str(_N), str(_R), str(_P),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    ])


def verify_password(plain: str, guardado: str) -> bool:
    """False ante cualquier hash que no se pueda interpretar, en vez de excepcion:
    una fila corrupta no debe tumbar el login."""
    try:
        algoritmo, n, r, p, salt_b64, hash_b64 = guardado.split("$")
        if algoritmo != _ALGORITMO:
            return False
        salt = base64.b64decode(salt_b64)
        esperado = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(plain.encode("utf-8"), salt=salt, n=int(n), r=int(r),
                            p=int(p), dklen=len(esperado), maxmem=_MAXMEM)
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(dk, esperado)


def generar_clave_aleatoria(largo: int = 16) -> str:
    """Para invitaciones: se crea la cuenta con una clave que nadie ve nunca,
    y la persona define la suya por el enlace de correo."""
    return secrets.token_urlsafe(largo)
```

- [ ] **Step 4: Correr para verificar que pasa**

```bash
venv/Scripts/python -m pytest tests/test_security.py -v
```

Expected: PASS, 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/security.py tests/test_security.py
git commit -m "feat: hasheo de claves con scrypt"
```

> **Nota de ejecución (2026-08-03):** la revisión de calidad de esta tarea pidió
> seis ajustes que se aplicaron en un commit aparte: subir `_N` a `2**16` con
> `_MAXMEM` de 256 MB (el valor original de 64 MB habría hecho fallar cualquier
> subida futura, porque `n=2**16` pide exactamente 64 MB), acotar los parámetros
> `n`/`r`/`p` leídos del hash guardado para que una fila manipulada no provoque
> una asignación enorme en cada intento de login, estrechar el `except` a solo
> `ValueError`, renombrar `largo` a `n_bytes` (`token_urlsafe` recibe bytes, no
> caracteres), normalizar a NFC antes de hashear y verificar — porque una clave
> con tilde o eñe escrita desde otro sistema produce bytes distintos y el login
> fallaría sin diagnóstico posible —, y sumar pruebas del formato del hash, del
> algoritmo equivocado y de los parámetros fuera de rango.

---

### Task 3: Modelo Usuario

**Files:**
- Modify: `app/models.py`
- Create: `tests/test_modelo_usuario.py`

- [ ] **Step 1: Escribir la prueba que falla**

Crea `tests/test_modelo_usuario.py`:

```python
from app.models import Usuario, ahora_utc


def test_usuario_nace_activo_y_con_version_uno(db):
    u = Usuario(email="rafael@gridworks.cl", password_hash="x")
    db.add(u)
    db.commit()

    guardado = db.query(Usuario).first()
    assert guardado.activo is True
    assert guardado.token_version == 1
    assert guardado.intentos_fallidos == 0
    assert guardado.bloqueado_hasta is None
    assert guardado.creado_en is not None


def test_ahora_utc_es_naive():
    """Si tuviera zona, compararlo contra lo que devuelve SQLite lanzaria
    TypeError y el bloqueo por intentos fallidos reventaria solo en local."""
    assert ahora_utc().tzinfo is None
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_modelo_usuario.py -v
```

Expected: FAIL con `ImportError: cannot import name 'Usuario' from 'app.models'`.

- [ ] **Step 3: Escribir la implementación**

En `app/models.py`, cambia la primera línea de imports y agrega el helper y el modelo.

Reemplaza las líneas 1-4:

```python
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Date, Numeric, DateTime, Boolean, Text, LargeBinary
from sqlalchemy.sql import func

from app.db import Base


def ahora_utc() -> datetime:
    """UTC sin zona horaria. Las columnas que se comparan en Python usan DateTime
    naive porque SQLite no guarda la zona: si guardaramos un datetime con zona,
    al releerlo vendria sin ella y la comparacion lanzaria TypeError."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

Y agrega al final del archivo:

```python
class Usuario(Base):
    """Cuenta de acceso a la aplicacion. Todas las cuentas tienen los mismos
    permisos: no hay campo de rol."""
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)  # siempre en minusculas
    nombre = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)

    # Va dentro de la cookie firmada y de los tokens de recuperacion. Subirlo
    # invalida de inmediato todas las sesiones y enlaces de esta cuenta.
    token_version = Column(Integer, nullable=False, default=1)

    activo = Column(Boolean, nullable=False, default=True)
    intentos_fallidos = Column(Integer, nullable=False, default=0)
    bloqueado_hasta = Column(DateTime, nullable=True)
    ultimo_ingreso = Column(DateTime, nullable=True)
    reset_enviado_en = Column(DateTime, nullable=True)  # freno al reenvio de enlaces
    creado_en = Column(DateTime, nullable=False, default=ahora_utc)
```

- [ ] **Step 4: Correr para verificar que pasa**

```bash
venv/Scripts/python -m pytest tests/test_modelo_usuario.py -v
```

Expected: PASS, 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_modelo_usuario.py
git commit -m "feat: modelo Usuario"
```

---

### Task 4: Crear y autenticar usuarios

**Files:**
- Create: `app/usuarios.py`
- Create: `tests/test_usuarios.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Crea `tests/test_usuarios.py`:

```python
from app import usuarios
from app.usuarios import Resultado


def test_crear_normaliza_el_correo_a_minusculas(db):
    u = usuarios.crear(db, "Rafael@GridWorks.CL", nombre="Rafael", clave="clave-larga-1")

    assert u.email == "rafael@gridworks.cl"


def test_autenticar_con_la_clave_correcta(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    resultado, u = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")

    assert resultado is Resultado.OK
    assert u.email == "rafael@gridworks.cl"
    assert u.ultimo_ingreso is not None


def test_autenticar_ignora_mayusculas_del_correo(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    resultado, _ = usuarios.autenticar(db, "  Rafael@GridWorks.CL  ", "clave-larga-1")

    assert resultado is Resultado.OK


def test_autenticar_con_la_clave_mala(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    resultado, u = usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")

    assert resultado is Resultado.CREDENCIALES_INVALIDAS
    assert u is None


def test_autenticar_con_un_correo_que_no_existe(db):
    resultado, u = usuarios.autenticar(db, "nadie@gridworks.cl", "lo-que-sea")

    assert resultado is Resultado.CREDENCIALES_INVALIDAS
    assert u is None


def test_una_cuenta_dada_de_baja_no_entra(db):
    u = usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")
    usuarios.desactivar(db, u)

    resultado, _ = usuarios.autenticar(db, "contador@gridworks.cl", "clave-larga-1")

    assert resultado is Resultado.INACTIVO


def test_crear_sin_clave_genera_una_al_azar(db):
    """Las invitaciones crean la cuenta sin que nadie escriba una clave."""
    u = usuarios.crear(db, "contador@gridworks.cl")

    assert u.password_hash
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_usuarios.py -v
```

Expected: FAIL con `ModuleNotFoundError: No module named 'app.usuarios'`.

- [ ] **Step 3: Escribir la implementación**

Crea `app/usuarios.py`:

```python
"""Operaciones sobre la tabla de usuarios. Este modulo no conoce cookies ni
HTTP: la capa web decide que hacer con los resultados que devuelve."""

from datetime import timedelta
from enum import Enum

from sqlalchemy.orm import Session

from app.models import Usuario, ahora_utc
from app.security import hash_password, verify_password, generar_clave_aleatoria

MAX_INTENTOS = 5
BLOQUEO_MINUTOS = 15
RESET_ESPERA_MINUTOS = 5
LARGO_MINIMO_CLAVE = 10

# Hash de descarte contra el que se verifica cuando el correo no existe, para
# que el tiempo de respuesta sea parecido y no delate que cuentas estan creadas.
_HASH_DESCARTE = hash_password(generar_clave_aleatoria())


class Resultado(str, Enum):
    OK = "ok"
    CREDENCIALES_INVALIDAS = "credenciales_invalidas"
    BLOQUEADO = "bloqueado"
    INACTIVO = "inactivo"


def normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


def por_email(db: Session, email: str):
    return db.query(Usuario).filter(Usuario.email == normalizar_email(email)).first()


def crear(db: Session, email: str, nombre: str = "", clave: str = None) -> Usuario:
    """Si no se pasa clave se genera una al azar que nadie ve: la persona define
    la suya por el enlace de correo."""
    u = Usuario(
        email=normalizar_email(email),
        nombre=nombre or None,
        password_hash=hash_password(clave or generar_clave_aleatoria()),
    )
    db.add(u)
    db.commit()
    return u


def autenticar(db: Session, email: str, clave: str):
    """Devuelve (Resultado, Usuario | None)."""
    u = por_email(db, email)

    if u is None:
        verify_password(clave, _HASH_DESCARTE)  # gasta el mismo tiempo a proposito
        return Resultado.CREDENCIALES_INVALIDAS, None

    if u.bloqueado_hasta and u.bloqueado_hasta > ahora_utc():
        return Resultado.BLOQUEADO, None

    if not verify_password(clave, u.password_hash):
        _registrar_intento_fallido(db, u)
        return Resultado.CREDENCIALES_INVALIDAS, None

    # La clave es correcta: recien aqui se revisa si la cuenta sigue habilitada,
    # para no revelar el estado de la cuenta a quien no sabe la clave.
    if not u.activo:
        return Resultado.INACTIVO, None

    u.intentos_fallidos = 0
    u.bloqueado_hasta = None
    u.ultimo_ingreso = ahora_utc()
    db.commit()
    return Resultado.OK, u


def _registrar_intento_fallido(db: Session, u: Usuario) -> None:
    """El incremento va en la base y no en Python: entre que se leyo el usuario
    y se escribe pasan ~650 ms hasheando, y varios intentos en paralelo leerian
    todos el mismo valor viejo y se pisarian, dejando el bloqueo sin efecto."""
    db.query(Usuario).filter(Usuario.id == u.id).update(
        {Usuario.intentos_fallidos: Usuario.intentos_fallidos + 1},
        synchronize_session=False,
    )
    db.commit()
    db.refresh(u)
    if u.intentos_fallidos >= MAX_INTENTOS:
        u.bloqueado_hasta = ahora_utc() + timedelta(minutes=BLOQUEO_MINUTOS)
        u.intentos_fallidos = 0
        db.commit()


def cambiar_clave(db: Session, u: Usuario, nueva: str) -> None:
    """Sube token_version: mata el enlace de recuperacion usado y cierra
    cualquier sesion abierta de esta cuenta."""
    u.password_hash = hash_password(nueva)
    u.token_version = (u.token_version or 1) + 1
    u.intentos_fallidos = 0
    u.bloqueado_hasta = None
    db.commit()


def desactivar(db: Session, u: Usuario) -> None:
    u.activo = False
    u.token_version = (u.token_version or 1) + 1
    db.commit()
```

- [ ] **Step 4: Correr para verificar que pasa**

```bash
venv/Scripts/python -m pytest tests/test_usuarios.py -v
```

Expected: PASS, 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/usuarios.py tests/test_usuarios.py
git commit -m "feat: crear y autenticar usuarios"
```

> **Nota de ejecución (2026-08-03):** la revisión de calidad encontró que el
> bloqueo, tal como estaba escrito arriba originalmente, no servía. El contador
> se leía en Python y se reescribía como valor absoluto; entre la lectura y la
> escritura pasan ~650 ms hasheando, y FastAPI atiende las rutas síncronas en un
> pool de 40 hilos, así que 40 intentos simultáneos leían todos `0` y escribían
> todos `1` — 40 pruebas costaban un solo incremento, repetible sin límite. El
> código de arriba ya está corregido con el incremento atómico en la base.
>
> De ahí salieron otros tres cambios: un tope de dos hasheos simultáneos en
> `app/security.py` (cada hasheo pide 64 MB y el camino de "correo no existe"
> también hashea, así que sin tope un puñado de intentos voltea el contenedor);
> una prueba de concurrencia con 8 hilos, que usa una base SQLite en archivo y
> no `StaticPool` — con `StaticPool` los 8 hilos comparten una sola conexión
> sqlite3 y la prueba falla sola de forma intermitente; y comentarios que dejan
> por escrito que el camino de cuenta bloqueada responde rápido a propósito.
>
> El arreglo se verificó revirtiéndolo: 11 de 11 corridas verdes con el
> incremento atómico, 3 de 3 rojas sin él.

---

### Task 5: Bloqueo por intentos fallidos

**Files:**
- Modify: `tests/test_usuarios.py`

La lógica ya quedó escrita en la Task 4. Esta tarea la cubre con pruebas, que es donde importa: un bloqueo mal contado se ve exactamente igual que uno bien contado hasta que alguien lo ataca.

- [ ] **Step 1: Escribir las pruebas que fallan**

Agrega al final de `tests/test_usuarios.py`:

```python
from datetime import timedelta

from app.models import ahora_utc


def test_cinco_intentos_fallidos_bloquean_la_cuenta(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    for _ in range(usuarios.MAX_INTENTOS):
        resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")
        assert resultado is Resultado.CREDENCIALES_INVALIDAS

    # La clave correcta tampoco entra mientras dure el bloqueo
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is Resultado.BLOQUEADO


def test_el_bloqueo_se_suelta_cuando_vence(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    for _ in range(usuarios.MAX_INTENTOS):
        usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")

    u.bloqueado_hasta = ahora_utc() - timedelta(seconds=1)
    db.commit()

    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is Resultado.OK


def test_entrar_bien_resetea_el_contador(db):
    usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    for _ in range(usuarios.MAX_INTENTOS - 1):
        usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")

    usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    u = usuarios.por_email(db, "rafael@gridworks.cl")
    assert u.intentos_fallidos == 0

    # Y el contador parte de cero de nuevo, no queda a un paso del bloqueo
    for _ in range(usuarios.MAX_INTENTOS - 1):
        usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is Resultado.OK


def test_cambiar_la_clave_sube_la_version_y_suelta_el_bloqueo(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    version_inicial = u.token_version
    for _ in range(usuarios.MAX_INTENTOS):
        usuarios.autenticar(db, "rafael@gridworks.cl", "equivocada")

    usuarios.cambiar_clave(db, u, "clave-nueva-larga-2")

    assert u.token_version == version_inicial + 1
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-nueva-larga-2")
    assert resultado is Resultado.OK


def test_desactivar_sube_la_version(db):
    u = usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")
    version_inicial = u.token_version

    usuarios.desactivar(db, u)

    assert u.token_version == version_inicial + 1
```

- [ ] **Step 2: Correr**

```bash
venv/Scripts/python -m pytest tests/test_usuarios.py -v
```

Expected: PASS, 12 passed. Si alguna falla, el error está en `_registrar_intento_fallido` o en `autenticar` de la Task 4, no en la prueba.

- [ ] **Step 3: Commit**

```bash
git add tests/test_usuarios.py
git commit -m "test: bloqueo por intentos fallidos"
```

---

### Task 6: Freno al reenvío de enlaces de recuperación

**Files:**
- Modify: `app/usuarios.py`
- Modify: `tests/test_usuarios.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Agrega al final de `tests/test_usuarios.py`:

```python
def test_no_se_puede_pedir_dos_enlaces_seguidos(db):
    """Sin esto, cualquiera que conozca el correo llena la bandeja y quema
    la cuota SMTP de la cuenta de Gmail."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    assert usuarios.puede_enviar_reset(u) is True
    usuarios.marcar_reset_enviado(db, u)
    assert usuarios.puede_enviar_reset(u) is False


def test_el_freno_se_suelta_al_pasar_la_espera(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    usuarios.marcar_reset_enviado(db, u)

    u.reset_enviado_en = ahora_utc() - timedelta(minutes=usuarios.RESET_ESPERA_MINUTOS, seconds=1)
    db.commit()

    assert usuarios.puede_enviar_reset(u) is True
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_usuarios.py -k reset -v
```

Expected: FAIL con `AttributeError: module 'app.usuarios' has no attribute 'puede_enviar_reset'`.

- [ ] **Step 3: Escribir la implementación**

Agrega al final de `app/usuarios.py`:

```python
def puede_enviar_reset(u: Usuario) -> bool:
    """Un enlace de recuperacion cada RESET_ESPERA_MINUTOS por cuenta."""
    if u.reset_enviado_en is None:
        return True
    return ahora_utc() - u.reset_enviado_en >= timedelta(minutes=RESET_ESPERA_MINUTOS)


def marcar_reset_enviado(db: Session, u: Usuario) -> None:
    u.reset_enviado_en = ahora_utc()
    db.commit()
```

- [ ] **Step 4: Correr**

```bash
venv/Scripts/python -m pytest tests/test_usuarios.py -v
```

Expected: PASS, 14 passed.

- [ ] **Step 5: Commit**

```bash
git add app/usuarios.py tests/test_usuarios.py
git commit -m "feat: freno al reenvio de enlaces de recuperacion"
```

---

### Task 7: Configuración

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: Editar `app/config.py`**

Reemplaza las líneas 28-32 (desde `APP_PASSWORD = ...` hasta el final) por:

```python
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
# En produccion (HTTPS, ej. Railway) poner COOKIE_SECURE=true para que la cookie
# de sesion nunca viaje en claro. En local (http) dejar sin setear.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")

# Siembra de la primera cuenta. Solo se usan si la tabla usuarios esta vacia;
# una vez creada la cuenta se pueden borrar del entorno.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# URL publica, para armar el enlace absoluto del correo de recuperacion. No se
# deriva del request porque detras del proxy de Railway no es confiable.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
```

Nota que `APP_PASSWORD` desaparece por completo.

- [ ] **Step 2: Verificar que el módulo carga**

```bash
venv/Scripts/python -c "from app.config import BASE_URL, ADMIN_EMAIL; print(BASE_URL)"
```

Expected: `http://localhost:8000`.

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat: variables ADMIN_EMAIL, ADMIN_PASSWORD y BASE_URL; quita APP_PASSWORD"
```

---

### Task 8: Sesión con identidad

Reescribe `app/auth.py`. En este punto `app/main.py` queda roto porque importa `is_valid_session` y `APP_PASSWORD`; se arregla en la Task 12. Las pruebas de esta tarea no tocan `main.py`, así que corren igual.

**Files:**
- Modify: `app/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Crea `tests/test_auth.py`:

```python
from app import auth, usuarios


class RequestFalso:
    """Lo minimo que mira usuario_actual: las cookies."""

    def __init__(self, cookies=None):
        self.cookies = cookies or {}


def _request_con_sesion(u):
    return RequestFalso({auth.COOKIE_NAME: auth.create_session_cookie(u)})


def test_una_cookie_valida_identifica_al_usuario(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    encontrado = auth.usuario_actual(_request_con_sesion(u), db)

    assert encontrado is not None
    assert encontrado.email == "rafael@gridworks.cl"


def test_sin_cookie_no_hay_usuario(db):
    assert auth.usuario_actual(RequestFalso(), db) is None


def test_una_cookie_falsificada_se_rechaza(db):
    peticion = RequestFalso({auth.COOKIE_NAME: "esto-no-viene-firmado"})

    assert auth.usuario_actual(peticion, db) is None


def test_una_cookie_con_version_vieja_se_rechaza(db):
    """Es el mecanismo que cierra las sesiones al cambiar la clave."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    peticion = _request_con_sesion(u)

    usuarios.cambiar_clave(db, u, "clave-nueva-larga-2")

    assert auth.usuario_actual(peticion, db) is None


def test_la_cookie_de_una_cuenta_dada_de_baja_se_rechaza(db):
    u = usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")
    peticion = _request_con_sesion(u)

    usuarios.desactivar(db, u)

    assert auth.usuario_actual(peticion, db) is None


def test_la_cookie_de_un_usuario_borrado_se_rechaza(db):
    u = usuarios.crear(db, "temporal@gridworks.cl", clave="clave-larga-1")
    peticion = _request_con_sesion(u)

    db.delete(u)
    db.commit()

    assert auth.usuario_actual(peticion, db) is None
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_auth.py -v
```

Expected: FAIL con `AttributeError: module 'app.auth' has no attribute 'usuario_actual'`.

- [ ] **Step 3: Escribir la implementación**

Reemplaza el contenido completo de `app/auth.py` por:

```python
"""Sesion y guardas de ruta. Todo lo que es token firmado vive aqui."""

from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from app.config import SECRET_KEY
from app.db import get_db
from app.models import Usuario

COOKIE_NAME = "session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 dias

_serializer = URLSafeTimedSerializer(SECRET_KEY)


def create_session_cookie(u: Usuario) -> str:
    """El payload lleva la version del usuario: cuando esa version sube en la
    base, esta cookie deja de validar sin necesidad de tabla de sesiones."""
    return _serializer.dumps({"uid": u.id, "v": u.token_version})


def usuario_actual(request: Request, db: Session):
    """Usuario de la sesion, o None. Rechaza firma invalida, vencimiento,
    usuario borrado, cuenta dada de baja y version desactualizada."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        datos = _serializer.loads(token, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(datos, dict):
        return None  # cookie del formato viejo ({"ok": true})

    u = db.query(Usuario).filter(Usuario.id == datos.get("uid")).first()
    if u is None or not u.activo:
        return None
    if datos.get("v") != u.token_version:
        return None
    return u


def require_login(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """Dependencia de FastAPI. Sin sesion valida manda al login: no hay ningun
    atajo que deje pasar sin autenticacion."""
    u = usuario_actual(request, db)
    if u is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return u
```

- [ ] **Step 4: Correr**

```bash
venv/Scripts/python -m pytest tests/test_auth.py -v
```

Expected: PASS, 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat: sesion con identidad de usuario y revocacion por version"
```

---

### Task 9: Tokens de recuperación

**Files:**
- Modify: `app/auth.py`
- Modify: `tests/test_auth.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Agrega al final de `tests/test_auth.py`:

```python
def test_un_token_de_reset_identifica_al_usuario(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    encontrado = auth.leer_token_reset(auth.crear_token_reset(u), db)

    assert encontrado is not None
    assert encontrado.email == "rafael@gridworks.cl"


def test_un_token_de_reset_ya_usado_se_rechaza(db):
    """Al cambiar la clave sube token_version, y eso mata el enlace. Es lo que
    lo hace de un solo uso sin llevar registro de tokens gastados."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    token = auth.crear_token_reset(u)

    usuarios.cambiar_clave(db, u, "clave-nueva-larga-2")

    assert auth.leer_token_reset(token, db) is None


def test_un_token_de_reset_vencido_se_rechaza(db, monkeypatch):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    token = auth.crear_token_reset(u)

    monkeypatch.setattr(auth, "RESET_MAX_AGE", -1)

    assert auth.leer_token_reset(token, db) is None


def test_una_cookie_de_sesion_no_sirve_como_token_de_reset(db):
    """Salt distinto: un token no se puede usar en el otro flujo."""
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")

    assert auth.leer_token_reset(auth.create_session_cookie(u), db) is None


def test_un_token_de_reset_no_sirve_como_cookie_de_sesion(db):
    u = usuarios.crear(db, "rafael@gridworks.cl", clave="clave-larga-1")
    peticion = RequestFalso({auth.COOKIE_NAME: auth.crear_token_reset(u)})

    assert auth.usuario_actual(peticion, db) is None
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_auth.py -k reset -v
```

Expected: FAIL con `AttributeError: module 'app.auth' has no attribute 'crear_token_reset'`.

- [ ] **Step 3: Escribir la implementación**

En `app/auth.py`, agrega junto a las otras constantes:

```python
RESET_MAX_AGE = 60 * 30  # 30 minutos
SALT_RESET = "reset-clave"
```

Y al final del archivo:

```python
def crear_token_reset(u: Usuario) -> str:
    """Salt propio para que un token de recuperacion no sirva como cookie de
    sesion ni al reves."""
    return _serializer.dumps({"uid": u.id, "v": u.token_version}, salt=SALT_RESET)


def leer_token_reset(token: str, db: Session):
    """Usuario del token, o None. Que la version tenga que calzar hace que el
    enlace sirva una sola vez: al cambiar la clave, sube y el token muere."""
    try:
        datos = _serializer.loads(token, max_age=RESET_MAX_AGE, salt=SALT_RESET)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(datos, dict):
        return None

    u = db.query(Usuario).filter(Usuario.id == datos.get("uid")).first()
    if u is None or not u.activo:
        return None
    if datos.get("v") != u.token_version:
        return None
    return u
```

**Importante:** `leer_token_reset` debe leer `RESET_MAX_AGE` desde el módulo en cada llamada (o sea, referenciar la constante global directamente, como está escrito) para que la prueba con `monkeypatch` funcione.

- [ ] **Step 4: Correr**

```bash
venv/Scripts/python -m pytest tests/test_auth.py -v
```

Expected: PASS, 11 passed.

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat: tokens de recuperacion de clave"
```

---

### Task 10: Siembra de la primera cuenta

**Files:**
- Modify: `app/usuarios.py`
- Modify: `tests/test_usuarios.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Agrega al final de `tests/test_usuarios.py`:

```python
def test_la_siembra_crea_la_primera_cuenta(db, monkeypatch):
    monkeypatch.setattr(usuarios, "ADMIN_EMAIL", "rafael@gridworks.cl")
    monkeypatch.setattr(usuarios, "ADMIN_PASSWORD", "clave-de-arranque-1")

    creado = usuarios.sembrar_admin_inicial(db)

    assert creado is not None
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-de-arranque-1")
    assert resultado is Resultado.OK


def test_la_siembra_no_hace_nada_si_ya_hay_usuarios(db, monkeypatch):
    """Asi las variables de arranque se pueden dejar puestas sin que pisen
    la clave que la persona ya cambio."""
    usuarios.crear(db, "alguien@gridworks.cl", clave="clave-larga-1")
    monkeypatch.setattr(usuarios, "ADMIN_EMAIL", "rafael@gridworks.cl")
    monkeypatch.setattr(usuarios, "ADMIN_PASSWORD", "clave-de-arranque-1")

    assert usuarios.sembrar_admin_inicial(db) is None
    assert usuarios.por_email(db, "rafael@gridworks.cl") is None


def test_la_siembra_no_hace_nada_sin_variables(db, monkeypatch):
    monkeypatch.setattr(usuarios, "ADMIN_EMAIL", "")
    monkeypatch.setattr(usuarios, "ADMIN_PASSWORD", "")

    assert usuarios.sembrar_admin_inicial(db) is None
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_usuarios.py -k siembra -v
```

Expected: FAIL con `AttributeError: module 'app.usuarios' has no attribute 'ADMIN_EMAIL'`.

- [ ] **Step 3: Escribir la implementación**

En `app/usuarios.py`, agrega al bloque de imports:

```python
from app.config import ADMIN_EMAIL, ADMIN_PASSWORD
```

Y al final del archivo:

```python
def sembrar_admin_inicial(db: Session):
    """Crea la primera cuenta desde el entorno, solo si no hay ninguna. Devuelve
    el usuario creado o None. Una vez creada, las variables se pueden borrar."""
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        return None
    if db.query(Usuario).count() > 0:
        return None
    return crear(db, ADMIN_EMAIL, nombre="Admin", clave=ADMIN_PASSWORD)
```

- [ ] **Step 4: Correr**

```bash
venv/Scripts/python -m pytest tests/test_usuarios.py -v
```

Expected: PASS, 17 passed.

- [ ] **Step 5: Commit**

```bash
git add app/usuarios.py tests/test_usuarios.py
git commit -m "feat: siembra de la primera cuenta desde el entorno"
```

---

### Task 11: Envío de correo sin etiquetado

`enviar_correo_con_etiqueta()` hace el envío SMTP y después busca el correo por IMAP para etiquetarlo, reintentando varios segundos. Para el correo de recuperación ese etiquetado sobra y lo haría lento.

**Files:**
- Modify: `app/gmail_sync.py:255-284`

- [ ] **Step 1: Extraer la función**

Reemplaza la función completa `enviar_correo_con_etiqueta` (líneas 255-284) por estas dos:

```python
def enviar_correo(to: str, asunto: str, cuerpo: str, adjunto=None) -> str:
    """Envia un correo por SMTP (Gmail). Devuelve el Message-ID usado.

    adjunto: opcional, tupla (filename, bytes, subtipo_mime)."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError("Faltan GMAIL_ADDRESS / GMAIL_APP_PASSWORD en las variables de entorno.")

    msg = MIMEMultipart()
    msg["Subject"] = asunto
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    mid = make_msgid()
    msg["Message-ID"] = mid
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

    if adjunto:
        filename, data, subtipo = adjunto
        part = MIMEApplication(data, _subtype=subtipo)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.sendmail(GMAIL_ADDRESS, [to], msg.as_string())

    return mid


def enviar_correo_con_etiqueta(to: str, asunto: str, cuerpo: str, etiqueta: str, adjunto=None) -> str:
    """Envia un correo y le aplica una etiqueta de Gmail a la copia guardada
    (en Enviados). Devuelve el Message-ID usado."""
    mid = enviar_correo(to, asunto, cuerpo, adjunto=adjunto)
    _etiquetar_mensaje(mid, etiqueta)
    return mid
```

- [ ] **Step 2: Verificar que el módulo carga y la firma no cambió**

```bash
venv/Scripts/python -c "from app import gmail_sync; import inspect; print(inspect.signature(gmail_sync.enviar_correo_con_etiqueta)); print(inspect.signature(gmail_sync.enviar_correo))"
```

Expected:
```
(to: str, asunto: str, cuerpo: str, etiqueta: str, adjunto=None) -> str
(to: str, asunto: str, cuerpo: str, adjunto=None) -> str
```

- [ ] **Step 3: Commit**

```bash
git add app/gmail_sync.py
git commit -m "refactor: extrae enviar_correo del envio con etiqueta"
```

---

### Task 12: Login y logout con correo

Aquí `app/main.py` vuelve a cargar. Cambia el login, y las 9 rutas existentes pasan a la dependencia.

**Files:**
- Modify: `app/main.py`
- Modify: `app/templates/login.html`
- Modify: `app/templates/base.html`

- [ ] **Step 1: Cambiar los imports de `app/main.py`**

Reemplaza las líneas 12-17 (nota que `BackgroundTasks` se suma al import de
`fastapi` de la línea 6, que lo necesitan las rutas de las Tasks 14 y 16):

```python
from app.config import COOKIE_SECURE, NOTIFY_EMAIL, GMAIL_LABEL, BASE_URL
from app.db import get_db, init_db, SessionLocal
from app.models import Purchase, Usuario
from app.auth import (require_login, usuario_actual, create_session_cookie, crear_token_reset,
                      leer_token_reset, COOKIE_NAME, MAX_AGE)
from app import gmail_sync, mantenedor, usuarios
from app.usuarios import Resultado, LARGO_MINIMO_CLAVE
from app.excel_export import build_workbook
```

- [ ] **Step 2: Sembrar la primera cuenta en el arranque**

Reemplaza la función `on_startup` (líneas 23-30):

```python
@app.on_event("startup")
def on_startup():
    init_db()
    db = SessionLocal()
    try:
        mantenedor.ensure_seed(db)
        sembrado = usuarios.sembrar_admin_inicial(db)
        if sembrado:
            print(f"[arranque] Cuenta inicial creada: {sembrado.email}")
    finally:
        db.close()
```

- [ ] **Step 3: Reemplazar las rutas de login y logout**

Reemplaza las líneas 38-61 (`login_form`, `login_submit`, `logout`) por:

```python
def _set_session(resp, u: Usuario):
    resp.set_cookie(COOKIE_NAME, create_session_cookie(u), httponly=True, samesite="lax",
                    secure=COOKIE_SECURE, max_age=MAX_AGE)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db)):
    if usuario_actual(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "usuario": None})


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...),
                 db: Session = Depends(get_db)):
    resultado, u = usuarios.autenticar(db, email, password)

    if resultado is Resultado.OK:
        return _set_session(RedirectResponse("/", status_code=303), u)

    if resultado is Resultado.BLOQUEADO:
        error = (f"Demasiados intentos fallidos. Espera {usuarios.BLOQUEO_MINUTOS} minutos "
                 "o restablece tu clave.")
    else:
        # Credenciales malas, correo inexistente y cuenta dada de baja dan el
        # mismo mensaje, para no revelar que cuentas existen.
        error = "Correo o clave incorrectos."

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "usuario": None, "error": error, "email": email},
        status_code=401,
    )


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp
```

- [ ] **Step 4: Migrar las 9 rutas protegidas a la dependencia**

En cada una de estas rutas, borra la línea `require_login(request)` del cuerpo y agrega el parámetro `usuario: Usuario = Depends(require_login)` a la firma:

| Línea original | Función |
|---|---|
| 64-66 | `dashboard` |
| 119-121 | `trigger_sync` |
| 132-134 | `sync_estado` |
| 138-140 | `aceptar_uno` |
| 238-240 | `aceptar_mes` |
| 254-256 | `aceptar_mes_estado` |
| 260-262 | `export_excel` |
| 271-273 | `export_pdfs` |
| 298-300 | `view_pdf` |

Ejemplo del antes y el después en `dashboard`:

```python
# antes
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, mes: str = "", flash: str = "", db: Session = Depends(get_db)):
    require_login(request)

    periodos = [...]

# despues
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, mes: str = "", flash: str = "",
              usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
    periodos = [...]
```

Y en `dashboard`, cambia el contexto del template: donde dice `"authenticated": True`, pon `"usuario": usuario`.

Las rutas sin parámetro `db` (`trigger_sync`, `sync_estado`, `aceptar_mes_estado`) igual reciben `usuario: Usuario = Depends(require_login)`; no necesitan agregar `db`, porque la dependencia trae la suya.

Las 9 firmas quedan exactamente así:

```python
def dashboard(request: Request, mes: str = "", flash: str = "",
              usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):

def trigger_sync(request: Request, usuario: Usuario = Depends(require_login)):

def sync_estado(request: Request, usuario: Usuario = Depends(require_login)):

def aceptar_uno(request: Request, purchase_id: int, mes: str = Form(""),
                usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):

def aceptar_mes(request: Request, mes: str = Form(""),
                usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):

def aceptar_mes_estado(request: Request, usuario: Usuario = Depends(require_login)):

def export_excel(request: Request, mes: str = "",
                 usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):

def export_pdfs(request: Request, mes: str = "",
                usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):

def view_pdf(request: Request, purchase_id: int,
             usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
```

Los cuerpos no cambian, salvo que se borra la línea `require_login(request)` de cada uno.

- [ ] **Step 5: Actualizar `app/templates/base.html`**

Reemplaza el bloque `<nav>` (líneas 11-16):

```html
  <nav class="bg-gray-900 text-white px-6 py-3 flex justify-between items-center">
    <span class="font-semibold">GridWorks · Libro de Compras</span>
    {% if usuario %}
    <span class="text-sm text-gray-300 flex items-center gap-4">
      <a href="/usuarios" class="hover:text-white">Usuarios</a>
      <a href="/cuenta" class="hover:text-white">{{ usuario.email }}</a>
      <a href="/logout" class="hover:text-white">Salir</a>
    </span>
    {% endif %}
  </nav>
```

- [ ] **Step 6: Actualizar `app/templates/login.html`**

Reemplaza el archivo completo:

```html
{% extends "base.html" %}
{% block content %}
<div class="max-w-sm mx-auto mt-20 bg-white p-6 rounded-lg shadow">
  <h1 class="text-lg font-semibold mb-4">Iniciar sesión</h1>
  {% if error %}
    <p class="text-red-600 text-sm mb-3">{{ error }}</p>
  {% endif %}
  <form method="post" action="/login">
    <input type="email" name="email" placeholder="Correo" required autofocus
           value="{{ email or '' }}"
           class="w-full border rounded px-3 py-2 mb-3" />
    <input type="password" name="password" placeholder="Clave" required
           class="w-full border rounded px-3 py-2 mb-3" />
    <button type="submit" class="w-full bg-gray-900 text-white rounded px-3 py-2">Entrar</button>
  </form>
  <p class="text-sm text-gray-500 mt-3">
    <a href="/olvide-clave" class="hover:underline">Olvidé mi clave</a>
  </p>
</div>
{% endblock %}
```

- [ ] **Step 7: Verificar que la aplicación levanta**

```bash
venv/Scripts/python -c "from app.main import app; print([r.path for r in app.routes])"
```

Expected: la lista incluye `/login`, `/logout`, `/`, `/sync`, `/export.xlsx`.

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/templates/login.html app/templates/base.html
git commit -m "feat: login con correo y clave"
```

---

### Task 13: Pruebas de las guardas de ruta

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_rutas.py`

- [ ] **Step 1: Agregar el fixture de cliente HTTP**

Agrega al final de `tests/conftest.py`:

```python
@pytest.fixture
def client(db):
    """Cliente HTTP contra la app real, con la base de pruebas inyectada.

    raise_server_exceptions=False deja que el 303 que lanza require_login se
    vea como respuesta en vez de propagarse como excepcion.

    OJO: no se usa 'with TestClient(...)'. El context manager dispara los
    eventos de startup, y on_startup llama a init_db() y ensure_seed() contra
    el engine real: las pruebas escribirian en local.db (o peor, en el Postgres
    de Railway si DATABASE_URL esta apuntando alla). Sin el 'with', el lifespan
    no corre y la app usa solo la base inyectada por dependency_overrides."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Escribir las pruebas que fallan**

Crea `tests/test_rutas.py`:

```python
import pytest

from app import auth, usuarios


@pytest.fixture
def usuario(db):
    return usuarios.crear(db, "rafael@gridworks.cl", nombre="Rafael", clave="clave-larga-1")


RUTAS_PROTEGIDAS = ["/", "/sync/estado", "/aceptar-mes/estado", "/export.xlsx", "/export/pdfs.zip"]


@pytest.mark.parametrize("ruta", RUTAS_PROTEGIDAS)
def test_sin_sesion_las_rutas_protegidas_mandan_al_login(client, ruta):
    resp = client.get(ruta, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_con_sesion_valida_el_tablero_responde(client, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.get("/", follow_redirects=False)

    assert resp.status_code == 200
    assert "rafael@gridworks.cl" in resp.text


def test_entrar_por_el_formulario_deja_la_cookie(client, usuario):
    resp = client.post("/login", data={"email": "rafael@gridworks.cl", "password": "clave-larga-1"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert auth.COOKIE_NAME in resp.cookies


def test_la_clave_mala_no_deja_cookie(client, usuario):
    resp = client.post("/login", data={"email": "rafael@gridworks.cl", "password": "equivocada"},
                       follow_redirects=False)

    assert resp.status_code == 401
    assert auth.COOKIE_NAME not in resp.cookies


def test_un_correo_inexistente_da_el_mismo_mensaje_que_una_clave_mala(client, usuario):
    """Si los mensajes difirieran, se podria averiguar que correos tienen cuenta."""
    con_cuenta = client.post("/login", data={"email": "rafael@gridworks.cl", "password": "equivocada"})
    sin_cuenta = client.post("/login", data={"email": "nadie@gridworks.cl", "password": "equivocada"})

    assert con_cuenta.status_code == sin_cuenta.status_code == 401
    assert "Correo o clave incorrectos." in con_cuenta.text
    assert "Correo o clave incorrectos." in sin_cuenta.text


def test_salir_borra_la_cookie(client, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.get("/logout", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
```

- [ ] **Step 3: Correr**

```bash
venv/Scripts/python -m pytest tests/test_rutas.py -v
```

Expected: PASS, 10 passed (5 rutas parametrizadas + 5 pruebas).

Si `/` da 500 en vez de 200, revisa que `dashboard` esté pasando `"usuario": usuario` al template y que `base.html` use `{% if usuario %}`.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_rutas.py
git commit -m "test: guardas de ruta y flujo de login"
```

---

### Task 14: Recuperación de clave

**Files:**
- Modify: `app/main.py`
- Create: `app/templates/olvide_clave.html`
- Create: `app/templates/restablecer.html`
- Modify: `tests/test_rutas.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Agrega al final de `tests/test_rutas.py`:

```python
def test_pedir_enlace_responde_igual_exista_o_no_la_cuenta(client, usuario, monkeypatch):
    """El mensaje neutro es lo que impide usar este formulario como buscador
    de correos registrados."""
    enviados = []
    monkeypatch.setattr("app.main.gmail_sync.enviar_correo",
                        lambda to, asunto, cuerpo, adjunto=None: enviados.append(to))

    con_cuenta = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})
    sin_cuenta = client.post("/olvide-clave", data={"email": "nadie@gridworks.cl"})

    assert "te enviamos un enlace" in con_cuenta.text
    assert "te enviamos un enlace" in sin_cuenta.text
    assert enviados == ["rafael@gridworks.cl"]


def test_no_se_manda_un_segundo_enlace_seguido(client, usuario, monkeypatch):
    enviados = []
    monkeypatch.setattr("app.main.gmail_sync.enviar_correo",
                        lambda to, asunto, cuerpo, adjunto=None: enviados.append(to))

    client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})
    segundo = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})

    assert len(enviados) == 1
    assert "te enviamos un enlace" in segundo.text  # mismo mensaje, no se nota


def test_el_correo_no_tumba_el_flujo_si_el_smtp_falla(client, usuario, monkeypatch):
    def revienta(to, asunto, cuerpo, adjunto=None):
        raise RuntimeError("SMTP caido")

    monkeypatch.setattr("app.main.gmail_sync.enviar_correo", revienta)

    resp = client.post("/olvide-clave", data={"email": "rafael@gridworks.cl"})

    assert resp.status_code == 200
    assert "te enviamos un enlace" in resp.text


def test_restablecer_cambia_la_clave_y_cierra_las_sesiones(client, db, usuario):
    token = auth.crear_token_reset(usuario)

    resp = client.post(f"/restablecer/{token}",
                       data={"password": "clave-nueva-larga-2", "password2": "clave-nueva-larga-2"},
                       follow_redirects=False)

    assert resp.status_code == 303
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-nueva-larga-2")
    assert resultado is usuarios.Resultado.OK


def test_un_token_ya_usado_no_sirve_de_nuevo(client, usuario):
    token = auth.crear_token_reset(usuario)
    client.post(f"/restablecer/{token}",
                data={"password": "clave-nueva-larga-2", "password2": "clave-nueva-larga-2"})

    resp = client.get(f"/restablecer/{token}")

    assert "ya fue usado" in resp.text or "venció" in resp.text


def test_una_clave_corta_se_rechaza(client, db, usuario):
    token = auth.crear_token_reset(usuario)

    client.post(f"/restablecer/{token}", data={"password": "corta", "password2": "corta"})

    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "corta")
    assert resultado is usuarios.Resultado.CREDENCIALES_INVALIDAS


def test_dos_claves_distintas_se_rechazan(client, db, usuario):
    token = auth.crear_token_reset(usuario)

    client.post(f"/restablecer/{token}",
                data={"password": "clave-nueva-larga-2", "password2": "clave-distinta-3"})

    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-nueva-larga-2")
    assert resultado is usuarios.Resultado.CREDENCIALES_INVALIDAS
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_rutas.py -k "enlace or restablecer or token or clave" -v
```

Expected: FAIL — las rutas `/olvide-clave` y `/restablecer/...` devuelven 404.

- [ ] **Step 3: Agregar las rutas**

Agrega en `app/main.py`, después de la ruta `/logout`:

```python
MENSAJE_ENLACE = "Si el correo está registrado, te enviamos un enlace para restablecer tu clave."


def _enviar_enlace_reset(email: str, token: str) -> None:
    """Corre DESPUES de responder, no dentro del request. Recibe valores planos
    y no el objeto ORM, porque para cuando esto se ejecuta la sesion de base de
    datos ya se cerro.

    Silencia los errores de SMTP a proposito: si el correo no sale, la persona
    no debe enterarse por un 500, y el aviso queda en el log."""
    cuerpo = (
        f"Para definir tu clave de acceso al Libro de Compras, entra a este enlace:\n\n"
        f"{BASE_URL}/restablecer/{token}\n\n"
        f"El enlace vence en 30 minutos y sirve una sola vez.\n"
        f"Si no pediste esto, ignora el correo: tu clave actual sigue funcionando.\n"
    )
    try:
        gmail_sync.enviar_correo(email, "Restablecer tu clave · Libro de Compras", cuerpo)
    except Exception as e:
        print(f"[reset] No se pudo enviar el correo a {email}: {e}")


@app.get("/olvide-clave", response_class=HTMLResponse)
def olvide_clave_form(request: Request):
    return templates.TemplateResponse("olvide_clave.html", {"request": request, "usuario": None})


@app.post("/olvide-clave", response_class=HTMLResponse)
def olvide_clave_submit(request: Request, tareas: BackgroundTasks, email: str = Form(...),
                        db: Session = Depends(get_db)):
    u = usuarios.por_email(db, email)
    # El turno se toma AQUI, dentro del request, y el envio se agenda para
    # despues de responder. Si el correo se mandara aqui mismo, un correo con
    # cuenta tardaria lo que tarda el SMTP y uno sin cuenta contestaria al
    # instante: el mensaje seria el mismo pero el tiempo delataria cuales
    # existen, que es justo lo que este flujo trata de no revelar.
    if u and u.activo and usuarios.reclamar_envio_reset(db, u):
        tareas.add_task(_enviar_enlace_reset, u.email, crear_token_reset(u))
    # Respuesta identica exista o no la cuenta, y haya salido o no el correo.
    return templates.TemplateResponse(
        "olvide_clave.html", {"request": request, "usuario": None, "mensaje": MENSAJE_ENLACE}
    )


@app.get("/restablecer/{token}", response_class=HTMLResponse)
def restablecer_form(request: Request, token: str, db: Session = Depends(get_db)):
    u = leer_token_reset(token, db)
    if u is None:
        return templates.TemplateResponse(
            "restablecer.html",
            {"request": request, "usuario": None, "invalido": True},
            status_code=400,
        )
    return templates.TemplateResponse(
        "restablecer.html", {"request": request, "usuario": None, "token": token, "email": u.email}
    )


@app.post("/restablecer/{token}", response_class=HTMLResponse)
def restablecer_submit(request: Request, token: str, password: str = Form(...),
                       password2: str = Form(...), db: Session = Depends(get_db)):
    u = leer_token_reset(token, db)
    if u is None:
        return templates.TemplateResponse(
            "restablecer.html",
            {"request": request, "usuario": None, "invalido": True},
            status_code=400,
        )

    error = _validar_clave_nueva(password, password2)
    if error:
        return templates.TemplateResponse(
            "restablecer.html",
            {"request": request, "usuario": None, "token": token, "email": u.email, "error": error},
            status_code=400,
        )

    # cambiar_clave devuelve False si otra peticion redimio el mismo enlace
    # primero. En ese caso el enlace ya no sirve y hay que decirlo, no fingir
    # que se guardo.
    if not usuarios.cambiar_clave(db, u, password):
        return templates.TemplateResponse(
            "restablecer.html",
            {"request": request, "usuario": None, "invalido": True},
            status_code=400,
        )
    return RedirectResponse("/login", status_code=303)


def _validar_clave_nueva(password: str, password2: str):
    """Devuelve el mensaje de error, o None si esta bien."""
    if password != password2:
        return "Las dos claves no coinciden."
    if len(password) < LARGO_MINIMO_CLAVE:
        return f"La clave debe tener al menos {LARGO_MINIMO_CLAVE} caracteres."
    return None
```

- [ ] **Step 4: Crear `app/templates/olvide_clave.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="max-w-sm mx-auto mt-20 bg-white p-6 rounded-lg shadow">
  <h1 class="text-lg font-semibold mb-4">Restablecer clave</h1>
  {% if mensaje %}
    <p class="text-blue-700 text-sm mb-3">{{ mensaje }}</p>
    <a href="/login" class="text-sm text-gray-500 hover:underline">Volver a iniciar sesión</a>
  {% else %}
    <p class="text-sm text-gray-600 mb-3">
      Escribe tu correo y te enviamos un enlace para definir una clave nueva.
    </p>
    <form method="post" action="/olvide-clave">
      <input type="email" name="email" placeholder="Correo" required autofocus
             class="w-full border rounded px-3 py-2 mb-3" />
      <button type="submit" class="w-full bg-gray-900 text-white rounded px-3 py-2">
        Enviar enlace
      </button>
    </form>
    <p class="text-sm text-gray-500 mt-3">
      <a href="/login" class="hover:underline">Volver</a>
    </p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Crear `app/templates/restablecer.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="max-w-sm mx-auto mt-20 bg-white p-6 rounded-lg shadow">
  {% if invalido %}
    <h1 class="text-lg font-semibold mb-4">Enlace no válido</h1>
    <p class="text-sm text-gray-600 mb-4">
      Este enlace venció o ya fue usado. Los enlaces duran 30 minutos y sirven una sola vez.
    </p>
    <a href="/olvide-clave"
       class="block text-center bg-gray-900 text-white rounded px-3 py-2">Pedir otro enlace</a>
  {% else %}
    <h1 class="text-lg font-semibold mb-1">Define tu clave</h1>
    <p class="text-sm text-gray-500 mb-4">{{ email }}</p>
    {% if error %}
      <p class="text-red-600 text-sm mb-3">{{ error }}</p>
    {% endif %}
    <form method="post" action="/restablecer/{{ token }}">
      <input type="password" name="password" placeholder="Clave nueva" required autofocus
             class="w-full border rounded px-3 py-2 mb-3" />
      <input type="password" name="password2" placeholder="Repite la clave" required
             class="w-full border rounded px-3 py-2 mb-3" />
      <button type="submit" class="w-full bg-gray-900 text-white rounded px-3 py-2">Guardar</button>
    </form>
    <p class="text-xs text-gray-500 mt-3">Mínimo 10 caracteres.</p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 6: Correr**

```bash
venv/Scripts/python -m pytest tests/ -v
```

Expected: PASS, todas.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/templates/olvide_clave.html app/templates/restablecer.html tests/test_rutas.py
git commit -m "feat: recuperacion de clave por enlace de correo"
```

---

### Task 15: Cambiar la clave propia

**Files:**
- Modify: `app/main.py`
- Create: `app/templates/cuenta.html`
- Modify: `tests/test_rutas.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Agrega al final de `tests/test_rutas.py`:

```python
def test_cambiar_la_clave_propia(client, db, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.post("/cuenta", data={"actual": "clave-larga-1",
                                        "password": "clave-nueva-larga-2",
                                        "password2": "clave-nueva-larga-2"},
                       follow_redirects=False)

    assert resp.status_code == 303
    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-nueva-larga-2")
    assert resultado is usuarios.Resultado.OK


def test_cambiar_la_clave_reemite_mi_propia_cookie(client, usuario):
    """Subir token_version cierra las otras sesiones; si no se reemitiera la
    cookie, uno se echaria a si mismo al cambiar la clave."""
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    client.post("/cuenta", data={"actual": "clave-larga-1",
                                 "password": "clave-nueva-larga-2",
                                 "password2": "clave-nueva-larga-2"})

    assert client.get("/", follow_redirects=False).status_code == 200


def test_sin_la_clave_actual_no_se_cambia(client, db, usuario):
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    client.post("/cuenta", data={"actual": "equivocada",
                                 "password": "clave-nueva-larga-2",
                                 "password2": "clave-nueva-larga-2"})

    resultado, _ = usuarios.autenticar(db, "rafael@gridworks.cl", "clave-larga-1")
    assert resultado is usuarios.Resultado.OK
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_rutas.py -k cuenta -v
```

Expected: FAIL — `/cuenta` devuelve 404.

- [ ] **Step 3: Agregar las rutas**

Agrega en `app/main.py`, después de las rutas de restablecer:

```python
@app.get("/cuenta", response_class=HTMLResponse)
def cuenta_form(request: Request, usuario: Usuario = Depends(require_login)):
    return templates.TemplateResponse("cuenta.html", {"request": request, "usuario": usuario})


@app.post("/cuenta", response_class=HTMLResponse)
def cuenta_submit(request: Request, actual: str = Form(...), password: str = Form(...),
                  password2: str = Form(...), usuario: Usuario = Depends(require_login),
                  db: Session = Depends(get_db)):
    from app.security import verify_password

    error = None
    if not verify_password(actual, usuario.password_hash):
        error = "Tu clave actual no es correcta."
    else:
        error = _validar_clave_nueva(password, password2)

    if error:
        return templates.TemplateResponse(
            "cuenta.html", {"request": request, "usuario": usuario, "error": error}, status_code=400
        )

    if not usuarios.cambiar_clave(db, usuario, password):
        return templates.TemplateResponse(
            "cuenta.html",
            {"request": request, "usuario": usuario,
             "error": "Tu clave cambio desde otra pestaña. Vuelve a intentarlo."},
            status_code=409,
        )
    # cambiar_clave subio token_version: hay que reemitir la cookie propia para
    # no quedar afuera junto con las sesiones de los otros dispositivos.
    resp = RedirectResponse("/cuenta?ok=1", status_code=303)
    return _set_session(resp, usuario)
```

Y ajusta `cuenta_form` para que muestre el aviso de éxito:

```python
@app.get("/cuenta", response_class=HTMLResponse)
def cuenta_form(request: Request, ok: str = "", usuario: Usuario = Depends(require_login)):
    mensaje = "Tu clave quedó cambiada. Las sesiones en otros dispositivos se cerraron." if ok else None
    return templates.TemplateResponse(
        "cuenta.html", {"request": request, "usuario": usuario, "mensaje": mensaje}
    )
```

- [ ] **Step 4: Crear `app/templates/cuenta.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="max-w-sm mx-auto mt-10 bg-white p-6 rounded-lg shadow">
  <h1 class="text-lg font-semibold mb-1">Mi cuenta</h1>
  <p class="text-sm text-gray-500 mb-4">{{ usuario.email }}</p>

  {% if mensaje %}
    <p class="text-blue-700 text-sm mb-3">{{ mensaje }}</p>
  {% endif %}
  {% if error %}
    <p class="text-red-600 text-sm mb-3">{{ error }}</p>
  {% endif %}

  <form method="post" action="/cuenta">
    <input type="password" name="actual" placeholder="Clave actual" required
           class="w-full border rounded px-3 py-2 mb-3" />
    <input type="password" name="password" placeholder="Clave nueva" required
           class="w-full border rounded px-3 py-2 mb-3" />
    <input type="password" name="password2" placeholder="Repite la clave nueva" required
           class="w-full border rounded px-3 py-2 mb-3" />
    <button type="submit" class="w-full bg-gray-900 text-white rounded px-3 py-2">
      Cambiar clave
    </button>
  </form>
  <p class="text-xs text-gray-500 mt-3">
    Mínimo 10 caracteres. Al cambiarla se cierran las sesiones en otros dispositivos.
  </p>
</div>
{% endblock %}
```

- [ ] **Step 5: Correr**

```bash
venv/Scripts/python -m pytest tests/ -v
```

Expected: PASS, todas.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/cuenta.html tests/test_rutas.py
git commit -m "feat: cambiar la clave propia desde /cuenta"
```

---

### Task 16: Invitar y dar de baja

**Files:**
- Modify: `app/main.py`
- Create: `app/templates/usuarios.html`
- Modify: `tests/test_rutas.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Agrega al final de `tests/test_rutas.py`:

```python
def test_invitar_crea_la_cuenta_y_manda_el_enlace(client, db, usuario, monkeypatch):
    """La cuenta nace con una clave aleatoria que nadie ve: la persona define
    la suya por el enlace, asi ninguna clave viaja por mensajeria."""
    enviados = []
    monkeypatch.setattr("app.main.gmail_sync.enviar_correo",
                        lambda to, asunto, cuerpo, adjunto=None: enviados.append(to))
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    client.post("/usuarios", data={"accion": "invitar", "email": "contador@gridworks.cl",
                                   "nombre": "Contador"})

    assert usuarios.por_email(db, "contador@gridworks.cl") is not None
    assert enviados == ["contador@gridworks.cl"]


def test_no_se_puede_invitar_un_correo_repetido(client, db, usuario, monkeypatch):
    monkeypatch.setattr("app.main.gmail_sync.enviar_correo",
                        lambda to, asunto, cuerpo, adjunto=None: None)
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.post("/usuarios", data={"accion": "invitar", "email": "rafael@gridworks.cl",
                                          "nombre": "Otro"})

    assert "ya tiene cuenta" in resp.text
    assert db.query(usuarios.Usuario).filter_by(email="rafael@gridworks.cl").count() == 1


def test_dar_de_baja_deja_a_la_persona_afuera_en_el_acto(client, db, usuario):
    otro = usuarios.crear(db, "contador@gridworks.cl", clave="clave-larga-1")
    cookie_del_otro = auth.create_session_cookie(otro)
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    client.post("/usuarios", data={"accion": "desactivar", "usuario_id": str(otro.id)})

    client.cookies.set(auth.COOKIE_NAME, cookie_del_otro)
    assert client.get("/", follow_redirects=False).status_code == 303


def test_no_puedo_darme_de_baja_a_mi_mismo(client, db, usuario):
    """Evita quedarse sin ninguna cuenta activa."""
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_cookie(usuario))

    resp = client.post("/usuarios", data={"accion": "desactivar", "usuario_id": str(usuario.id)})

    assert "tu propia cuenta" in resp.text
    assert usuarios.por_email(db, "rafael@gridworks.cl").activo is True
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
venv/Scripts/python -m pytest tests/test_rutas.py -k "invitar or baja or mismo" -v
```

Expected: FAIL — `/usuarios` devuelve 404.

- [ ] **Step 3: Agregar las rutas**

Agrega en `app/main.py`, después de las rutas de cuenta:

```python
def _pantalla_usuarios(request, db, usuario, mensaje=None, error=None, status=200):
    filas = db.query(Usuario).order_by(Usuario.email).all()
    return templates.TemplateResponse(
        "usuarios.html",
        {"request": request, "usuario": usuario, "usuarios": filas,
         "mensaje": mensaje, "error": error},
        status_code=status,
    )


@app.get("/usuarios", response_class=HTMLResponse)
def usuarios_lista(request: Request, usuario: Usuario = Depends(require_login),
                   db: Session = Depends(get_db)):
    return _pantalla_usuarios(request, db, usuario)


@app.post("/usuarios", response_class=HTMLResponse)
def usuarios_accion(request: Request, tareas: BackgroundTasks, accion: str = Form(...),
                    email: str = Form(""), nombre: str = Form(""), usuario_id: str = Form(""),
                    usuario: Usuario = Depends(require_login), db: Session = Depends(get_db)):
    if accion == "invitar":
        if usuarios.por_email(db, email):
            return _pantalla_usuarios(request, db, usuario,
                                      error=f"{email} ya tiene cuenta.", status=400)
        nuevo = usuarios.crear(db, email, nombre=nombre)
        usuarios.reclamar_envio_reset(db, nuevo)  # cuenta recien creada: siempre gana el turno
        tareas.add_task(_enviar_enlace_reset, nuevo.email, crear_token_reset(nuevo))
        return _pantalla_usuarios(
            request, db, usuario,
            mensaje=f"Cuenta creada. Le enviamos a {nuevo.email} un enlace para definir su clave."
        )

    if accion == "desactivar":
        objetivo = db.query(Usuario).filter(Usuario.id == int(usuario_id or 0)).first()
        if objetivo is None:
            return _pantalla_usuarios(request, db, usuario, error="Cuenta no encontrada.", status=404)
        if objetivo.id == usuario.id:
            return _pantalla_usuarios(request, db, usuario,
                                      error="No puedes dar de baja tu propia cuenta.", status=400)
        usuarios.desactivar(db, objetivo)
        return _pantalla_usuarios(request, db, usuario, mensaje=f"{objetivo.email} quedó sin acceso.")

    return _pantalla_usuarios(request, db, usuario, error="Acción desconocida.", status=400)
```

- [ ] **Step 4: Crear `app/templates/usuarios.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1 class="text-lg font-semibold mb-4">Usuarios</h1>

{% if mensaje %}
  <div class="bg-blue-50 text-blue-800 text-sm rounded p-3 mb-4">{{ mensaje }}</div>
{% endif %}
{% if error %}
  <div class="bg-red-50 text-red-700 text-sm rounded p-3 mb-4">{{ error }}</div>
{% endif %}

<div class="bg-white rounded-lg shadow mb-6 p-4">
  <h2 class="text-sm font-semibold text-gray-700 mb-3">Invitar</h2>
  <form method="post" action="/usuarios" class="flex flex-wrap gap-2 items-center">
    <input type="hidden" name="accion" value="invitar" />
    <input type="email" name="email" placeholder="Correo" required
           class="border rounded px-3 py-2 flex-1 min-w-48" />
    <input type="text" name="nombre" placeholder="Nombre (opcional)"
           class="border rounded px-3 py-2 flex-1 min-w-48" />
    <button type="submit" class="bg-gray-900 text-white rounded px-4 py-2">Invitar</button>
  </form>
  <p class="text-xs text-gray-500 mt-2">
    Se le envía un enlace para que defina su clave. Nadie escribe claves por otro.
  </p>
</div>

<table class="w-full bg-white rounded-lg shadow overflow-hidden text-sm">
  <thead class="bg-gray-100 text-gray-600 text-left">
    <tr>
      <th class="px-4 py-3">Correo</th>
      <th class="px-4 py-3">Nombre</th>
      <th class="px-4 py-3">Último ingreso</th>
      <th class="px-4 py-3">Estado</th>
      <th class="px-4 py-3">Acción</th>
    </tr>
  </thead>
  <tbody>
    {% for u in usuarios %}
    <tr class="border-t">
      <td class="px-4 py-3">{{ u.email }}</td>
      <td class="px-4 py-3">{{ u.nombre or '' }}</td>
      <td class="px-4 py-3">{{ u.ultimo_ingreso.strftime('%d-%m-%Y %H:%M') if u.ultimo_ingreso else 'nunca' }}</td>
      <td class="px-4 py-3">
        {% if u.activo %}
          <span class="bg-green-100 text-green-800 rounded px-2 py-1 text-xs">activa</span>
        {% else %}
          <span class="bg-gray-200 text-gray-600 rounded px-2 py-1 text-xs">sin acceso</span>
        {% endif %}
      </td>
      <td class="px-4 py-3">
        {% if u.activo and u.id != usuario.id %}
        <form method="post" action="/usuarios"
              onsubmit="return confirm('¿Dar de baja a {{ u.email }}? Queda sin acceso de inmediato.')">
          <input type="hidden" name="accion" value="desactivar" />
          <input type="hidden" name="usuario_id" value="{{ u.id }}" />
          <button type="submit" class="text-red-600 hover:underline">Dar de baja</button>
        </form>
        {% else %}
          <span class="text-gray-400">—</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Correr la suite completa**

```bash
venv/Scripts/python -m pytest tests/ -v
```

Expected: PASS, todas.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/usuarios.html tests/test_rutas.py
git commit -m "feat: invitar y dar de baja usuarios"
```

---

### Task 17: Prueba de humo en local

No es TDD: es levantar la aplicación de verdad y usarla, porque las pruebas no cubren que el correo salga ni que los templates se vean bien.

**Files:** ninguno (solo verificación)

- [ ] **Step 1: Poner las variables de arranque en `.env`**

Agrega al `.env` local:

```
ADMIN_EMAIL=rafael.fuenzalida@gmail.com
ADMIN_PASSWORD=una-clave-larga-de-prueba
BASE_URL=http://localhost:8000
```

Y borra la línea `APP_PASSWORD=...`, que ya no se usa.

- [ ] **Step 2: Levantar**

```bash
venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Expected en el log: `[arranque] Cuenta inicial creada: rafael.fuenzalida@gmail.com`.

- [ ] **Step 3: Recorrer el flujo en el navegador**

Verifica cada uno:

1. `http://localhost:8000/` redirige a `/login`
2. Entrar con la clave equivocada muestra "Correo o clave incorrectos."
3. Entrar bien lleva al tablero, y arriba a la derecha aparece el correo
4. `/cuenta` cambia la clave y sigues dentro
5. `/usuarios` lista tu cuenta, sin botón de baja en tu propia fila
6. Invitar a un correo tuyo de prueba: llega el correo con el enlace
7. El enlace abre el formulario, define la clave, y esa cuenta entra
8. Pedir un segundo enlace enseguida: mismo mensaje, no llega otro correo
9. Salir y volver a `/` redirige a `/login`

- [ ] **Step 4: Commit si hubo arreglos**

```bash
git add -A
git commit -m "fix: ajustes de la prueba de humo del login"
```

---

### Task 18: Documentación y despliegue

**Files:**
- Modify: `README.md`
- Modify: `CONTEXT.md`

- [ ] **Step 1: Documentar las variables en `README.md`**

En la sección de variables de entorno, quita `APP_PASSWORD` y agrega:

```markdown
| `ADMIN_EMAIL` | Correo de la primera cuenta. Solo se usa si la tabla `usuarios` está vacía. |
| `ADMIN_PASSWORD` | Clave de la primera cuenta. Cámbiala al entrar y borra ambas variables. |
| `BASE_URL` | URL pública, para el enlace del correo de recuperación. En Railway: `https://conta.gridworks.cl` |
| `COOKIE_SECURE` | `true` en Railway. Obligatorio ahora que viajan credenciales reales. |
```

Y agrega esta sección:

```markdown
## Acceso

Cada persona entra con su correo y su clave. Las cuentas se crean desde
`/usuarios`, que le envía a la persona un enlace para que defina su propia clave
— nadie escribe claves por otro.

### Primer despliegue

El orden importa, o te quedas afuera:

1. Poner `ADMIN_EMAIL` y `ADMIN_PASSWORD` en Railway
2. Desplegar (la tabla `usuarios` se crea sola)
3. Entrar con esas credenciales
4. Cambiar la clave desde `/cuenta`
5. Borrar `ADMIN_EMAIL` y `ADMIN_PASSWORD` de Railway

Las sesiones abiertas se cortan en el despliegue: la cookie del formato viejo
deja de ser válida. Hay que entrar de nuevo.

### Si te quedas sin acceso

Borra la tabla `usuarios` en Postgres, pon `ADMIN_EMAIL` y `ADMIN_PASSWORD` de
nuevo, y reinicia. La siembra vuelve a correr porque la tabla está vacía.
```

- [ ] **Step 2: Actualizar `CONTEXT.md`**

Marca como resuelto el pendiente del dominio si corresponde, y agrega a la lista de pendientes:

```markdown
- Autenticación: cuentas individuales con correo y clave, con recuperación por
  correo (ver `docs/superpowers/specs/2026-08-03-login-correo-clave-design.md`).
  Ya no existe `APP_PASSWORD` ni el modo abierto cuando la variable falta.
```

- [ ] **Step 3: Correr la suite completa una última vez**

```bash
venv/Scripts/python -m pytest tests/ -v
```

Expected: PASS, todas.

- [ ] **Step 4: Commit**

```bash
git add README.md CONTEXT.md
git commit -m "docs: acceso con correo y clave, y orden del primer despliegue"
```

---

## Checklist de despliegue a Railway

Después de mergear, en este orden:

- [ ] Poner `ADMIN_EMAIL` y `ADMIN_PASSWORD` en las variables de Railway
- [ ] Poner `BASE_URL=https://conta.gridworks.cl`
- [ ] Verificar que `COOKIE_SECURE=true` esté puesto
- [ ] Verificar que `SECRET_KEY` no sea `dev-secret-change-me` — si lo es, poner una de verdad ahora, porque de ella depende que las cookies no se puedan falsificar
- [ ] Desplegar
- [ ] Revisar el log: debe decir `[arranque] Cuenta inicial creada: ...`
- [ ] Entrar y cambiar la clave desde `/cuenta`
- [ ] Invitar al contador desde `/usuarios`
- [ ] Borrar `ADMIN_EMAIL` y `ADMIN_PASSWORD` de Railway
- [ ] Borrar `APP_PASSWORD` de Railway
