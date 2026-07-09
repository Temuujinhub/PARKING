// Тайлан — нимгэн orchestrator: огнооны муж, таб сонголт, зогсоол шүүлт + толгойн Excel.
// Таб бүрийн агуулга components/ReportTabs.jsx-д тусдаа компонент (өөрсдөө useFetch хийнэ).
import { Download } from 'lucide-react'
import { useState } from 'react'
import {
  ByPaymentTab, DailyTab, MonthlyTab, RevenueTab, ShiftsTab, TransactionsTab, siteQ,
} from '../components/ReportTabs'
import { useFetch } from '../hooks/useFetch'
import { useDownload } from '../hooks/useDownload'

const TABS = [['revenue', 'Зогсоолоор'], ['monthly', 'Сараар'], ['daily', 'Өдрөөр'],
  ['shifts', 'Ээлжээр'], ['bypayment', 'Төлбөрийн төрлөөр'], ['transactions', 'Бичилт']]

export default function Reports() {
  const dl = useDownload()
  const [tab, setTab] = useState('revenue')
  const today = new Date().toISOString().slice(0, 10)
  const weekAgo = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10)
  const [from, setFrom] = useState(weekAgo)
  const [to, setTo] = useState(today)
  const [siteId, setSiteId] = useState('')
  const { data: sites } = useFetch('/api/admin/sites', { initial: [], silent: true })

  // Толгойн Excel товч — таб тус бүрд өөр зам (Бичилт таб өөрийн товчтой)
  const headerExcel = {
    revenue: [`/api/reports/revenue/excel?date_from=${from}&date_to=${to}`, `tailan_${from}_${to}.xlsx`],
    daily: [`/api/reports/daily/excel?date_from=${from}&date_to=${to}${siteQ(siteId)}`, `odriin_tailan_${from}_${to}.xlsx`],
    monthly: [`/api/reports/monthly/excel?date_from=${from}&date_to=${to}${siteQ(siteId)}`, `saraar_${from}_${to}.xlsx`],
    shifts: [`/api/reports/shifts/excel?date_from=${from}&date_to=${to}`, `eeljeer_${from}_${to}.xlsx`],
    bypayment: [`/api/reports/by-payment/excel?date_from=${from}&date_to=${to}${siteQ(siteId)}`, `tolboriin_torol_${from}_${to}.xlsx`],
  }
  const ex = headerExcel[tab]

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Тайлан</h1>
        <div className="flex items-center gap-2">
          <input type="date" className="input w-40" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Эхлэх огноо" />
          <span className="text-slate-500">—</span>
          <input type="date" className="input w-40" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Дуусах огноо" />
          {ex && <button className="btn-primary" onClick={() => dl(ex[0], ex[1])}><Download size={16} /> Excel</button>}
        </div>
      </div>

      <div className="flex gap-1 border-b border-surface-border/60 overflow-x-auto" role="tablist">
        {TABS.map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>

      {/* Зогсоол шүүлт — Сараар/Өдрөөр/Ээлжээр/Төлбөрийн төрлөөр таб-уудад */}
      {['monthly', 'daily', 'shifts', 'bypayment'].includes(tab) && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-slate-400">Зогсоол:</span>
          <select className="input w-auto" value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Зогсоол шүүх">
            <option value="">Бүх зогсоол</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
      )}

      {tab === 'revenue' && <RevenueTab from={from} to={to} />}
      {tab === 'daily' && <DailyTab from={from} to={to} siteId={siteId} />}
      {tab === 'monthly' && <MonthlyTab from={from} to={to} siteId={siteId} />}
      {tab === 'shifts' && <ShiftsTab from={from} to={to} siteId={siteId} />}
      {tab === 'bypayment' && <ByPaymentTab from={from} to={to} siteId={siteId} />}
      {tab === 'transactions' && <TransactionsTab from={from} to={to} sites={sites} />}
    </div>
  )
}
