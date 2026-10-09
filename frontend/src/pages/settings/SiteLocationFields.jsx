import { MapPin } from 'lucide-react'
import { coordinateLink, coordinatesFromLink, locationError } from '../../siteLocation'

export default function SiteLocationFields({ value, onChange }) {
  const error = locationError(value)
  const point = coordinatesFromLink(value.google_maps_url)
  const link = coordinateLink(value)
  return <fieldset className="rounded-lg border border-surface-border p-4 space-y-3">
    <legend className="px-1 text-sm font-semibold">Байршил ба түншийн газрын зураг</legend>
    <label className="block text-sm">Google Maps холбоос
      <input className="input mt-1 w-full" type="url" maxLength={2048} placeholder="https://maps.app.goo.gl/…"
        value={value.google_maps_url || ''} aria-invalid={!!error} aria-describedby="site-location-hint"
        onChange={e => onChange({ ...value, google_maps_url: e.target.value })} />
    </label>
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <label className="block text-sm">Өргөрөг (latitude)
        <input className="input mt-1" type="number" min="-90" max="90" step="any" placeholder="47.918…"
          value={value.latitude ?? ''} onChange={e => onChange({ ...value, latitude: e.target.value })} />
      </label>
      <label className="block text-sm">Уртраг (longitude)
        <input className="input mt-1" type="number" min="-180" max="180" step="any" placeholder="106.917…"
          value={value.longitude ?? ''} onChange={e => onChange({ ...value, longitude: e.target.value })} />
      </label>
    </div>
    <p id="site-location-hint" className="text-xs text-slate-300">Түншийн газрын зурагт зогсоолын орох хэсгийн хоёр координат хэрэгтэй. Google Maps дээр цэгээ баруун товшоод координатыг хуулна. Богино холбоос дангаараа координат өгөхгүй.</p>
    <div className="flex flex-wrap gap-2 items-center">
      {point && <button type="button" className="btn-secondary" onClick={() => onChange({ ...value, ...point })}>Холбоосын координатыг ашиглах</button>}
      {link && <a className="btn-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" href={link} target="_blank" rel="noopener noreferrer"><MapPin size={16} aria-hidden="true" />Цэгийг газрын зурагт шалгах</a>}
    </div>
    {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    <p className="text-xs text-slate-300">Багтаамж 0 бол сул зай ба дүүргэлтийн хувь тодорхойгүй гэж API-д гарна. Дүүргэлт нь камерын зогсолтын бүртгэлээс тооцсон ойролцоо үзүүлэлт.</p>
  </fieldset>
}
