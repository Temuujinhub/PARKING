import {
  Banknote, Car, ClipboardList, FileText, History, LayoutDashboard, ListX,
  LogOut, Percent, ReceiptText, ScrollText, Settings, ShieldAlert, Users, DoorOpen,
} from 'lucide-react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'

// Цэс: module = эрхийн матрицын түлхүүр (backend ROLE_PERMISSIONS-тэй ижил)
const MENU = [
  { to: '/', label: 'Хяналтын самбар', icon: LayoutDashboard, module: 'dashboard' },
  { to: '/cashier', label: 'Касс', icon: Banknote, module: 'cashier' },
  { to: '/check', label: 'Шалгах', icon: Car, module: 'check' },
  { to: '/history', label: 'Түүх', icon: History, module: 'history' },
  { to: '/discounts', label: 'Хөнгөлөлт', icon: Percent, module: 'discounts' },
  { to: '/drivers', label: 'Бүртгэлтэй жолооч', icon: ClipboardList, module: 'drivers' },
  { to: '/reports', label: 'Тайлан', icon: FileText, module: 'reports' },
  { to: '/vat', label: 'Ибаримт', icon: ReceiptText, module: 'vat' },
  { to: '/barriers', label: 'Хаалтны удирдлага', icon: DoorOpen, module: 'barriers' },
  { to: '/blacklist', label: 'Хар жагсаалт', icon: ShieldAlert, module: 'blacklist' },
  { to: '/settings', label: 'Тохиргоо', icon: Settings, module: 'settings' },
  { to: '/users', label: 'Хэрэглэгчид', icon: Users, module: 'users' },
  { to: '/logs', label: 'Лог', icon: ScrollText, module: 'logs' },
]

const ROLE_LABELS = {
  SUPER_ADMIN: 'Супер админ', ADMIN: 'Админ', FINANCE: 'Санхүү', OPERATOR: 'Оператор',
}

export default function Layout() {
  const { user, can, logout } = useAuth()
  const navigate = useNavigate()
  const items = MENU.filter((m) => (m.module === 'users' ? user?.role === 'SUPER_ADMIN' : can(m.module)))

  return (
    <div className="flex min-h-dvh">
      <aside className="w-60 shrink-0 bg-surface-card border-r border-surface-border/60 flex flex-col">
        <div className="px-5 py-5 border-b border-surface-border/60">
          <div className="font-bold text-lg tracking-tight">
            <span className="text-accent">P</span> Smart Parking
          </div>
          <div className="text-xs text-slate-500 mt-0.5">Зогсоолын удирдлага</div>
        </div>
        <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5" aria-label="Үндсэн цэс">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors cursor-pointer
                 ${isActive ? 'bg-accent/10 text-accent font-medium' : 'text-slate-300 hover:bg-surface-muted'}`}>
              <Icon size={17} aria-hidden />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="p-4 border-t border-surface-border/60">
          <div className="text-sm font-medium truncate">{user?.full_name || user?.username}</div>
          <div className="text-xs text-slate-500 mb-2">{ROLE_LABELS[user?.role] || user?.role}</div>
          <button onClick={() => { logout(); navigate('/login') }} className="btn-secondary w-full justify-center text-xs py-1.5">
            <LogOut size={14} /> Гарах
          </button>
        </div>
      </aside>
      <main className="flex-1 min-w-0 p-6 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
