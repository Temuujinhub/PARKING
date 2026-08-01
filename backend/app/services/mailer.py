"""И-мэйл илгээх (SMTP) — байгууллагын сарын нэхэмжлэл зэрэг албан илгээлтэд.

Тохиргоо: .env-ийн PARKING_SMTP_* (config.py). smtplib нь SYNC тул async
контекстээс asyncio.to_thread-ээр дуудна — event loop-ийг блоклохгүй.
"""
import logging
import smtplib
from email.message import EmailMessage

from ..config import settings

log = logging.getLogger("parking.mailer")


def smtp_ready() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def send_mail(to: str, subject: str, body: str,
              attachment: bytes | None = None, attachment_name: str = "") -> None:
    """Нэг и-мэйл илгээнэ (хавсралттай байж болно). Алдаа гарвал exception шиднэ —
    дуудагч нь хэрэглэгчид ойлгомжтой мессеж болгож буцаана."""
    if not smtp_ready():
        raise RuntimeError("SMTP тохируулаагүй байна (.env-д PARKING_SMTP_HOST/USER/PASSWORD)")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(
            attachment, maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=attachment_name or "invoice.xlsx")
    if settings.smtp_tls and settings.smtp_port != 465:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    log.info(f"и-мэйл илгээгдлээ → {to}: {subject}")
