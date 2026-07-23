// LPR камерын зураг (snapshot) харуулах — auth токентой blob-оор татна
import { Camera, DownloadCloud } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { Modal } from './ui'

// Нэг зураг: /api/sessions/{id}/snapshot/{entry|exit}
// Зураг байхгүй бол "Камераас татах" — камерын санах ойгоос нөхөж татна (backfill)
export function SnapshotImg({ sessionId, kind, label }) {
  const [url, setUrl] = useState(null)
  const [err, setErr] = useState(false)
  const [fetching, setFetching] = useState(false)
  const [fetchErr, setFetchErr] = useState('')
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    let objectUrl
    setUrl(null); setErr(false)
    api(`/api/sessions/${sessionId}/snapshot/${kind}`, { blob: true })
      .then((b) => { objectUrl = URL.createObjectURL(b); setUrl(objectUrl) })
      .catch(() => setErr(true))
    return () => objectUrl && URL.revokeObjectURL(objectUrl)
  }, [sessionId, kind, refresh])

  const backfill = () => {
    setFetching(true); setFetchErr('')
    api(`/api/sessions/${sessionId}/snapshot/${kind}/backfill`, { method: 'POST' })
      .then(() => setRefresh((r) => r + 1))
      .catch((e) => setFetchErr(e?.message || 'Камераас татаж чадсангүй'))
      .finally(() => setFetching(false))
  }

  return (
    <div>
      <div className="label mb-1">{label}</div>
      {err ? (
        <div className="rounded-lg bg-surface-muted h-44 flex flex-col gap-2 items-center justify-center text-xs text-slate-500">
          <span>Зураг хадгалагдаагүй</span>
          <button className="btn-secondary py-1 px-2 text-xs flex items-center gap-1"
            onClick={backfill} disabled={fetching}>
            <DownloadCloud size={13} />
            {fetching ? 'Камераас хайж байна…' : 'Камераас татах'}
          </button>
          {fetchErr && <span className="text-red-400 text-center px-2">{fetchErr}</span>}
        </div>
      ) : url ? (
        <img src={url} alt={`${label} зураг`} className="rounded-lg w-full max-h-72 object-contain bg-black" />
      ) : (
        <div className="rounded-lg bg-surface-muted h-44 animate-pulse" />
      )}
    </div>
  )
}

// Камер товч + орох/гарах 2 зурагтай modal. Зураг байхгүй ч modal нээж
// "Камераас татах"-аар нөхөж болно.
export function SnapshotButton({ session }) {
  const [open, setOpen] = useState(false)
  const has = session.entry_snapshot || session.exit_snapshot
  return (
    <>
      <button className={`btn-secondary py-1 px-2 text-xs ${has ? '' : 'opacity-40'}`}
        onClick={() => setOpen(true)}
        aria-label={`${session.plate_number} зураг харах`}
        title={has ? 'Камерын зураг' : 'Зураг хадгалагдаагүй — камераас нөхөж татаж болно'}>
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
