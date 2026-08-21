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


def transform(lines: list[str]) -> tuple[list[str], int]:
    depth = _depths(lines)
    insert_at: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = BLOCK_RE.match(line)
        if not m:
            continue
        body = depth[i] + 1
        # Блокийн ӨӨРИЙН биед (дэд location-д БИШ) include аль хэдийн бий юу
        already = False
        for j in range(i + 1, len(lines)):
            if depth[j] <= depth[i]:
                break                      # блок хаагдлаа
            if depth[j] == body and _strip(lines[j]) == INCLUDE:
                already = True
                break
        if not already:
            insert_at.append((i + 1, f"{m.group(1)}    {INCLUDE}\n"))

    out = list(lines)
    for pos, text in reversed(insert_at):   # ард талаас — индекс гулсахгүй
        out.insert(pos, text)
    return out, len(insert_at)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    write = "--write" in sys.argv[2:]

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    new, added = transform(lines)

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
