-- «Гараар хаасан» бүртгэлийн задаргаа — ЗӨВХӨН УНШИНА (read-only role-оор ажиллана).
-- close_reason_diag.py-ийн SQL хувилбар: сервер дээр root эрхгүй үед psql-ээр
-- шууд ажиллуулна.
--
--   PGPASSWORD='...' psql -h 127.0.0.1 -p 15432 -U easyparking_ro -d parking \
--        -v site='Рашбулаг' -v days=3 -f close_reason_diag.sql
--
-- :site = зогсоолын нэрний эхлэл, :days = хэдэн хоног.

\set ON_ERROR_STOP on
\if :{?site}
\else
  \set site 'Рашбулаг'
\endif
\if :{?days}
\else
  \set days 3
\endif

\echo '══ 1) Хаалтын ЖИНХЭНЭ эх сурвалж (status = MANUAL_CLOSED = «Гараар хаасан») ══'
SELECT st.name                                            AS зогсоол,
       COALESCE(a.action, '(AuditLog алга)')              AS хаасан_зам,
       COALESCE(a.username, '—')                          AS хэн,
       count(*)                                           AS тоо,
       count(*) FILTER (WHERE COALESCE(s.total_fee, 0) = 0) AS "0₮_мөр",
       -- Түүх хуудас (sessions_router._CLOSE_ACTIONS) зөвхөн эдгээрийг мэднэ;
       -- бусад нь «Хаасан» багананд ХООСОН харагдана.
       count(*) FILTER (WHERE a.action IS NULL OR a.action NOT IN
             ('MANUAL_EXIT','ADMIN_REMOVE','AUTO_CLOSE','AUTO_FREE_CLOSE','AUTO_JUNK_CLOSE'))
                                                          AS "UI-д_хоосон"
FROM parking_sessions s
JOIN parking_sites st ON st.id = s.site_id
LEFT JOIN LATERAL (
    SELECT al.action, al.username
    FROM audit_logs al
    WHERE al.entity = 'session' AND al.entity_id = s.id::text
    ORDER BY al.created_at DESC
    LIMIT 1
) a ON true
WHERE s.status = 'MANUAL_CLOSED'
  AND s.exit_time > now() - (:'days' || ' days')::interval
  AND st.name LIKE :'site' || '%'
GROUP BY 1, 2, 3
ORDER BY 4 DESC;

\echo ''
\echo '══ 2) Бүх төлөв — тухайн хугацаанд юу хэрхэн дуусаж байна ══'
SELECT st.name AS зогсоол, s.status AS төлөв, count(*) AS тоо,
       count(*) FILTER (WHERE COALESCE(s.total_fee, 0) = 0) AS "0₮",
       count(*) FILTER (WHERE s.duration_minutes IS NULL)   AS "хугацаа_үл_мэдэгдэх",
       count(*) FILTER (WHERE COALESCE(s.paused_minutes, 0) > 0) AS "дотор_орсон"
FROM parking_sessions s
JOIN parking_sites st ON st.id = s.site_id
WHERE s.entry_time > now() - (:'days' || ' days')::interval
  AND st.name LIKE :'site' || '%'
GROUP BY 1, 2
ORDER BY 1, 3 DESC;

\echo ''
\echo '══ 3) Дотоод (nested) камерууд зөв тэмдэглэгдсэн үү? ══'
\echo '   nested_inner = f байвал дотоод хаалт ЭНГИЙН гарц шиг ажиллаж, машиныг'
\echo '   ДОТОР байхад нь session-ийг хааж байна гэсэн үг.'
SELECT st.name AS зогсоол, d.name AS төхөөрөмж, d.device_type AS төрөл,
       d.lane_dir AS чиглэл, d.lane_no AS эгнээ, d.nested_inner AS дотоод_үү,
       d.auto_open AS авто_нээх, d.ip_address AS ip, d.status AS төлөв,
       d.last_seen AS сүүлд_холбогдсон
FROM devices d
JOIN parking_sites st ON st.id = d.site_id
WHERE st.name LIKE :'site' || '%' AND d.status <> 'deleted'
ORDER BY d.nested_inner, d.device_type, d.lane_dir, d.lane_no;

\echo ''
\echo '══ 4) Одоо «дотор» гэж тоологдож буй машинууд (paused_since) ══'
SELECT s.plate_number AS дугаар, s.entry_time AS орсон,
       s.paused_since AS дотор_орсон,
       round(EXTRACT(EPOCH FROM (now() - s.paused_since)) / 60)::int AS дотор_минут,
       COALESCE(s.paused_minutes, 0) AS хуримтлагдсан_минут, s.status AS төлөв
FROM parking_sessions s
JOIN parking_sites st ON st.id = s.site_id
WHERE st.name LIKE :'site' || '%'
  AND s.status IN ('OPEN', 'AWAITING_PAYMENT', 'PAID')
  AND s.paused_since IS NOT NULL
ORDER BY s.paused_since;

\echo ''
\echo '══ 5) Зогсоолын дамжин/авто хаалтын тохиргоо ══'
SELECT name AS зогсоол, transit_max_hours AS "дамжих_дээд_ц",
       auto_close_hours AS "авто_хаалт_ц",
       entry_only_free_hours AS "зөвхөн_орох_үнэгүй_ц",
       parent_site_id AS "эцэг_зогсоол"
FROM parking_sites
WHERE name LIKE :'site' || '%';

\echo ''
\echo '══ 6) Жишээ мөрүүд (сүүлийн 30 «Гараар хаасан») ══'
SELECT s.plate_number AS дугаар, s.entry_time AS орсон, s.exit_time AS гарсан,
       s.duration_minutes AS минут, COALESCE(s.total_fee, 0)::int AS дүн,
       s.exit_confirmed AS "гарах_уншилттай_юу",
       COALESCE(s.paused_minutes, 0) AS дотор_минут,
       COALESCE(a.action, '(AuditLog алга)') AS хаасан_зам,
       left(COALESCE(s.note, ''), 60) AS тэмдэглэл
FROM parking_sessions s
JOIN parking_sites st ON st.id = s.site_id
LEFT JOIN LATERAL (
    SELECT al.action FROM audit_logs al
    WHERE al.entity = 'session' AND al.entity_id = s.id::text
    ORDER BY al.created_at DESC LIMIT 1
) a ON true
WHERE s.status = 'MANUAL_CLOSED'
  AND s.exit_time > now() - (:'days' || ' days')::interval
  AND st.name LIKE :'site' || '%'
ORDER BY s.exit_time DESC
LIMIT 30;
