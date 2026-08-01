// Тохиргооны хэсгүүдийн дундын туслах функцууд
import { Camera, DoorOpen } from 'lucide-react'
import { useState } from 'react'

// Enter дархад дараагийн талбар руу шилжих (сүүлийнх дээр submit)
export function enterToNext(e) {
  if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return
  e.preventDefault()
  const els = [...e.target.form.querySelectorAll('input, select')].filter((el) => !el.disabled)
  const i = els.indexOf(e.target)
  if (i > -1 && i < els.length - 1) els[i + 1].focus()
  else e.target.form.requestSubmit()
}

// QR зураг — ачаалж чадаагүй бол алдаа + "Дахин үүсгэх" товч харуулна
export function QrImage({ code, alt }) {
  const [key, setKey] = useState(0)
  const [err, setErr] = useState(false)
  const retry = () => { setErr(false); setKey((k) => k + 1) }
  if (err) {
    return (
      <div className="mx-auto w-52 h-52 rounded-xl bg-surface-muted flex flex-col items-center justify-center gap-3 text-sm text-slate-400">
        QR ачаалж чадсангүй
        <button type="button" className="btn-primary py-1.5" onClick={retry}>Дахин үүсгэх</button>
      </div>
    )
  }
  return (
    <div>
      <img key={key} className="mx-auto rounded-xl bg-white p-3 w-52 h-52"
        src={`/api/public/qr/${code}.png?v=${key}`} alt={alt} onError={() => setErr(true)} />
      <button type="button" className="text-xs text-slate-500 hover:text-slate-300 mt-1.5 cursor-pointer underline"
        onClick={retry}>QR дахин үүсгэх</button>
    </div>
  )
}

// Орох/гарах хаалтын тоогоор төхөөрөмжийн загварыг динамикаар үүсгэнэ.
// Эгнээ бүр өөрийн камер + хаалттай, ижил lane_no-той (barrier камерынхаа реле-ээр нээгддэг).
export function genDevices(entryLanes, exitLanes) {
  // Камер+хаалт+LED нь ЦОГЦ төхөөрөмж (ITC436/IPMECS): камерын IP-ээр хаалт
  // нээж LED-ийг удирддаг. Тиймээс зөвхөн КАМЕР үүсгэнэ — хаалтны мөрийг
  // ensure_lane_barriers (backend) камерынх нь IP дээр авто үүсгэнэ. Ингэснээр
  // жагсаалт цэвэрхэн, давхар хаалт үүсэхгүй. Тусдаа IP-тэй хаалт хэрэгтэй бол
  // Төхөөрөмж хэсгээс гараар нэмнэ.
  const list = []
  for (let i = 1; i <= entryLanes; i++) {
    const suf = entryLanes > 1 ? ` ${i}` : ''
    list.push({ key: `entry_cam_${i}`, name: `Орох камер${suf}`, device_type: 'camera', lane_dir: 'entry', lane_no: i, auto_open: true, icon: Camera, integrated: true })
  }
  for (let j = 1; j <= exitLanes; j++) {
    const lane = entryLanes + j
    const suf = exitLanes > 1 ? ` ${j}` : ''
    list.push({ key: `exit_cam_${j}`, name: `Гарах камер${suf}`, device_type: 'camera', lane_dir: 'exit', lane_no: lane, auto_open: false, icon: Camera, integrated: true })
  }
  return list
}

// QR-т кодлогдсонтой ижил линк — backend public_base_url (домэйн) ашиглана
export const payUrl = (s) => s?.pay_url || `${location.origin}/pay?site=${s?.site_code}`
export const qrUrl = (code) => `/api/public/qr/${code}.png`
