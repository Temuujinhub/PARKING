"""msgbill-ийн сарын баримтын квот — 429-ийн харагдац.

2026-08-27 production: 11:15-т msgbill-ийн сарын хязгаар (500) дүүрч, түүнээс
хойш 24 цагийн турш **85 баримт чимээгүй бүтэлгүйтсэн** — бүх зогсоолын ДДТД
зогссон. Алдааны текст DB-д бичигдсээр байсан ч ямар ч дохио байгаагүй тул
хэн ч анзаараагүй. `parse_quota_limit` нь хязгаарыг татгалзсан хариунаас
сугалж авдаг — тариф нь msgbill талд байдаг тул өөр эх сурвалж БАЙХГҮЙ.
"""
from app.services.msgbill import parse_quota_limit

REAL = "msgbill 429: Сарын eBarimt хязгаар (500) дүүрсэн — шатлалаа ахиулна уу (Billing → eBarimt API)."


def test_бодит_production_мессежээс_хязгаарыг_сугална():
    assert parse_quota_limit(REAL) == 500


def test_өөр_тарифын_тоог_ч_уншина():
    assert parse_quota_limit("Сарын хязгаар (2000) дүүрсэн") == 2000
    assert parse_quota_limit("Сарын хязгаар (10000) дүүрсэн") == 10000


def test_хязгаар_дурдаагүй_бол_None():
    assert parse_quota_limit("msgbill 429: Too many requests") is None


def test_хоосон_оролт():
    assert parse_quota_limit(None) is None
    assert parse_quota_limit("") is None


def test_нэг_оронтой_тоог_хязгаар_гэж_үзэхгүй():
    """«(5)» гэх мэт жижиг хаалт нь тариф биш — худал утга буцаах ёсгүй."""
    assert parse_quota_limit("алдаа (5) удаа давтагдав") is None
