// Тохиргоо: Зогсоол / Төхөөрөмж / LED дэлгэц / Төлбөрийн дүрэм / Автомат ажил /
// Түрээслэгч / Холболт — хэсэг бүр settings/ доторх тусдаа файлд
import { useState } from 'react'
import { useAuth } from '../auth'
import AutomationSection from './settings/AutomationSection'
import DevicesSection from './settings/DevicesSection'
import IntegrationsSection from './settings/IntegrationsSection'
import PaymentRulesSection from './settings/PaymentRulesSection'
import ScreenSection from './settings/ScreenSection'
import SitesSection from './settings/SitesSection'
import TenantsSection from './settings/TenantsSection'

export default function Settings() {
  const [tab, setTab] = useState('sites')
  const { user } = useAuth()
  const canIntegrations = ['SUPER_ADMIN', 'ADMIN'].includes(user?.role)
  // Табын зохион байгуулалт (2026-09-03):
  //   Зогсоол/Төхөөрөмж/LED — ЮУ байгаа (бүтэц)
  //   Төлбөрийн дүрэм       — ЯАЖ шийдэх (бүх дүрэм, зогсоол бүрээр; ерөнхий горимтой)
  //   Автомат ажил          — ард нь АЖИЛЛАДАГ зүйлс (камерын эрүүл мэнд, лог нөхөлт, цэвэрлэгээ)
  //   Түрээслэгч/Холболт    — ХЭН, ХААШАА (данс, API)
  // Өмнөх «Авто цэвэрлэгээ» таб нь дүрэм + автомат ажил хольсон, Төлбөрийн дүрэмтэй
  // давхцаж байсан тул задлав.
  const tabs = [['sites', 'Зогсоол'], ['devices', 'Төхөөрөмж'], ['screen', 'LED дэлгэц'],
    ['payrules', 'Төлбөрийн дүрэм'], ['automation', 'Автомат ажил'],
    // Түрээслэгч зөвхөн SUPER_ADMIN-д; Холболт (данс/API/цэнэглэгч) ADMIN-д мөн
    // харагдана (өөрийн хамрах хүрээгээр — backend шүүнэ)
    ...(user?.role === 'SUPER_ADMIN' ? [['tenants', 'Түрээслэгч']] : []),
    ...(canIntegrations ? [['integrations', 'Холболт']] : [])]
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Тохиргоо</h1>
      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {tabs.map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>
      {tab === 'sites' && <SitesSection onGotoIntegrations={
        canIntegrations ? () => setTab('integrations') : null} />}
      {tab === 'devices' && <DevicesSection />}
      {tab === 'screen' && <ScreenSection />}
      {tab === 'payrules' && <PaymentRulesSection onGotoDevices={() => setTab('devices')} />}
      {tab === 'automation' && <AutomationSection onGotoRules={() => setTab('payrules')} />}
      {tab === 'tenants' && <TenantsSection onGotoIntegrations={() => setTab('integrations')} />}
      {tab === 'integrations' && <IntegrationsSection />}
    </div>
  )
}
