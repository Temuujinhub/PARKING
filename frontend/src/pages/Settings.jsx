// Тохиргоо: Зогсоол / Төхөөрөмж / LED дэлгэц / Түрээслэгч / Холболт — хэсэг бүр settings/ доторх тусдаа файлд
import { useState } from 'react'
import { useAuth } from '../auth'
import AutoCloseSection from './settings/AutoCloseSection'
import DevicesSection from './settings/DevicesSection'
import IntegrationsSection from './settings/IntegrationsSection'
import ScreenSection from './settings/ScreenSection'
import SitesSection from './settings/SitesSection'
import TenantsSection from './settings/TenantsSection'

export default function Settings() {
  const [tab, setTab] = useState('sites')
  const { user } = useAuth()
  const canIntegrations = ['SUPER_ADMIN', 'ADMIN'].includes(user?.role)
  const tabs = [['sites', 'Зогсоол'], ['devices', 'Төхөөрөмж'], ['screen', 'LED дэлгэц'],
    ['autoclose', 'Авто цэвэрлэгээ'],
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
      {tab === 'autoclose' && <AutoCloseSection />}
      {tab === 'tenants' && <TenantsSection onGotoIntegrations={() => setTab('integrations')} />}
      {tab === 'integrations' && <IntegrationsSection />}
    </div>
  )
}
