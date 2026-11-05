// Камерын стрим сэргэхгүй байгаа анхааруулга — оператор ба админд бүх хуудсан дээр.
// Камер дугаар уншиж байгаа ч систем рүү дамжуулахгүй, дахин холболт тусалсангүй
// үед (backend: services/log_tail) гарна. «Камерыг дахин асаах» товч нь камерыг
// reboot хийнэ (1–2 минут тухайн хаалт дугаар танихгүй).
// 2026-11-05 BTALBAI орох камер 6 цаг 87% алдсаныг гомдол ирсний дараа л мэдсэн.
import { CameraOff, RotateCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, fmtHM } from '../api'
import { useAuth } from '../auth'
import { Modal, useToast } from './ui'

const POLL_MS = 20000
const MODULES = ['cashier', 'settings', 'barriers', 'devices', 'health']

export default function CameraAlerts() {
  const { can } = useAuth()
  const toast = useToast()
  const allowed = MODULES.some((m) => can(m))
  const [alerts, setAlerts] = useState([])
  const [confirm, setConfirm] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api('/api/admin/camera-alerts').then(setAlerts).catch(() => {})
  }, [])

  useEffect(() => {
    if (!allowed) return undefined
    load()
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [allowed, load])

  if (!allowed || !alerts.length) return null

  const reboot = async () => {
    const a = confirm
    setBusy(true)
    try {
      const res = await api(`/api/admin/camera-alerts/${a.device_id}/reboot`, { method: 'POST' })
      toast(res.detail, res.ok ? 'success' : 'error')
      if (res.ok) setAlerts((xs) => xs.filter((x) => x.device_id !== a.device_id))
      setConfirm(null)
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
      load()
    }
  }

  return (
    <>
      {alerts.map((a) => (
        <div key={a.device_id} role="alert"
          className="mb-4 rounded-lg border border-red-500/50 bg-red-500/10 px-4 py-3 flex flex-wrap items-center gap-3">
          <CameraOff size={20} className="text-red-400 shrink-0" aria-hidden />
          <div className="min-w-0 flex-1">
            <div className="font-medium text-red-300">
              «{a.name}» камер дугаар уншиж байгаа ч систем рүү дамжуулахгүй байна
            </div>
            <div className="text-sm text-slate-300 mt-0.5">
              {fmtHM(a.since)}-аас хойш · {a.missed} машин стримээр ирээгүй · {a.reconnects} удаа
              дахин холбосон ч сэргэсэнгүй. Хаалганы өмнө машингүй үед камерыг дахин асаана уу.
            </div>
          </div>
          {a.can_reboot && (
            <button onClick={() => setConfirm(a)}
              className="btn-danger">
              <RotateCw size={16} aria-hidden /> Камерыг дахин асаах
            </button>
          )}
        </div>
      ))}
      <Modal open={!!confirm} onClose={() => !busy && setConfirm(null)} title="Камерыг дахин асаах уу?" focusTrap>
        {confirm && (
          <div className="space-y-4">
            <p className="text-sm text-slate-300">
              «{confirm.name}» ({confirm.ip}) камер унтарч асна. <b>1–2 минутын турш энэ хаалт дугаар
              танихгүй, автоматаар нээгдэхгүй.</b> Хаалганы өмнө машин байхгүй үед дарна уу.
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn-secondary" disabled={busy} onClick={() => setConfirm(null)}>
                Болих
              </button>
              <button className="btn-danger" disabled={busy} onClick={reboot}>
                {busy ? 'Илгээж байна…' : 'Тийм, дахин асаах'}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </>
  )
}
