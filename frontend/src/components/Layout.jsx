import {
  Banknote, Car, ChevronDown, ClipboardList, DoorOpen, FileText, History, KeyRound,
  LayoutDashboard, LogOut, Moon, Percent, ReceiptText, ScrollText, Settings, ShieldAlert,
  Sun, Tag, Users, Wallet,
} from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { isDark, toggleTheme } from '../theme'
import Logo from './Logo'
import { Field, Modal, useToast } from './ui'

// Бүлэглэсэн цэс: standalone item эсвэл {group, children[]}. module = эрхийн түлхүүр.
const NAV = [
  { to: '/', label: 'Хяналтын самбар', icon: LayoutDashboard, module: 'dashboard' },
  {
    group: 'Касс', icon: Banknote, children: [
      { to: '/cashier', label: 'Касс', icon: Banknote, module: 'cashier' },
      { to: '/check', label: 'Шалгах', icon: Car, module: 'check' },
      { to: '/history', label: 'Түүх', icon: History, module: 'history' },
      { to: '/compensations', label: 'Нөхөн төлбөр', icon: Banknote, module: 'compensations' },
    ],
  },
  {
    group: 'Санхүү', icon: Wallet, children: [
      { to: '/settlement', label: 'Мөнгөн тооцоо', icon: Wallet, module: 'reports' },
      { to: '/reports', label: 'Тайлан', icon: FileText, module: 'reports' },
      { to: '/discounts', label: 'Хөнгөлөлт', icon: Percent, module: 'discounts' },
      { to: '/tariffs', label: 'Тарифын загвар', icon: Tag, module: 'discounts' },
      { to: '/drivers', label: 'Бүртгэлтэй жолооч', icon: ClipboardList, module: 'drivers' },
      { to: '/vat', label: 'Ибаримт', icon: ReceiptText, module: 'vat' },
      { to: '/blacklist', label: 'Хар жагсаалт', icon: ShieldAlert, module: 'blacklist' },
    ],
  },
  {
    group: 'Админ', icon: Settings, children: [
      { to: '/settings', label: 'Тохиргоо', icon: Settings, module: 'settings' },
      { to: '/barriers', label: 'Хаалтны удирдлага', icon: DoorOpen, module: 'barriers' },
      { to: '/users', label: 'Ажилтан', icon: Users, module: 'users' },
      { to: '/logs', label: 'Лог', icon: ScrollText, module: 'logs' },
    ],
  },
]

const ROLE_LABELS = {
  SUPER_ADMIN: 'Супер админ', ADMIN: 'Админ', FINANCE: 'Санхүү', HR: 'Хүний нөөц', OPERATOR: 'Оператор',
}

export default function Layout() {
  const { user, can, logout } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const [dark, setDark] = useState(isDark())
  const [pwModal, setPwModal] = useState(null) // {old_password, new_password, confirm}
  const [collapsed, setCollapsed] = useState({}) // {groupName: true} — хумигдсан бүлэг

  // Хүртээмжтэй child-тай бүлгүүд + standalone item-ууд
  const nav = NAV.map((n) => n.group
    ? { ...n, children: n.children.filter((c) => can(c.module)) }
    : n).filter((n) => n.group ? n.children.length > 0 : can(n.module))
  const linkClass = ({ isActive }) =>
    `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors cursor-pointer
     ${isActive ? 'bg-accent/10 text-accent font-medium' : 'text-slate-300 hover:bg-surface-muted'}`

  const changePassword = async (e) => {
    e.preventDefault()
    if (pwModal.new_password !== pwModal.confirm) return toast('Шинэ нууц үг давхцахгүй байна', 'error')
    try {
      await api('/api/auth/change-password', {
        method: 'POST',
        body: { old_password: pwModal.old_password, new_password: pwModal.new_password },
      })
      toast('Нууц үг солигдлоо')
      setPwModal(null)
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="flex min-h-dvh">
      <aside className="w-60 shrink-0 bg-surface-card border-r border-surface-border/60 flex flex-col">
        <div className="px-5 py-5 border-b border-surface-border/60">
          <Logo size={30} textClass="text-base" />
          <div className="text-xs text-slate-500 mt-1.5">Зогсоолын удирдлага</div>
        </div>
        <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5" aria-label="Үндсэн цэс">
          {nav.map((n) => n.group ? (
            <div key={n.group} className="pt-1.5">
              <button onClick={() => setCollapsed((c) => ({ ...c, [n.group]: !c[n.group] }))}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-300 cursor-pointer">
                <n.icon size={13} aria-hidden />
                <span className="flex-1 text-left">{n.group}</span>
                <ChevronDown size={13} className={`transition-transform ${collapsed[n.group] ? '-rotate-90' : ''}`} />
              </button>
              {!collapsed[n.group] && (
                <div className="space-y-0.5 mt-0.5">
                  {n.children.map(({ to, label, icon: Icon }) => (
                    <NavLink key={to} to={to} className={linkClass}>
                      <Icon size={17} aria-hidden /><span>{label}</span>
                    </NavLink>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <NavLink key={n.to} to={n.to} end={n.to === '/'} className={linkClass}>
              <n.icon size={17} aria-hidden /><span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="p-4 border-t border-surface-border/60">
          <div className="flex items-center justify-between mb-2">
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">{user?.full_name || user?.username}</div>
              <div className="text-xs text-slate-500">{ROLE_LABELS[user?.role] || user?.role}</div>
            </div>
            <button onClick={() => setDark(toggleTheme())} aria-label="Өдөр/шөнийн горим солих"
              className="p-2 rounded-lg hover:bg-surface-muted cursor-pointer text-slate-400 hover:text-accent transition-colors">
              {dark ? <Sun size={17} /> : <Moon size={17} />}
            </button>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            <button onClick={() => setPwModal({ old_password: '', new_password: '', confirm: '' })}
              className="btn-secondary justify-center text-xs py-1.5" title="Нууц үг солих">
              <KeyRound size={13} /> Нууц үг
            </button>
            <button onClick={() => { logout(); navigate('/login') }} className="btn-secondary justify-center text-xs py-1.5">
              <LogOut size={13} /> Гарах
            </button>
          </div>
        </div>

        <Modal open={!!pwModal} onClose={() => setPwModal(null)} title="Нууц үг солих">
          {pwModal && (
            <form onSubmit={changePassword} className="space-y-3">
              <Field label="Одоогийн нууц үг" required>
                <input className="input" type="password" required autoComplete="current-password"
                  value={pwModal.old_password} onChange={(e) => setPwModal({ ...pwModal, old_password: e.target.value })} />
              </Field>
              <Field label="Шинэ нууц үг (8+ тэмдэгт)" required>
                <input className="input" type="password" required minLength={8} autoComplete="new-password"
                  value={pwModal.new_password} onChange={(e) => setPwModal({ ...pwModal, new_password: e.target.value })} />
              </Field>
              <Field label="Шинэ нууц үг давтах" required>
                <input className="input" type="password" required autoComplete="new-password"
                  value={pwModal.confirm} onChange={(e) => setPwModal({ ...pwModal, confirm: e.target.value })} />
              </Field>
              <button className="btn-primary w-full justify-center">Солих</button>
            </form>
          )}
        </Modal>
      </aside>
      <main className="flex-1 min-w-0 p-6 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
