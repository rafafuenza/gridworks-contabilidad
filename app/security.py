"""Hasheo de claves. Este modulo no conoce la base de datos ni el request:
solo convierte texto plano en un hash y verifica el par.

Se usa hashlib.scrypt de la biblioteca estandar en vez de bcrypt o argon2
para no sumar una dependencia con binarios compilados."""

import base64
import hashlib
import hmac
import secrets
import unicodedata

_ALGORITMO = "scrypt"
_N = 2 ** 16      # costo de CPU/memoria: 64 MB por hasheo (128*r*N)
_R = 8
_P = 1
_DKLEN = 32
_MAXMEM = 256 * 1024 * 1024  # holgura real sobre los 64 MB que pide n=2**16
# no usamos 2**17 (128 MB/hash, el piso de OWASP): esto corre en un contenedor
# chico de Railway para 2-3 usuarios, y 64 MB es el punto mas seguro ahi


def hash_password(plain: str) -> str:
    """Devuelve 'scrypt$n$r$p$salt_b64$hash_b64'. El formato lleva sus propios
    parametros para poder subirlos mas adelante sin invalidar los hashes viejos."""
    plain = unicodedata.normalize("NFC", plain)
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
        n, r, p = int(n), int(r), int(p)
        salt = base64.b64decode(salt_b64)
        esperado = base64.b64decode(hash_b64)
        # defensa contra una fila manipulada: no dejar que n/r/p disparen una
        # asignacion de memoria enorme antes de llamar a scrypt
        if not (2 ** 12 <= n <= 2 ** 17 and 1 <= r <= 16 and 1 <= p <= 4 and len(esperado) == _DKLEN):
            return False
        plain = unicodedata.normalize("NFC", plain)
        dk = hashlib.scrypt(plain.encode("utf-8"), salt=salt, n=n, r=r,
                            p=p, dklen=len(esperado), maxmem=_MAXMEM)
    except ValueError:
        return False
    return hmac.compare_digest(dk, esperado)


def generar_clave_aleatoria(n_bytes: int = 16) -> str:
    """Para invitaciones: se crea la cuenta con una clave que nadie ve nunca,
    y la persona define la suya por el enlace de correo.

    n_bytes son bytes de entropia, no caracteres: con el default de 16
    devuelve un string de aproximadamente 22 caracteres."""
    return secrets.token_urlsafe(n_bytes)
