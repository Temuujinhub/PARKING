-- Хоёр түрээслэгчийн загварт шилжих миграци (2026-08-01):
--   Систем (SUPER_ADMIN, Тэмүүжин)
--   ├── ИйзиПаркинг ХХК   — өөрийн 40 зогсоол (NIC, KH, SPORT …)
--   └── Моннис Пропертиес — Моннис билдинг (+3 төлөвлөгдсөн)
--
-- Юу хийдэг (ИДЕМПОТЕНТ — олон удаа ажиллуулахад аюулгүй):
--   1. MONNIS/EASYPARKING түрээслэгчдийг олно, байхгүй бол үүсгэнэ
--      (MONNIS зогсоолыг аль хэдийн эзэмшсэн түрээслэгч байвал түүнийг ашиглана)
--   2. MONNIS зогсоолыг Моннист, бусад бүх зогсоолыг ИйзиПаркингд ононо
--   3. Моннис зогсоолтой хэрэглэгчдийг Моннис түрээслэгч рүү зөөнө;
--      АДМИН/САНХҮҮ ролийнхных нь зогсоолын хязгаарыг тайлж түрээслэгчийн
--      БҮХ зогсоолыг (ирээдүйн +3-ыг ч) автоматаар хардаг болгоно.
--      ОПЕРАТОР-уудын үндсэн зогсоол (ээлж нээхэд хэрэгтэй) хэвээр үлдэнэ.
--   4. Үлдсэн бүх хэрэглэгчийг (SUPER_ADMIN-аас бусад) ИйзиПаркингд ононо
--   5. Гэрээт машидыг түрээслэгчид нь ононо: зогсоолтой нь зогсоолоороо,
--      «бүх зогсоол» (NULL) нь ИйзиПаркинд — түрээслэгч дамнан үнэгүй нэвтрэхгүй
--
-- Ажиллуулах: sudo -u postgres psql -d parking -f /root/PARKING/deploy/migrate_two_tenants.sql
-- ӨМНӨ НЬ update.sh ажиллуулж tenants хүснэгт үүссэн байх ёстой.

BEGIN;

DO $$
DECLARE
  ep_id uuid;
  mn_id uuid;
  mn_site uuid;
BEGIN
  SELECT id INTO mn_site FROM parking_sites WHERE site_code = 'MONNIS';

  -- Моннис түрээслэгч: зогсоолын одоогийн эзэн > code=MONNIS > шинээр үүсгэх
  IF mn_site IS NOT NULL THEN
    SELECT tenant_id INTO mn_id FROM parking_sites WHERE id = mn_site AND tenant_id IS NOT NULL;
  END IF;
  IF mn_id IS NULL THEN
    SELECT id INTO mn_id FROM tenants WHERE code = 'MONNIS';
  END IF;
  IF mn_id IS NULL THEN
    INSERT INTO tenants (id, name, code, register, is_active, created_at)
    VALUES (gen_random_uuid(), 'Моннис Пропертиес ХХК', 'MONNIS', '15200020090',
            true, now() at time zone 'utc')
    RETURNING id INTO mn_id;
  END IF;

  -- ИйзиПаркинг түрээслэгч
  SELECT id INTO ep_id FROM tenants WHERE code = 'EASYPARKING';
  IF ep_id IS NULL THEN
    INSERT INTO tenants (id, name, code, note, is_active, created_at)
    VALUES (gen_random_uuid(), 'ИйзиПаркинг ХХК', 'EASYPARKING',
            'Операторын өөрийн зогсоолууд (40)', true, now() at time zone 'utc')
    RETURNING id INTO ep_id;
  END IF;

  -- Зогсоолын оноолт
  IF mn_site IS NOT NULL THEN
    UPDATE parking_sites SET tenant_id = mn_id WHERE id = mn_site;
  END IF;
  UPDATE parking_sites SET tenant_id = ep_id WHERE tenant_id IS NULL;

  -- Моннис зогсоолтой хэрэглэгчид → Моннис түрээслэгч
  IF mn_site IS NOT NULL THEN
    UPDATE users SET tenant_id = mn_id
     WHERE role <> 'SUPER_ADMIN' AND tenant_id IS NULL
       AND (site_id = mn_site
            OR coalesce(site_ids::text, '') LIKE '%' || mn_site::text || '%');
  END IF;
  -- Моннисын АДМИН/САНХҮҮ — түрээслэгчийн бүх зогсоолыг автоматаар харна
  UPDATE users SET site_id = NULL, site_ids = NULL
   WHERE tenant_id = mn_id AND role IN ('ADMIN', 'FINANCE');

  -- Үлдсэн бүгд → ИйзиПаркинг
  UPDATE users SET tenant_id = ep_id
   WHERE role <> 'SUPER_ADMIN' AND tenant_id IS NULL;

  -- Гэрээт машид: зогсоолтой нь зогсоолынхоо түрээслэгчид,
  -- «бүх зогсоол» (site NULL) нь ИйзиПаркинд — түрээслэгч ДАМНАН нэвтрэхгүй
  UPDATE registered_drivers rd SET tenant_id = s.tenant_id
    FROM parking_sites s
   WHERE rd.site_id = s.id AND rd.tenant_id IS DISTINCT FROM s.tenant_id;
  UPDATE registered_drivers SET tenant_id = ep_id
   WHERE site_id IS NULL AND tenant_id IS NULL;
END $$;

COMMIT;

-- ── Баталгаажуулалт ──
SELECT coalesce(t.name, '(СИСТЕМ - SUPER_ADMIN)') AS tenant,
       count(*) AS users
FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id
GROUP BY 1 ORDER BY 1;

SELECT t.name AS tenant, s.name AS site, s.site_code
FROM parking_sites s LEFT JOIN tenants t ON t.id = s.tenant_id
ORDER BY 1, 2;

SELECT u.username, u.role, coalesce(t.code, 'SUPER') AS tenant,
       CASE WHEN u.site_id IS NULL AND (u.site_ids IS NULL OR u.site_ids::text IN ('null','[]'))
            THEN 'түрээслэгчийн бүх зогсоол' ELSE 'заасан зогсоол' END AS scope
FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id
ORDER BY tenant, u.role, u.username;
