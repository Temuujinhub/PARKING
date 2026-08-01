// Тохиргоо: Зогсоол / Төхөөрөмж / LED дэлгэц — хэсэг бүр settings/ доторх тусдаа файлд
import { useState } from 'react'
import DevicesSection from './settings/DevicesSection'
import ScreenSection from './settings/ScreenSection'
import SitesSection from './settings/SitesSection'

export default function Settings() {
  const [tab, setTab] = useState('sites')
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Тохиргоо</h1>
      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['sites', 'Зогсоол'], ['devices', 'Төхөөрөмж'], ['screen', 'LED дэлгэц']].map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>
      {tab === 'sites' && <SitesSection />}
      {tab === 'devices' && <DevicesSection />}
      {tab === 'screen' && <ScreenSection />}
    </div>
  )
}
