"""Hash de contrasenas con Argon2id.

El hash vive aqui y no en el modelo ni en la vista, porque es el unico punto
donde se decide con que parametros se hashea una contrasena. Cambiar esos
parametros mas adelante obliga a rehashear: por eso se declaran como constantes
con nombre y no como numeros sueltos en la llamada.

**Por que Argon2id y no bcrypt o PBKDF2.** Argon2id es la unica de las tres que
resiste tanto el ataque con GPU como el ataque de canal lateral, y es la que
recomienda la guia de OWASP. bcrypt tiene un techo de 72 bytes de entrada y
PBKDF2 necesita mas iteraciones para alcanzar el mismo coste.

**Por que `verify` devuelve `False` y no lanza.** Una cuenta con el hash corrupto
o de una version anterior no puede distinguished de una contrasena incorrecta
desde fuera, y no debe distinguirse: en los dos casos la respuesta al que
intenta entrar es la misma.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

#: Coste de la operacion. Los tres numeros vienen juntos y no se tocan por
#: separado: `memory_cost` en KiB, `time_cost` en pasadas y `parallelism` en
#: hilos. Son los valores que recomienda OWASP para Argon2id, y estan por debajo
#: de lo que un atacante dedicate de proposito, que es justamente lo que se busca.
#:
#: Subirlos es barato en desarrollo y caro en produccion: cada registro paga los
#: 64 MiB y el tiempo de las tres pasadas. Por eso estan en una constante
#: documentada y no repartidos por el codigo.
COSTO_ARGON2ID = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

#: El hash lleva dentro sus propios parametros y su sal, asi que verificar no
#: necesita saber con que se genero. Guardar parametros aparte en la base
#: obligaria a cambiarlos de forma sincronizada con cada hash ya escrito.
LARGO_MAXIMO_HASH: int = 255


def hash_password(password: str) -> str:
    """Hashea una contrasena con Argon2id y una sal nueva.

    La sal la genera `argon2-cffi` por hash. No se reutiliza: dos cuentas con la
    misma contrasena tienen que dar hashes distintos, o una tabla de hashes
    delata que dos usuarios eligieron lo mismo.
    """
    return COSTO_ARGON2ID.hash(password)


def verify_password(hash_guardado: str, password: str) -> bool:
    """Comprueba una contrasena contra su hash.

    Devuelve `False` tambien cuando el hash esta corrupto o usa parametros que
    esta instalacion no soporta. Traducir eso a un error dejaria distinguir "el
    hash esta roto" de "la contrasena no es" solo a quien pueda provocarlo.
    """
    try:
        return COSTO_ARGON2ID.verify(hash_guardado, password)
    except (VerificationError, InvalidHashError):
        return False
