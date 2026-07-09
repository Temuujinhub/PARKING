import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import Layout from './components/Layout'
import { ToastHost } from './components/ui'
import Barriers from './pages/Barriers'
import Blacklist from './pages/Blacklist'
import Cashier from './pages/Cashier'
import Check from './pages/Check'
import Compensations from './pages/Compensations'
import Dashboard from './pages/Dashboard'
import Discounts from './pages/Discounts'
import Drivers from './pages/Drivers'
import History from './pages/History'
import Login from './pages/Login'
import Logs from './pages/Logs'
import Pay from './pages/Pay'
import Reports from './pages/Reports'
import Settings from './pages/Settings'
import Users from './pages/Users'
import Vat from './pages/Vat'

function Protected({ module, children }) {
  const { user, loading, can } = useAuth()
  if (loading) return <div className="min-h-dvh flex items-center justify-center text-slate-500">Ачаалж байна…</div>
  if (!user) return <Navigate to="/login" replace />
  if (module && !can(module)) return <Navigate to="/" replace />
  return children
}

// Нүүр хуудас: dashboard эрхтэй бол Хяналтын самбар, үгүй бол (жишээ OPERATOR) эхний
// хүртээмжтэй хуудас руу шилжүүлнэ — 403 гацаанаас сэргийлнэ.
function Home() {
  const { can } = useAuth()
  if (can('dashboard')) return <Dashboard />
  const fallback = ['cashier', 'check', 'barriers', 'history', 'drivers'].find((m) => can(m))
  return <Navigate to={fallback ? `/${fallback}` : '/login'} replace />
}

export default function App() {
  return (
    <AuthProvider>
      <ToastHost />
      <BrowserRouter>
        <Routes>
          {/* Public — жолоочийн төлбөрийн хуудас */}
          <Route path="/pay" element={<Pay />} />
          <Route path="/login" element={<Login />} />
          <Route element={<Protected><Layout /></Protected>}>
            <Route index element={<Home />} />
            <Route path="cashier" element={<Protected module="cashier"><Cashier /></Protected>} />
            <Route path="check" element={<Protected module="check"><Check /></Protected>} />
            <Route path="history" element={<Protected module="history"><History /></Protected>} />
            <Route path="discounts" element={<Protected module="discounts"><Discounts /></Protected>} />
            <Route path="drivers" element={<Protected module="drivers"><Drivers /></Protected>} />
            <Route path="reports" element={<Protected module="reports"><Reports /></Protected>} />
            <Route path="vat" element={<Protected module="vat"><Vat /></Protected>} />
            <Route path="compensations" element={<Protected module="cashier"><Compensations /></Protected>} />
            <Route path="barriers" element={<Protected module="barriers"><Barriers /></Protected>} />
            <Route path="blacklist" element={<Protected module="blacklist"><Blacklist /></Protected>} />
            <Route path="settings" element={<Protected module="settings"><Settings /></Protected>} />
            <Route path="users" element={<Users />} />
            <Route path="logs" element={<Protected module="logs"><Logs /></Protected>} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
