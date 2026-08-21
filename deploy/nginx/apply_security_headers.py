#!/usr/bin/env python3
"""nginx vhost-д хамгаалалтын header-ийн snippet-ийг БҮХ location блокт тарааж
байрлуулна (idempotent). Аюулгүй байдлын хатууруулалтын ЗЭРЭГ ЭРСДЭЛГҮЙ хэсэг.

ЯАГААД СКРИПТ ВЭ: nginx-д `add_header` УДАМШДАГГҮЙ — location блокт өөрийн
add_header байвал эцгийн бүх header алга болно. Тиймээс snippet-ийг location
БҮРД давтаж `include` хийх шаардлагатай бөгөөд үүнийг гараар хийхэд мартагдана
(2026-08-20-нд яг ийм шалтгаанаар /assets/ бүх header-ээ алдсан байсан).

Хийдэг зүйл:
  1) `server {` ба `location ... {` блок бүрийн ЭХЭНД include мөр оруулна
  2) НЭГ МӨРТ location блокт (`location ~ ... { return 444; }`) хүрэхгүй
  3) Аль хэдийн include хийсэн блокийг давхардуулахгүй
  4) Нөөцийг /var/backups/parking-nginx/ руу бичнэ — nginx-ийн include зам
     (`sites-enabled/*`) нь өргөтгөлөөр шүүдэггүй тул нөөцийг ТЭНД үлдээвэл
     nginx түүнийг ч ачаалж server блок давхардана (2026-08-21 прод осол)

`server_tokens off;` энд ОРУУЛАХГҮЙ — http түвшний тунхагтай давхцаж
"directive is duplicate" болдог. Түүнийг /etc/nginx/nginx.conf-д гараар нээнэ.

Файлыг ӨӨРЧЛӨХ ЗӨВХӨН --write өгсөн үед. Анхдагчаар зөвхөн diff харуулна.

    python3 apply_security_headers.py /etc/nginx/sites-enabled/parking
    python3 apply_security_headers.py /etc/nginx/sites-enabled/parking --write
"""
import os
import pathlib
import re
import shutil
import sys
from datetime import datetime

INCLUDE = "include snippets/parking-security.conf;"
BLOCK_RE = re.compile(r"^(\s*)(?:location\s+.*|server\s*)\{\s*$")
SERVER_ONLY_RE = re.compile(r"^(\s*)server\s*\{\s*$")


def _strip(line: str) -> str:
    """Мөрийг тайлбаргүй, зайгүй болгож харьцуулахад бэлдэнэ."""
    return line.split("#", 1)[0].strip()


def _depths(lines: list[str]) -> list[int]:
    """Мөр БҮРИЙН ӨМНӨХ хаалтны гүн — блокийн ӨӨРИЙН биеийг дэд блокоос ялгана."""
    out, d = [], 0
    for line in lines:
        out.append(d)
        code = _strip(line)
        d += code.count("{") - code.count("}")
    return out


# Snippet өөрөө өгдөг header-үүд. Vhost-д ГАРААР бичигдсэн эдгээр мөр үлдвэл
# нэг блокт ХОЁР add_header болж, хариунд header ХОЁР УДАА явна (nginx-д нэг
# контекст доторх add_header нь ДАРДАГГҮЙ, НЭМДЭГ). 2026-08-21 Monnis дээр
# «X-Frame-Options ×2, HSTS ×2 (нэг нь includeSubDomains-гүй)» гэж илэрсэн.
MANAGED = ("x-frame-options", "x-content-type-options", "referrer-policy",
           "strict-transport-security", "permissions-policy",
           "content-security-policy", "content-security-policy-report-only")


def _is_managed_header(line: str) -> bool:
    """Мөр нь ЗӨВХӨН удирддаг add_header-ийн тунхаг мөн үү (хаалт агуулаагүй)."""
    code = _strip(line)
    if not code.lower().startswith("add_header") or "{" in code or "}" in code:
        return False
    parts = code.split(None, 2)
    return len(parts) >= 2 and parts[1].strip('"\'').lower() in MANAGED


def _has_in_body(lines: list[str], depth: list[int], i: int, needle: str) -> bool:
    """i-р мөрөөр нээгдсэн блокийн ӨӨРИЙН биед (дэд блокт БИШ) needle байгаа эсэх."""
    for j in range(i + 1, len(lines)):
        if depth[j] <= depth[i]:
            return False                 # блок хаагдлаа
        if depth[j] == depth[i] + 1 and _strip(lines[j]) == needle:
            return True
    return False


def transform(lines: list[str], extra: tuple[str, ...] = ()) -> tuple[list[str], int]:
    """extra — server блокт нэмж холбох snippet-ийн НЭР (scanner-guard г.м).
    Тэдгээр нь `location` тунхаг агуулдаг тул ЗӨВХӨН server контекстэд орно."""
    depth = _depths(lines)
    insert_at: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = BLOCK_RE.match(line)
        if not m:
            continue
        if extra and SERVER_ONLY_RE.match(line):
            for name in extra:
                inc = f"include snippets/{name};"
                if not _has_in_body(lines, depth, i, inc):
                    insert_at.append((i + 1, f"{m.group(1)}    {inc}\n"))
        if not _has_in_body(lines, depth, i, INCLUDE):
            insert_at.append((i + 1, f"{m.group(1)}    {INCLUDE}\n"))

    out = list(lines)
    for pos, text in reversed(insert_at):   # ард талаас — индекс гулсахгүй
        out.insert(pos, text)
    # Snippet-ийн өгдөг header-ийн ГАРААР бичсэн хуулбаруудыг хасна
    kept, dropped = [], 0
    for ln in out:
        if _is_managed_header(ln):
            dropped += 1
            continue
        kept.append(ln)
    return kept, len(insert_at) + dropped


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    write = "--write" in sys.argv[2:]
    extra: tuple[str, ...] = ()
    for a in sys.argv[2:]:
        if a.startswith("--with="):
            extra = tuple(x.strip() for x in a[7:].split(",") if x.strip())

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    new, added = transform(lines, extra)

    if added == 0:
        print(f"✓ {path}: аль хэдийн бүрэн хатууруулсан — өөрчлөх зүйл алга")
        return 0

    if not write:
        print(f"— {path}: {added} мөр нэмэгдэнэ (--write өгвөл бичнэ):\n")
        import difflib
        sys.stdout.writelines(difflib.unified_diff(lines, new, "одоогийн", "шинэ"))
        return 0

    bdir = pathlib.Path(os.environ.get("PARKING_NGINX_BACKUP_DIR", "/var/backups/parking-nginx"))
    bdir.mkdir(parents=True, exist_ok=True)
    backup = str(bdir / f"{pathlib.Path(path).name}.backup-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(new)
    print(f"✓ {path}: {added} мөр нэмэв.  Нөөц: {backup}")
    print(f"  Буцаах бол:  cp {backup} {path} && systemctl reload nginx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
