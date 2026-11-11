"""Зургийн retention (2026-11-11): 20GB-ийн таг → 400GB (прод диск 1TB), дискний 60%-иас
хэтрэхгүй; диск дүүрэхэд хамгийн хуучнаас устгах (сүүлийн 24ц-ийнхыг хамгаална).

  1. Хугацаа (120 хоног) → таг → дискний яаралтай зай гэсэн дараалал
  2. Таг: min(400GB, дискний 60%)
  3. Яаралтай: сүүлийн protect цагийн зургийг ХЭЗЭЭ Ч устгахгүй
"""
import os
import time
from types import SimpleNamespace

from app.config import settings
from app.services import auto_close as AC

DAY = 86400
GB = 1024 ** 3


def _entries(now, ages_days, size=100):
    return [(now - a * DAY, size, f"/s/{i}.jpg") for i, a in enumerate(ages_days)]


def test_age_rule_first():
    now = 1_000_000_000.0
    doomed, stats = AC.plan_snapshot_prune(_entries(now, [130, 121, 5, 1]), now_ts=now, days=120,
                                           cap_bytes=0)
    assert [p for p, _ in doomed] == ["/s/0.jpg", "/s/1.jpg"] and stats["age"] == 2


def test_cap_deletes_oldest_until_under():
    now = 1_000_000_000.0
    doomed, stats = AC.plan_snapshot_prune(_entries(now, [10, 9, 8, 7, 6], size=100), now_ts=now,
                                           days=120, cap_bytes=250)
    assert [p for p, _ in doomed] == ["/s/0.jpg", "/s/1.jpg", "/s/2.jpg"]
    assert stats == {"age": 0, "cap": 3, "disk": 0}


def test_disk_emergency_respects_protected_recent():
    now = 1_000_000_000.0
    # 5 хоногийн, 3 хоногийн, 12 цагийн, 1 цагийн зураг; 1000 байт яаралтай хэрэгтэй
    entries = [(now - 5 * DAY, 100, "/old5"), (now - 3 * DAY, 100, "/old3"),
               (now - 12 * 3600, 100, "/h12"), (now - 3600, 100, "/h1")]
    doomed, stats = AC.plan_snapshot_prune(entries, now_ts=now, days=120, cap_bytes=0,
                                           free_deficit=1000, protect_sec=24 * 3600)
    assert [p for p, _ in doomed] == ["/old5", "/old3"]          # 24ц-ийнх хөндөгдөөгүй
    assert stats["disk"] == 2


def test_disk_emergency_stops_when_enough():
    now = 1_000_000_000.0
    doomed, stats = AC.plan_snapshot_prune(_entries(now, [9, 8, 7, 6], size=100), now_ts=now,
                                           days=120, cap_bytes=0, free_deficit=150, protect_sec=0)
    assert len(doomed) == 2 and stats["disk"] == 2


def test_cap_400gb_bounded_by_disk(monkeypatch, tmp_path):
    import shutil
    disk = {"total": 1000 * 10 ** 9}   # 1TB (прод)
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: SimpleNamespace(total=disk["total"], used=0, free=disk["total"]))
    monkeypatch.setattr(settings, "retention_snapshot_max_gb", 400.0)
    monkeypatch.setattr(settings, "retention_snapshot_max_disk_percent", 60.0)
    assert AC.snapshot_cap_bytes(str(tmp_path)) == int(400 * GB)          # 1TB → 400GB
    disk["total"] = 100 * GB                                                # жижиг TEST диск
    assert AC.snapshot_cap_bytes(str(tmp_path)) == int(60 * GB)           # дискний 60%
    monkeypatch.setattr(settings, "retention_snapshot_max_gb", 0.0)
    assert AC.snapshot_cap_bytes(str(tmp_path)) == int(60 * GB)           # зөвхөн %
    monkeypatch.setattr(settings, "retention_snapshot_max_disk_percent", 0)
    assert AC.snapshot_cap_bytes(str(tmp_path)) == 0                      # хязгааргүй


def test_defaults_400gb():
    from app.config import Settings
    assert Settings.model_fields["retention_snapshot_max_gb"].default == 400.0
    assert Settings.model_fields["retention_snapshot_max_disk_percent"].default == 60.0
    assert Settings.model_fields["retention_snapshot_protect_hours"].default == 24


def test_prune_snapshots_on_disk(monkeypatch, tmp_path):
    """Бодит файлууд: 130 хоногийнх (хугацаа) + таг 1 файлын хэмжээ."""
    now = time.time()
    day = tmp_path / "20260101"
    day.mkdir()
    files = []
    for i, age in enumerate([130, 3, 2, 1]):
        f = day / f"P{i}_entry.jpg"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (now - age * DAY, now - age * DAY))
        files.append(f)
    monkeypatch.setattr(settings, "retention_snapshot_days", 120)
    monkeypatch.setattr(settings, "retention_snapshot_max_gb", 2000 / GB)   # 2 файл багтана
    monkeypatch.setattr(settings, "retention_snapshot_max_disk_percent", 0)
    monkeypatch.setattr(settings, "disk_free_min_percent", 0.0)
    out = AC.prune_snapshots(str(tmp_path))
    assert out["snapshots"] == 2
    assert [f.exists() for f in files] == [False, False, True, True]
