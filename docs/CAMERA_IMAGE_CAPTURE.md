## 1. Digest-auth HTTP client (snapshot.cgi татахад ашигладаг)

```ts
const md5 = (s: string) => crypto.createHash('md5').update(s).digest('hex')

function buildDigest(user: string, pass: string, method: string, uri: string, wwwAuth: string): string {
  const realm  = wwwAuth.match(/realm="([^"]+)"/)?.[1]  ?? ''
  const nonce  = wwwAuth.match(/nonce="([^"]+)"/)?.[1]  ?? ''
  const qop    = wwwAuth.match(/qop="?([^",\s]+)"?/)?.[1]
  const opaque = wwwAuth.match(/opaque="([^"]+)"/)?.[1]
  const ha1 = md5(`${user}:${realm}:${pass}`)
  const ha2 = md5(`${method}:${uri}`)
  let hdr: string
  if (qop) {
    const nc = '00000001', cnonce = crypto.randomBytes(4).toString('hex')
    const resp = md5(`${ha1}:${nonce}:${nc}:${cnonce}:${qop}:${ha2}`)
    hdr = `Digest username="${user}", realm="${realm}", nonce="${nonce}", uri="${uri}", qop=${qop}, nc=${nc}, cnonce="${cnonce}", response="${resp}"`
  } else {
    hdr = `Digest username="${user}", realm="${realm}", nonce="${nonce}", uri="${uri}", response="${md5(`${ha1}:${nonce}:${ha2}`)}"`
  }
  if (opaque) hdr += `, opaque="${opaque}"`
  return hdr
}

function digestGetBuffer(host: string, port: number, path: string, user: string, pass: string): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const doReq = (authHdr?: string) => {
      const hdrs: Record<string, string> = { 'User-Agent': 'ANPR-Viewer/1.0' }
      if (authHdr) hdrs['Authorization'] = authHdr
      // agent: false — Node 19+ global agent сокетыг pool-д хадгалдаг; Dahua CGI тэр сокетыг
      // дахин ашиглахад хариу өгөхгүй гацдаг тул хүсэлт бүрд шинэ сокет нээнэ
      const req = http.request({ hostname: host, port, path, method: 'GET', headers: hdrs, timeout: 8000, agent: false }, res => {
        if (res.statusCode === 401 && !authHdr) {
          res.resume()
          const wwwAuth = res.headers['www-authenticate'] as string | undefined
          if (wwwAuth) doReq(buildDigest(user, pass, 'GET', path, wwwAuth))
          else reject(new Error('401 no WWW-Authenticate'))
          return
        }
        if (res.statusCode !== 200) { res.resume(); reject(new Error(`HTTP ${res.statusCode}`)); return }
        const chunks: Buffer[] = []
        res.on('data', (c: Buffer) => chunks.push(c))
        res.on('end',  () => resolve(Buffer.concat(chunks)))
        res.on('error', reject)
      })
      req.on('error', reject)
      req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
      req.end()
    }
    doReq()
  })
}
```

## 2. Snapshot.cgi-аар зураг татаж хадгалах (fallback/eventManager горим)

```ts
async function fetchAndSaveEventImage(ev: CamEvent, cfg: StreamCfg): Promise<void> {
  const snapPath = '/cgi-bin/snapshot.cgi?channel=1&count=1'
  try {
    const imgBuf = await digestGetBuffer(cfg.host, cfg.port, snapPath, cfg.user, cfg.pass)
    if (imgBuf.length < 1000 || imgBuf[0] !== 0xFF || imgBuf[1] !== 0xD8) {
      console.log(`[img:${cfg.key}] bad JPEG ${imgBuf.length}B`)
      return
    }
    const dateStr = new Date().toISOString().slice(0, 10)
    const dayDir  = join(IMAGES_DIR, dateStr)
    if (!existsSync(dayDir)) mkdirSync(dayDir, { recursive: true })
    writeFileSync(join(dayDir, `${ev.id}.jpg`), imgBuf)
    ev.imageUrl = `/api/image/${ev.id}`
    cacheImage(ev.id, imgBuf)
    console.log(`[img:${cfg.key}] saved size=${imgBuf.length}B  ${ev.plateNumber}`)
    // Update DB row with image URL (INSERT happened before image was fetched)
    if (dbReady) pool.query('UPDATE anpr_events SET image_url=$1 WHERE id=$2', [ev.imageUrl, ev.id]).catch(() => {})
    // Notify SSE clients that image is now available
    const payload = JSON.stringify({ id: ev.id, imageUrl: ev.imageUrl })
    for (const c of sseClients) { try { c.write(`event: imageUpdate\ndata: ${payload}\n\n`) } catch {} }
  } catch (e) {
    console.log(`[img:${cfg.key}] fetch ERR: ${e}`)
  }
}
```

## 3. snapManager (үндсэн горим) — камераас push-ээр ирсэн бодит зургийг зулгаах

```ts
// Хэрэглэгддэг camera stream URL:
//   snap ? '/cgi-bin/snapManager.cgi?action=attachFileProc&Flags[0]=Event&Events=[TrafficJunction]'
//        : '/cgi-bin/eventManager.cgi?action=attach&codes=[TrafficJunction]&heartbeat=5'

// snapManager нь текст event (Events[0].Key=Value) илгээгээд, ард нь binary хэсэг илгээдэг.
// Тухайн binary blob-оос SceneImage.Offset/Length ашиглан жинхэнэ зургийг татдаг.
function parseSnapEvent(text: string): { ev: CamEvent; scene: { offset: number; len: number } | null } | null {
  const m = new Map<string, string>()
  for (const line of text.split(/\r?\n/)) {
    const eq = line.indexOf('=')
    if (eq === -1) continue
    const key = line.slice(0, eq).trim()
    if (!key.startsWith('Events[0].')) continue
    m.set(key.slice('Events[0].'.length), line.slice(eq + 1).trim())
  }
  if (m.get('Code') !== 'TrafficJunction') return null
  const plate = (m.get('Object.Text') || m.get('TrafficCar.PlateNumber') || '').trim()
  if (!plate) return null
  const utc = Number(m.get('RealUTC') || m.get('UTC') || 0)
  const dirRaw = m.get('TrafficCar.Direction') ?? ''
  const direction: CamEvent['direction'] =
    dirRaw === '0' ? 'entering' : dirRaw === '1' ? 'exiting' : 'unknown'
  const ev: CamEvent = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    plateNumber: plate,
    confidence: Number(m.get('Object.Confidence') || m.get('TrafficCar.Confidence') || 0),
    timestamp: utc ? new Date(utc * 1000).toISOString() : new Date().toISOString(),
    vehicleColor: m.get('TrafficCar.VehicleColor') || undefined,
    plateColor:   m.get('TrafficCar.PlateColor') || undefined,
    vehicleType:  m.get('Vehicle.Text') || m.get('TrafficCar.Category') || undefined,
    laneNo: Number(m.get('Lane') || 0),
    direction,
    country: (m.get('CommInfo.Country') && m.get('CommInfo.Country') !== 'Unknown') ? m.get('CommInfo.Country') : undefined,
    objectId: Number(m.get('EventID') || 0) || undefined,
    groupId:  Number(m.get('GroupID') || 0) || undefined,
  }
  const sLen = Number(m.get('SceneImage.Length') || 0)
  const scene = sLen > 0 ? { offset: Number(m.get('SceneImage.Offset') || 0), len: sLen } : null
  return { ev, scene }
}

// Multipart стримийн нэг хэсгийг боловсруулна — эндээс зургийн binary хэсгийг таньж авдаг
function processPart(contentType: string, body: Buffer, st: StreamState): void {
  // ── snapManager: binary blob (scene image + plate cutout) follows its text event ──
  if (contentType.startsWith('image/')) {
    if (!IMAGE_CAPTURE_ENABLED) return   // capture paused — event already pushed without waiting for this part
    if (!st.snap) return
    const ev = st.pendingEv
    if (!ev) { console.log(`[snap:${st.cfg.key}] image part ${body.length}B with no pending event`); return }
    if (st.pendingTimer) { clearTimeout(st.pendingTimer); st.pendingTimer = null }
    const scene = st.pendingScene
    st.pendingEv = null
    st.pendingScene = null
    // Slice out the full scene image by offset/length; fall back to whole blob
    let img = body
    if (scene && scene.len > 0 && scene.offset + scene.len <= body.length) {
      img = body.slice(scene.offset, scene.offset + scene.len)
    }
    if (img.length < 1000 || img[0] !== 0xFF || img[1] !== 0xD8) {
      console.log(`[snap:${st.cfg.key}] bad scene JPEG ${img.length}B — fallback to snapshot.cgi`)
      pushEventRaw(ev)
      fetchAndSaveEventImage(ev, st.cfg).catch(() => {})
      return
    }
    saveCaptureImage(ev, img, st.cfg)
    return
  }

  if (!contentType.startsWith('text/plain')) {
    if (st.snap) console.log(`[snap:${st.cfg.key}] part ct="${contentType}" ${body.length}B`)
    return
  }

  const text = body.toString('utf8').trim()
  if (text === '') return
  if (text === 'Heartbeat') { process.stdout.write('.'); return }

  // ... (лог/debug бичих хэсэг хассан)

  if (st.snap) {
    // ── snapManager flat "Events[0].Key=Value" format ──
    const parsed = parseSnapEvent(text)
    if (!parsed) return
    const { ev, scene } = parsed
    applyStreamCtx(ev, st.cfg)
    if (isDuplicate(ev.plateNumber, ev.parkingCameraId)) return
    if (!IMAGE_CAPTURE_ENABLED) { pushEventRaw(ev); return }   // skip waiting for the image part entirely
    // A previous event never got its image — flush it first
    if (st.pendingEv) flushPending(st, 'next event arrived, no image')
    st.pendingEv = ev
    st.pendingScene = scene
    st.pendingTimer = setTimeout(() => {
      if (st.pendingEv?.id === ev.id) flushPending(st, 'no capture image in time')
    }, 4000)
    return
  }

  // ── eventManager "Code=...;data={JSON}" format ──
  const ev = parseTextEvent(text)
  if (!ev) return
  applyStreamCtx(ev, st.cfg)
  const evCode = text.match(/Code=([^;]+)/)?.[1] ?? ''
  if (evCode !== 'TrafficJunction') return
  if (isDuplicate(ev.plateNumber, ev.parkingCameraId)) return
  // push immediately, then fetch live snapshot (unless capture is paused)
  pushEventRaw(ev)
  if (IMAGE_CAPTURE_ENABLED) fetchAndSaveEventImage(ev, st.cfg).catch(() => {})
}

// snapManager-с ирсэн зургийг диск рүү хадгалах
function saveCaptureImage(ev: CamEvent, imgBuf: Buffer, cfg: StreamCfg): void {
  const dateStr = new Date().toISOString().slice(0, 10)
  const dayDir  = join(IMAGES_DIR, dateStr)
  if (!existsSync(dayDir)) mkdirSync(dayDir, { recursive: true })
  try {
    writeFileSync(join(dayDir, `${ev.id}.jpg`), imgBuf)
    ev.imageUrl = `/api/image/${ev.id}`
    cacheImage(ev.id, imgBuf)
    console.log(`[snap:${cfg.key}] CAPTURE saved size=${imgBuf.length}B  ${ev.plateNumber}`)
  } catch (e) { console.log(`[snap:${cfg.key}] save ERR: ${e}`) }
  pushEventRaw(ev)   // imageUrl already set → DB INSERT includes it
}

// зураг хугацаандаа ирээгүй бол event-ийг зурагүйгээр push хийж snapshot.cgi руу шилждэг
function flushPending(st: StreamState, reason: string): void {
  if (!st.pendingEv) return
  if (st.pendingTimer) { clearTimeout(st.pendingTimer); st.pendingTimer = null }
  const ev = st.pendingEv
  st.pendingEv = null
  st.pendingScene = null
  console.log(`[snap:${st.cfg.key}] ${reason} for ${ev.plateNumber} — fallback to snapshot.cgi`)
  pushEventRaw(ev)
  if (IMAGE_CAPTURE_ENABLED) fetchAndSaveEventImage(ev, st.cfg).catch(() => {})
}
```

## 4. Зургийн кэш (memory) ба тохиргоо

```ts
const IMAGES_DIR = join(process.cwd(), 'images')
if (!existsSync(IMAGES_DIR)) mkdirSync(IMAGES_DIR, { recursive: true })

// ── Image store: event id → JPEG Buffer ───────────────────────────────────
const imageStore = new Map<string, Buffer>()
const MAX_IMAGES = 50

// Бүх оруулалт ЗААВАЛ энэ функцээр явна кэш хязгааргүй өсөж OOM болдог
function cacheImage(id: string, buf: Buffer): void {
  imageStore.set(id, buf)
  while (imageStore.size > MAX_IMAGES) imageStore.delete(imageStore.keys().next().value!)
}

// Камерууд snapManager-ийг ашиглахгүй, snapshot.cgi руу шилжсэн
const SNAP_DISABLED = new Set<string>(['park:5', 'park:7'])

//Одоогоор ЗУРАГ ТАТАЛТ БҮГД ЗОГССОН — plate/timestamp/түвшин г.м. өгөгдөл цуглардаг ч зураг хадгалагдахгүй түр зогсоосон
const IMAGE_CAPTURE_ENABLED = false
```

## 5. HTTP endpoint — frontend-д зургийг үзүүлэх

```ts
// Serve captured plate images (memory first, then disk)
if (url.startsWith('/api/image/')) {
  const id = url.slice('/api/image/'.length).split('?')[0]
  const img = imageStore.get(id)
  if (img) {
    res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=3600', 'Access-Control-Allow-Origin': '*' })
    res.end(img); return
  }
  // Дискнээс async уншина — sync уншилт event loop-ийг блоклож стрим/SSE-г гацаадаг.
  try {
    const ts = Number(id.split('-')[0])
    const guess = Number.isFinite(ts) && ts > 0 ? [new Date(ts).toISOString().slice(0, 10)] : []
    const days = [...new Set([...guess, ...(await fsp.readdir(IMAGES_DIR))])]
    for (const d of days) {
      try {
        const buf = await fsp.readFile(join(IMAGES_DIR, d, `${id}.jpg`))
        cacheImage(id, buf)
        res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=3600', 'Access-Control-Allow-Origin': '*' })
        res.end(buf); return
      } catch { /* энэ өдөрт алга — дараагийнхыг үзнэ */ }
    }
  } catch { /* ignore */ }
  res.writeHead(404); res.end()
  return
}
```

## 6. Frontend талд авах хэсэг (`src/api/dahuaApi.ts`)

```ts
source.addEventListener('imageUpdate', (e) => {
  try {
    const { id, imageUrl } = JSON.parse((e as MessageEvent).data) as { id: string; imageUrl: string }
    onImageUpdate?.(id, imageUrl)
  } catch {}
})
```

Зураг бэлэн болмогц сервер `imageUpdate` нэртэй SSE event илгээж, frontend тухайн event-ийн
`imageUrl`-г шинэчилдэг (browser нь `<img src="/api/image/<id>">` ашиглан татаж авдаг).

---

## 7. PARKING backend-ийн хэрэгжилт (Python) — энэ баримттай харьцуулсан зураглал

Дээрх TypeScript код нь **ANPR-Viewer** (Node) клиентийнх — лавлагаа болгож
хадгалсан. PARKING backend яг ижил бодлогуудыг Python талдаа дараах байдлаар
хэрэгжүүлдэг (2026-09-01-нд энэ баримтын дүрмүүдээр бүхэлд нь сайжруулав):

| ANPR-Viewer (энэ баримт) | PARKING (backend/app) | Тайлбар |
|---|---|---|
| §1 digest client, `agent:false` | `services/barrier.camera_client` + httpx DigestAuth | PARKING эсрэгээрээ клиентийг ХУВААЛЦДАГ — камерын зэрэгцээ холболтын хязгаар дүүрвэл хаалтны команд хүлээгддэг тул |
| §2 snapshot.cgi fallback | `services/snapshot._fetch_from_camera` | Ажилласан URL хувилбарыг цээжилдэг, дараалсан бүтэлгүйтэлд түр зогсоодог (quiet), хаалтны командад зам тавьдаг |
| §2 `bad JPEG <1000B` шалгалт | `services/snapshot.valid_jpeg` (2026-09-01) | БҮХ эх сурвалж `_save`-ээр дамждаг тул валидаци төвлөрсөн: SOI magic + ≥1000B. Эвдэрсэн зургийг хадгалахгүй |
| §3 snapManager attachFileProc | `services/snap_puller` — WS зам (`_ws_session`) + comet зам (`_comet_session`, SubscribeNotify type=1 + Base64) | Production дээр comet нь батлагдсан ганц суваг |
| §3 `pendingTimer` (4с) | `_comet_one`/`_pull_one`-ийн 3с `_flush_later` (2026-09-01) | Өмнө нь burst-ийн СҮҮЛЧИЙН зураг дараагийн event иртэл `best` буферт гацдаг байв |
| §3 fallback дараалал | стрим зураг (3с) → WS/comet хүлээлт (8с) → snapshot.cgi | `_capture_and_store`; `PARKING_SNAPSHOT_CGI_FALLBACK=false` гэж БҮҮ тавь — snapshot.cgi нь ганц найдвартай эх сурвалж хэвээр |
| §4 memory cache (MAX_IMAGES) | Браузерын кэш: `Cache-Control: private, max-age=86400, immutable` (2026-09-01) | Замын файл өөрчлөгддөггүй (шинэ зураг = шинэ нэр) тул сервер кэш хэрэггүй |
| §5 өдрийн хавтас, id-гаар хайх | `{snapshot_dir}/YYYYMMDD/{plate}_{HHMMSS}_{rand}_{lane}.jpg`, зам DB-д (`entry_snapshot`/`exit_snapshot`) | 2026-09-01: tmp→rename атомар бичилт + нэрэнд санамсаргүй дагавар (нэг секундын мөргөлдөөн арилсан) |
| §6 `imageUpdate` SSE | `SNAPSHOT_READY` WS broadcast + frontend `SnapshotImg`-ийн retry (2026-09-01) | Зураг event-ээс хоцорч ирдэг тул касс 90с-ийн цонхонд өсөх зайтай дахин татдаг |

Мөн ANPR-Viewer-т байхгүй нэмэлтүүд: session мөрийн түгжээтэй үеийн retry,
давхар хадгалагдсан файлын цэвэрлэгээ (`discard_saved`), нөхөн таталт
(RecordFinder/mediaFileFind/амьд кадр — `fetch_stored_picture`), retention
(хугацаа + нийт GB таг), суваг тус бүрийн тоолуур (`/api/admin/cameras/snap-state`).
