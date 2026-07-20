// LPR камерын зураг (snapshot) харуулах — auth токентой blob-оор татна
import { Camera } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { Modal } from './ui'

// Нэг зураг: /api/sessions/{id}/snapshot/{entry|exit}
export function SnapshotImg({ sessionId, kind, label }) {
  const [url, setUrl] = useState(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let objectUrl
    setUrl(null); setErr(false)
    api(`/api/sessions/${sessionId}/snapshot/${kind}`, { blob: true })
      .then((b) => { objectUrl = URL.createObjectURL(b); setUrl(objectUrl) })
      .catch(() => setErr(true))
    return () => objectUrl && URL.revokeObjectURL(objectUrl)
  }, [sessionId, kind])
  return (
    <div>
      <div className="label mb-1">{label}</div>
      {err ? (
        <div className="rounded-lg bg-surface-muted h-44 flex items-center justify-center text-xs text-slate-500">
          Зураг хадгалагдаагүй
        </div>
      ) : url ? (
        <img src={url} alt={`${label} зураг`} className="rounded-lg w-full max-h-72 object-contain bg-black" />
      ) : (
        <div className="rounded-lg bg-surface-muted h-44 animate-pulse" />
      )}
    </div>
  )
}

// Камер товч + орох/гарах 2 зурагтай modal. Зураг байхгүй бол товч бүдэг.
export function SnapshotButton({ session }) {
  const [open, setOpen] = useState(false)
  const has = session.entry_snapshot || session.exit_snapshot
  return (
    <>
      <button className={`btn-secondary py-1 px-2 text-xs ${has ? '' : 'opacity-30'}`}
        onClick={() => setOpen(true)} disabled={!has}
        aria-label={`${session.plate_number} зураг харах`} title={has ? 'Камерын зураг' : 'Зураг хадгалагдаагүй'}>
        <Camera size={14} />
      </button>
      <Modal open={open} onClose={() => setOpen(false)}
        title={`${session.plate_number} — Камерын зураг`} wide>
        {open && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <SnapshotImg sessionId={session.id} kind="entry" label="Орох" />
            <SnapshotImg sessionId={session.id} kind="exit" label="Гарах" />
          </div>
        )}
      </Modal>
    </>
  )
}
