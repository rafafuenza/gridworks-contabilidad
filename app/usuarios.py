"""Operaciones sobre la tabla de usuarios. Este modulo no conoce cookies ni
HTTP: la capa web decide que hacer con los resultados que devuelve."""

from datetime import timedelta
from enum import Enum

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import ADMIN_EMAIL, ADMIN_PASSWORD
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

    # Responde rapido, sin hashear, cuando la cuenta ya esta bloqueada. Es a
    # proposito: hashear en este camino le regalaria a quien ataca 650 ms y
    # 64 MB de trabajo por cada request ya bloqueado, lo que es peor que la
    # fuga de informacion (que una cuenta bloqueada existe, tras 5 intentos).
    # El semaforo de app/security.py agranda esta brecha bajo carga: los dos
    # caminos que si hashean (correo inexistente y clave mala) siguen tardando
    # lo mismo entre si porque ambos esperan el mismo permiso, pero el camino
    # BLOQUEADO no pide permiso y responde de inmediato, asi que con el
    # semaforo ocupado la diferencia pasa de ~650 ms a varios segundos. Se
    # acepta: no hashear en este camino sigue siendo la opcion correcta.
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


def cambiar_clave(db: Session, u: Usuario, nueva: str) -> bool:
    """Cambia la clave y sube token_version, lo que mata el enlace de
    recuperacion usado y cierra las sesiones abiertas de esta cuenta.

    Devuelve False si otra peticion se adelanto con el mismo enlace. El UPDATE
    es condicional a la version que se leyo: sin eso, dos redenciones del mismo
    token en paralelo pasarian las dos y ganaria la ultima en escribir.
    reset_enviado_en se deja tal cual a proposito: no es necesario limpiarlo
    para que el freno funcione."""
    version_leida = u.token_version
    filas = (
        db.query(Usuario)
        .filter(Usuario.id == u.id, Usuario.token_version == version_leida)
        .update(
            {
                Usuario.password_hash: hash_password(nueva),
                Usuario.token_version: version_leida + 1,
                Usuario.intentos_fallidos: 0,
                Usuario.bloqueado_hasta: None,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.refresh(u)
    return filas == 1


def desactivar(db: Session, u: Usuario) -> None:
    u.activo = False
    u.token_version = (u.token_version or 1) + 1
    db.commit()


def reclamar_envio_reset(db: Session, u: Usuario) -> bool:
    """Toma el turno para enviarle un enlace a esta cuenta. Devuelve True solo
    si esta llamada gano el turno; el que pierde no debe enviar nada.

    El chequeo y la marca van en un solo UPDATE condicional a proposito: si se
    consultara primero y se marcara despues, varios pedidos en paralelo leerian
    todos el mismo valor viejo y saldrian todos los correos. Ademas se marca
    antes de enviar, no despues: si el SMTP falla, es preferible que la persona
    espere el turno a que el freno desaparezca justo cuando el correo anda mal."""
    limite = ahora_utc() - timedelta(minutes=RESET_ESPERA_MINUTOS)
    filas = (
        db.query(Usuario)
        .filter(
            Usuario.id == u.id,
            or_(Usuario.reset_enviado_en.is_(None), Usuario.reset_enviado_en <= limite),
        )
        .update({Usuario.reset_enviado_en: ahora_utc()}, synchronize_session=False)
    )
    db.commit()
    db.refresh(u)
    return filas == 1


def sembrar_admin_inicial(db: Session):
    """Crea la primera cuenta desde el entorno, solo si no hay ninguna. Devuelve
    el usuario creado o None. Una vez creada, las variables se pueden borrar.

    Se exigen las dos variables: con solo el correo se crearia una cuenta con
    una clave al azar que nadie conoce, lo que se ve como exito y no lo es."""
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        return None
    if db.query(Usuario).count() > 0:
        return None
    return crear(db, ADMIN_EMAIL, nombre="Admin", clave=ADMIN_PASSWORD)
