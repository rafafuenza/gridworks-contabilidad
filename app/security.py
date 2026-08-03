"""Hasheo de claves. Este modulo no conoce la base de datos ni el request:
solo convierte texto plano en un hash y verifica el par.

Se usa hashlib.scrypt de la biblioteca estandar en vez de bcrypt o argon2
para no sumar una dependencia con binarios compilados."""

import base64
import hashlib
import hmac
import secrets

_ALGORITMO = "scrypt"
_N = 2 ** 14      # costo de CPU/memoria: ~16 MB por hasheo
_R = 8
_P = 1
_DKLEN = 32
_MAXMEM = 64 * 1024 * 1024  # holgura sobre los 16 MB que pide n=2**14


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
