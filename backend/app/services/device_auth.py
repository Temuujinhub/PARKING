"""Төхөөрөмжийн нэвтрэх мэдээлэл — нэг эх сурвалж.

Зогсоол бүрийн камерууд өөр өөр нууц үгтэй байж болно (өөр өөр байгууллага
суурилуулдаг). Тиймээс Device.username/password талбарууд нэмэгдсэн; хоосон
үлдээвэл .env-ийн глобал утга үйлчилнэ — өмнөх зан төлөв бүрэн хадгалагдана.

ЧУХАЛ: creds-ийг ҮРГЭЛЖ db session амьд байхад (энгийн мөр болгож) шийдэж
дараа нь background task руу дамжуулна. Device ORM обьектыг task руу шидвэл
session хаагдсаны дараа DetachedInstanceError өгнө.
"""
from ..config import settings

Creds = tuple[str, str]


def camera_credentials(device=None) -> Creds:
    """Камерын CGI/RPC нэвтрэлт: төхөөрөмжийнх → .env глобал."""
    user = (getattr(device, "username", None) or "").strip()
    pwd = (getattr(device, "password", None) or "").strip()
    return (user or settings.camera_username, pwd or settings.camera_password)


def barrier_credentials(device=None) -> Creds:
    """Хаалтны RPC2 нэвтрэлт: төхөөрөмжийнх → .env barrier_* → .env camera_*.

    Dahua ANPR кит дээр хаалт нь камерынхаа релеэр ажилладаг тул ихэвчлэн
    камерын нэвтрэлттэй ижил."""
    user = (getattr(device, "username", None) or "").strip()
    pwd = (getattr(device, "password", None) or "").strip()
    return (user or settings.barrier_username or settings.camera_username,
            pwd or settings.barrier_password or settings.camera_password)
