// Хаалтны удирдлага — төхөөрөмжийн статус, гараар нээх/хаах, командын лог
import { DoorClosed, DoorOpen, PlugZap, RefreshCw, ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { Badge, Table, useToast } from '../components/ui'

export default function Barriers() {
  const toast = useToast()
  const [devices, setDevices] = useState([])
  const [commands, setCommands] = useState([])

  const load = () => {
    api('/api/admin/devices').then(setDevices).catch(() => {})
    api('/api/barriers/commands?limit=50').then(setCommands).catch(() => {})
  }
  useEffect(load, [])

  const command = async (id, action, body = {}, okMsg = 'Амжилттай') => {
    try {
      const r = await api(`/api/barriers/${id}/${action}`, { method: 'POST', body })
      toast(r.status === 'SUCCESS' ? okMsg : `Команд амжилтгүй: ${r.response || ''}`,
        r.status === 'SUCCESS' ? 'success' : 'error')
      load()
    } catch (e) { toast(e.message, 'error') }
  }
  const open = (id) => command(id, 'open', {}, 'Хаалт нээгдлээ')
  const close = (id) => command(id, 'close', {}, 'Хаалт хаагдлаа')
  const forceOpen = (id) => {
    if (window.confirm('Албадан нээх үү? Хаалт онгорхой хэвээр үлдэнэ (гараар хаах хүртэл).'))
      command(id, 'open', { force: true }, 'Албадан нээгдлээ')
  }

  const testConn = async (id) => {
    try {
      const r = await api(`/api/admin/devices/${id}/test-connection`, { method: 'POST' })
      toast(r.detail, r.reachable ? 'success' : 'error')
    } catch (e) { toast(e.message, 'error') }
  }

  const barriers = devices.filter((d) => d.device_type === 'barrier')
  const cameras = devices.filter((d) => d.device_type === 'camera')

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Хаалтны удирдлага</h1>
        <button className="btn-secondary" onClick={load}><RefreshCw size={15} /> Шинэчлэх</button>
      </div>

      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {barriers.map((b) => (
          <div key={b.id} className="card">
            <div className="flex items-center justify-between mb-1">
              <div className="font-semibold">{b.name}</div>
              <Badge value={b.status} />
            </div>
            <div className="text-xs text-slate-500 mb-3">
              {b.site_name} · Эгнээ {b.lane_no} ({b.lane_dir === 'entry' ? 'орох' : 'гарах'}) · {b.model || 'DZBL-A'}
              {b.ip_address && <span className="font-mono"> · {b.ip_address}</span>}
            </div>
            <div className="flex gap-2">
              <button className="btn-primary flex-1 justify-center" onClick={() => open(b.id)}>
                <DoorOpen size={16} /> Нээх
              </button>
              <button className="btn-secondary flex-1 justify-center" onClick={() => close(b.id)}>
                <DoorClosed size={16} /> Хаах
              </button>
            </div>
            <button className="btn-secondary w-full justify-center mt-2 text-amber-400"
              onClick={() => forceOpen(b.id)} title="Хаалтыг онгорхой байлгах (forceBreaking)">
              <ShieldAlert size={14} /> Албадан нээх
            </button>
          </div>
        ))}
      </div>

      <div className="card">
        <h2 className="font-semibold mb-3">Камерын холболт</h2>
        <div className="text-xs text-slate-500 mb-2">
          <b>Онлайн</b> = камер серверт дата/heartbeat илгээж байна (камер→сервер).
          <b>Холболт шалгах</b> = сервер камер руу хүрч байгаа эсэх (сервер→камер, хаалт нээхэд хэрэгтэй).
        </div>
        <Table headers={['Нэр', 'Чиглэл', 'IP', 'Сүүлд холбогдсон', 'Онлайн', 'Сервер→камер']} empty={cameras.length === 0}>
          {cameras.map((c) => (
            <tr key={c.id}>
              <td className="td">{c.name}</td>
              <td className="td">{c.lane_dir === 'entry' ? 'Орох' : 'Гарах'}</td>
              <td className="td font-mono text-xs">{c.ip_address || '-'}</td>
              <td className="td font-mono text-xs">{fmtDate(c.last_seen)}</td>
              <td className="td">
                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium
                  ${c.online ? 'bg-accent/15 text-accent' : 'bg-red-500/15 text-red-400'}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${c.online ? 'bg-accent' : 'bg-red-400'}`} />
                  {c.online ? 'Онлайн' : 'Офлайн'}
                </span>
              </td>
              <td className="td">
                <button className="btn-secondary py-1 text-xs" onClick={() => testConn(c.id)} disabled={!c.ip_address}>
                  <PlugZap size={13} /> Шалгах
                </button>
              </td>
            </tr>
          ))}
        </Table>
      </div>

      <div className="card">
        <h2 className="font-semibold mb-3">Командын түүх</h2>
        <Table headers={['Хаалт', 'Команд', 'Эх үүсвэр', 'Хэн', 'Огноо', 'Үр дүн']} empty={commands.length === 0}>
          {commands.map((c) => (
            <tr key={c.id}>
              <td className="td">{c.device_name}</td>
              <td className="td font-mono">{c.command}</td>
              <td className="td text-xs">{c.command_source}</td>
              <td className="td text-xs">{c.issued_by || 'систем'}</td>
              <td className="td font-mono text-xs">{fmtDate(c.created_at)}</td>
              <td className="td"><Badge value={c.status} /></td>
            </tr>
          ))}
        </Table>
      </div>
    </div>
  )
}
