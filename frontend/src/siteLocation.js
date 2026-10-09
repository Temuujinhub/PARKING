export function locationPayload(site) {
  return {
    google_maps_url: (site.google_maps_url || '').trim() || null,
    latitude: site.latitude == null || String(site.latitude).trim() === '' ? null : Number(site.latitude),
    longitude: site.longitude == null || String(site.longitude).trim() === '' ? null : Number(site.longitude),
  }
}

export function locationError(site) {
  const p = locationPayload(site)
  if ((p.latitude == null) !== (p.longitude == null)) return 'Өргөрөг, уртрагыг хоёуланг нь оруулна уу.'
  if (p.latitude != null && (!Number.isFinite(p.latitude) || !Number.isFinite(p.longitude)
    || Math.abs(p.latitude) > 90 || Math.abs(p.longitude) > 180)) return 'Координатын утга буруу байна.'
  if (p.google_maps_url) {
    try {
      const u = new URL(p.google_maps_url)
      const host = u.hostname
      const pathOK = ['maps.app.goo.gl', 'maps.google.com'].includes(host)
        || ['www.google.com', 'google.com'].includes(host) && (u.pathname === '/maps' || u.pathname.startsWith('/maps/'))
        || host === 'goo.gl' && u.pathname.startsWith('/maps/')
      if (u.protocol !== 'https:' || !pathOK || u.username || u.password || u.port
        || /[\s\\]/.test(p.google_maps_url) || p.google_maps_url.length > 2048) throw new Error()
    } catch { return 'Зөв HTTPS Google Maps холбоос оруулна уу.' }
  }
  return ''
}

export function coordinateLink(site) {
  const p = locationPayload(site)
  if (locationError(site) || p.latitude == null) return null
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${p.latitude},${p.longitude}`)}`
}

export function coordinatesFromLink(link) {
  // Only explicit search coordinates; @lat,lng is a viewport centre, not the entrance.
  if (locationError({ google_maps_url: link })) return null
  try {
    const u = new URL(link)
    const value = u.searchParams.get('query') || u.searchParams.get('q') || ''
    const m = value.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/)
    if (!m) return null
    const point = { latitude: Number(m[1]), longitude: Number(m[2]) }
    return locationError(point) ? null : point
  } catch { return null }
}
