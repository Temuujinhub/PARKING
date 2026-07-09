// Дундын өгөгдөл татах hook — GET хүсэлтийг нэг стандарт болгож, алдааг чимээгүй
// залгидаггүй (toast-оор харуулна), давхардсан useEffect+catch(()=>{}) кодыг арилгана.
//
// Хэрэглээ:
//   const { data, loading, error, reload } = useFetch('/api/admin/sites', { initial: [] })
//   path нь state-ээс бүрдвэл (шүүлтүүр, огноо) — path өөрчлөгдмөгц автоматаар дахин татна.
//   Байнга дуудагддаг (polling) газар { silent: true } өгвөл алдаа toast харуулахгүй.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useToast } from '../components/ui'

export function useFetch(path, { initial = null, enabled = true, silent = false } = {}) {
  const [data, setData] = useState(initial)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(!!enabled)
  const toast = useToast()

  const reload = useCallback(() => {
    if (!enabled || !path) { setLoading(false); return Promise.resolve() }
    setLoading(true)
    return api(path)
      .then((d) => { setData(d); setError(null); return d })
      .catch((e) => { setError(e.message); if (!silent) toast?.(e.message, 'error') })
      .finally(() => setLoading(false))
  }, [path, enabled, silent]) // toast нь mount-ийн дараа тогтмол

  useEffect(() => { reload() }, [reload])

  return { data, error, loading, reload, setData }
}
