"""DB-д хадгалагдах нууц утгын шифрлэлт (Fernet, симметр түлхүүр .env-д).

Юуг хамгаалдаг: зогсоол бүрийн QPay client_secret (parking_sites.qpay_password),
төхөөрөмжийн нэвтрэх нууц үг (devices.password). Эдгээр өмнө нь Postgres болон
pg_dump backup-д ил бичигдэж байсан.

Зарчим:
  • PARKING_SECRET_ENC_KEY хоосон бол юу ч өөрчлөгдөхгүй (шифрлэхгүй, хуучин зан төлөв).
  • Бичихдээ encrypt_secret() — "enc:" угтвартай Fernet токен болгоно.
  • Уншихдаа decrypt_secret() — "enc:" угтвартайг тайлна; угтваргүй хуучин
    plaintext утгыг байгаагаар нь буцаана (шилжилтийн үед хоёул зэрэг ажиллана).
  • Тайлж чадахгүй бол (түлхүүр буруу/алга) ЧАНГА алдаа шиднэ — MONNIS шиг
    тусдаа данстай зогсоолын төлбөр глобал данс руу ЧИМЭЭГҮЙ унахаас сэргийлнэ.

Түлхүүр үүсгэх: venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Хуучин мөрүүдийг шифрлэх: venv/bin/python tools/encrypt_secrets.py (repo root-оос)
"""
import logging

from .config import settings

log = logging.getLogger("parking.secretbox")

_PREFIX = "enc:"


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(settings.secret_enc_key.encode())


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(_PREFIX))


def encrypt_secret(value: str | None) -> str | None:
    """Хадгалахын өмнө шифрлэнэ. Түлхүүргүй бол байгаагаар нь (нийцтэй горим)."""
    if not value or not settings.secret_enc_key:
        return value
    if is_encrypted(value):
        return value  # аль хэдийн шифрлэгдсэн — давхар шифрлэхгүй
    return _PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str:
    """Хэрэглэхийн өмнө тайлна. Plaintext (хуучин) утгыг байгаагаар нь буцаана."""
    if not value:
        return ""
    if not is_encrypted(value):
        return value
    if not settings.secret_enc_key:
        raise ValueError("Шифрлэгдсэн нууц утга байна, гэвч PARKING_SECRET_ENC_KEY "
                         "тохируулаагүй — .env-ээ шалгана уу")
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken as e:
        log.error("Нууц утгыг тайлж чадсангүй — PARKING_SECRET_ENC_KEY буруу эсвэл солигдсон")
        raise ValueError("Нууц утгыг тайлж чадсангүй (түлхүүр зөрсөн)") from e
