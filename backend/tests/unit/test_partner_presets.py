"""MonPay-ийн autoPay webhook-ийг deploy-оор тавих (2026-11-11) — серверийн үүрэг, дүрэм, DB.

  1. Үүрэг: deploy.env / орчин / PARKING_PUBLIC_BASE_URL — бүгд нэг утгатай байх; зөрвөл None
  2. Хоосон → нэг удаа тавина; админ хоосолсны дараа дахин тавихгүй
  3. Өөр орчны MonPay хост (продын dump-аас сэргээсэн TEST) → ҮРГЭЛЖ засна
  4. MonPay-ээс өөр URL → хөндөхгүй; vehicle_webhook_url хөндөгдөхгүй; AuditLog
"""
import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as M
from app.database import Base
from app.services import partner_presets as PP

TEST_URL = "https://z-wallet.monpay.mn/auto-parking/parking/webhooks/easyparking"
PROD_URL = "https://wallet.monpay.mn/auto-parking/parking/webhooks/easyparking"


def test_urls():
    assert PP.monpay_url("staging") == TEST_URL
    assert PP.monpay_url("production") == PROD_URL


def _env_file(tmp_path, text):
    p = tmp_path / "deploy.env"
    p.write_text(text, encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("file_text,env,base,expected", [
    ("PARKING_DEPLOY_ROLE=staging\n", {}, "https://test.easy-parking.mn", "staging"),
    ("PARKING_DEPLOY_ROLE='staging'  \n", {}, "http://localhost", "staging"),
    (None, {}, "https://site.easy-parking.mn", "production"),
    (None, {}, "http://172.16.100.21", "production"),
    ("PARKING_DEPLOY_ROLE=production\n", {}, "https://site.easy-parking.mn", "production"),
    (None, {"PARKING_DEPLOY_ROLE": "staging"}, "http://localhost", "staging"),
    # Зөрчилтэй дохио — ЮУ Ч ХИЙХГҮЙ
    ("PARKING_DEPLOY_ROLE=production\n", {}, "https://test.easy-parking.mn", None),
    ("PARKING_DEPLOY_ROLE=staging\n", {}, "https://site.easy-parking.mn", None),
    # Дохиогүй (CI, хөгжүүлэгч) / танигдахгүй үүрэг
    (None, {}, "http://localhost", None),
    (None, {}, "https://other.example.com", None),
    ("PARKING_DEPLOY_ROLE=dev\n", {}, "http://localhost", None),
    ("# PARKING_DEPLOY_ROLE=staging\n", {}, "http://localhost", None),
])
def test_server_role(tmp_path, file_text, env, base, expected):
    path = _env_file(tmp_path, file_text) if file_text is not None else str(tmp_path / "missing.env")
    role, signals = PP.server_role(base, env=env, deploy_env_file=path)
    assert role == expected, signals


@pytest.mark.parametrize("current,role,done,expected_url", [
    (None, "staging", False, TEST_URL),
    ("", "production", False, PROD_URL),
    (None, "production", True, None),                 # админ хоосолсон — дахин тавихгүй
    (TEST_URL, "staging", False, None),
    (PROD_URL, "production", True, None),
    (PROD_URL, "staging", True, TEST_URL),            # продын dump-аас сэргээсэн TEST
    (TEST_URL, "production", True, PROD_URL),         # прод дээр тест хост — засна
    ("https://wallet.monpay.mn/old/hook", "production", False, PROD_URL),
    ("https://wallet.monpay.mn/old/hook", "production", True, None),
    ("https://api.monpay.mn/hook", "staging", False, TEST_URL),
    ("https://partner.example/hook", "staging", False, None),   # админы шийдвэр
])
def test_plan(current, role, done, expected_url):
    new, why = PP.plan_monpay_webhook(current, role, done)
    assert new == expected_url, why


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _key(db, name, **kw):
    raw = f"pk_{name}_synthetic"
    k = M.PartnerKey(name=name, key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                     key_prefix=raw[:10], scopes="read,pay,vehicles", **kw)
    db.add(k)
    db.commit()
    return k


def test_apply_sets_once_and_respects_admin(db):
    mp = _key(db, "mobifinance", vehicle_webhook_url="https://wallet.monpay.mn/notify")
    other = _key(db, "socialpay")
    changes = PP.apply_monpay_webhook(db, role="production")
    assert [c["new"] for c in changes] == [PROD_URL]
    db.refresh(mp)
    db.refresh(other)
    assert mp.webhook_url == PROD_URL
    assert mp.vehicle_webhook_url == "https://wallet.monpay.mn/notify"   # хөндөгдөөгүй
    assert other.webhook_url is None                                     # өөр түнш
    log = db.query(M.AuditLog).filter_by(action="PARTNER_WEBHOOK_SET").one()
    assert log.username == "system:deploy" and log.detail["url"] == PROD_URL

    # Админ хоосолсон → дахин restart хийхэд тавихгүй
    mp.webhook_url = None
    db.commit()
    assert PP.apply_monpay_webhook(db, role="production") == []
    db.refresh(mp)
    assert mp.webhook_url is None


def test_apply_fixes_wrong_environment_after_dump_restore(db):
    mp = _key(db, "mobifinance", webhook_url=PROD_URL)
    PP.apply_monpay_webhook(db, role="production")      # прод дээр: тэмдэглэгээ үүснэ
    assert db.query(M.AuditLog).count() == 0
    # Продын dump TEST-д сэргээгдэв (тэмдэглэгээ ч хамт) — TEST-ийн үүрэгтэй асав
    changes = PP.apply_monpay_webhook(db, role="staging")
    assert changes and changes[0]["new"] == TEST_URL
    db.refresh(mp)
    assert mp.webhook_url == TEST_URL


def test_apply_without_key_waits_for_key(db):
    assert PP.apply_monpay_webhook(db, role="staging") == []
    mp = _key(db, "mobifinance")                          # түлхүүр дараа үүсэв
    assert PP.apply_monpay_webhook(db, role="staging")[0]["new"] == TEST_URL
    db.refresh(mp)
    assert mp.webhook_url == TEST_URL


def test_apply_unknown_role_noop(db, monkeypatch):
    mp = _key(db, "mobifinance")
    monkeypatch.setattr(PP, "server_role", lambda *a, **k: (None, ["public_base_url=?"]))
    assert PP.apply_monpay_webhook(db) == []
    db.refresh(mp)
    assert mp.webhook_url is None


def test_inactive_key_untouched(db):
    mp = _key(db, "mobifinance", is_active=False)
    assert PP.apply_monpay_webhook(db, role="production") == []
    db.refresh(mp)
    assert mp.webhook_url is None


def test_startup_never_raises():
    def boom():
        raise RuntimeError("db down")
    PP.run_startup_presets(boom)      # алдааг лог л болгоно
