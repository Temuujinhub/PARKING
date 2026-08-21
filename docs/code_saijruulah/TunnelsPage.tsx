import { useEffect, useState } from 'react';

interface MtPeerUI { name: string; address: string; state: string; uptime: string | null; subnets: { src: string; dst: string; ph2: string | null }[] }
interface DbPeer { id: number; name: string; remoteGw: string; remoteLan: string; localSubnet: string; note: string; enabled: boolean; vendor: string; apiPort: string; wanInterface: string; lanInterface: string; pskSet: boolean; fgSet: boolean }
type PeerForm = Omit<DbPeer, 'id' | 'pskSet' | 'fgSet'> & { id?: number; psk: string; pskSet: boolean; fgSet: boolean; fgToken: string; fgUser: string; fgPass: string };
const BLANK_PEER: Omit<DbPeer, 'id' | 'pskSet' | 'fgSet'> = { name: '', remoteGw: '', remoteLan: '', localSubnet: '10.0.79.0/26', note: '', enabled: true, vendor: 'mikrotik', apiPort: '', wanInterface: '', lanInterface: '' };
const BLANK_PEER_FORM = { ...BLANK_PEER, psk: '', pskSet: false, fgSet: false, fgToken: '', fgUser: '', fgPass: '' };

const inpStyle: React.CSSProperties = { background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text-primary)', fontSize: 13, outline: 'none', width: '100%', boxSizing: 'border-box' };
const sectionTitle = (t: string) => <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent-blue)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: 1 }}>{t}</div>;
const btn = (bg: string): React.CSSProperties => ({ padding: '7px 18px', background: bg, border: 'none', borderRadius: 6, color: 'var(--text-on-accent)', fontSize: 13, cursor: 'pointer' });

function field(label: string, value: string, onChange: (v: string) => void, placeholder = '', type = 'text', required = false) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>{label}{required && <span style={{ color: 'var(--accent-red)', marginLeft: 2 }}>*</span>}</label>
      <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} style={inpStyle} required={required} />
    </div>
  );
}

type SubTab = 'conn' | 'peers';

export default function TunnelsPage({ token }: { token: string }) {
  const H = { 'Content-Type': 'application/json', 'X-Auth-Token': token };
  const [subTab, setSubTab] = useState<SubTab>('conn');

  // Холболт
  const [host, setHost] = useState('');
  const [port, setPort] = useState('');
  const [https, setHttps] = useState(false);
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [connPassSet, setConnPassSet] = useState(false);

  // Тунел нэмэх форм
  const [peerSel, setPeerSel] = useState('');
  const [name, setName] = useState('');
  const [remoteGw, setRemoteGw] = useState('');
  const [localSubnet, setLocalSubnet] = useState('');
  const [remoteSubnets, setRemoteSubnets] = useState('');
  const [fgTunnelName, setFgTunnelName] = useState('');
  const [peers, setPeers] = useState<MtPeerUI[] | null>(null);

  // Салбар (peer) бүртгэл
  const [knownPeers, setKnownPeers] = useState<DbPeer[]>([]);
  const [peerForm, setPeerForm] = useState<PeerForm>({ ...BLANK_PEER_FORM });

  // Нийтлэг
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const conn = () => ({ host, port: port || undefined, https, username, password });

  async function call(path: string, extra: Record<string, unknown> = {}) {
    const r = await fetch(path, { method: 'POST', headers: H, body: JSON.stringify({ ...conn(), ...extra }) });
    return r.json() as Promise<Record<string, never>>;
  }

  function loadPeers() {
    fetch('/api/mikrotik/peers-list', { method: 'POST', headers: H, body: '{}' })
      .then(r => r.json()).then((d: { ok: boolean; peers?: DbPeer[] }) => { if (d.ok) setKnownPeers(d.peers ?? []); })
      .catch(() => {});
  }

  useEffect(() => {
    loadPeers();
    fetch('/api/mikrotik/conn-get', { method: 'POST', headers: H, body: '{}' })
      .then(r => r.json()).then((d: { ok: boolean; conn?: { host: string; port: string; https: boolean; username: string; passSet: boolean } }) => {
        if (d.ok && d.conn) {
          if (d.conn.host) setHost(d.conn.host);
          if (d.conn.port) setPort(d.conn.port);
          setHttps(d.conn.https);
          if (d.conn.username) setUsername(d.conn.username);
          setConnPassSet(d.conn.passSet);
        }
      }).catch(() => {});
  }, []);

  async function saveConn() {
    if (!host || !username) { setMsg({ ok: false, text: '✗ Хаяг ба хэрэглэгч оруулна уу' }); return; }
    setBusy('conn'); setMsg(null);
    try {
      const r = await fetch('/api/mikrotik/conn-save', { method: 'POST', headers: H, body: JSON.stringify({ host, port, https, username, password }) });
      const d = await r.json() as { ok: boolean; error?: string };
      if (d.ok) { if (password) setConnPassSet(true); setMsg({ ok: true, text: '✓ Холболт хадгалагдлаа' }); }
      else setMsg({ ok: false, text: `✗ ${d.error}` });
    } catch { setMsg({ ok: false, text: '✗ Сервертэй холбогдохгүй' }); }
    finally { setBusy(''); }
  }

  async function testConn() {
    setBusy('test'); setMsg(null);
    try {
      const d = await call('/api/mikrotik/test') as unknown as { ok: boolean; error?: string; identity?: string; board?: string; version?: string };
      setMsg(d.ok ? { ok: true, text: `✓ ${d.identity} · ${d.board} · ${d.version}` } : { ok: false, text: `✗ ${d.error}` });
    } catch { setMsg({ ok: false, text: '✗ Сервертэй холбогдохгүй' }); }
    finally { setBusy(''); }
  }

  async function loadList() {
    setBusy('list'); setMsg(null);
    try {
      const d = await call('/api/mikrotik/list') as unknown as { ok: boolean; error?: string; peers?: MtPeerUI[] };
      if (d.ok) setPeers(d.peers ?? []);
      else setMsg({ ok: false, text: `✗ ${d.error}` });
    } catch { setMsg({ ok: false, text: '✗ Сервертэй холбогдохгүй' }); }
    finally { setBusy(''); }
  }

  async function addTunnel(e: React.FormEvent) {
    e.preventDefault();
    setBusy('add'); setMsg(null);
    try {
      const d = await call('/api/mikrotik/tunnel', {
        name, remoteGw, localSubnet, remoteSubnets,
        fgTunnelName: fgTunnelName.trim() || undefined,
      }) as unknown as { ok: boolean; error?: string; name?: string; subnets?: string[]; fortigate?: { ok: boolean; error?: string } | null };
      if (d.ok) {
        // Салбар нь FortiGate бол сервер spoke талыг автоматаар үүсгэсэн — үр дүнг нь хамт харуулна
        const fgText = d.fortigate == null ? ''
          : d.fortigate.ok ? ' · FortiGate тал ✓ автоматаар үүслээ'
          : ` · FortiGate тал ✗ ${d.fortigate.error}`;
        setMsg({ ok: d.fortigate == null || d.fortigate.ok, text: `✓ "${d.name}" тунел нэмэгдлээ (${(d.subnets ?? []).join(', ')})${fgText}` });
        setPeerSel(''); setName(''); setRemoteGw(''); setRemoteSubnets(''); setFgTunnelName('');
        loadList();
      } else setMsg({ ok: false, text: `✗ ${d.error}` });
    } catch { setMsg({ ok: false, text: '✗ Сервертэй холбогдохгүй' }); }
    finally { setBusy(''); }
  }

  async function delTunnel(pname: string) {
    if (!window.confirm(`"${pname}" тунелийг устгах уу?`)) return;
    setBusy('del'); setMsg(null);
    try {
      const d = await call('/api/mikrotik/tunnel/delete', { name: pname }) as unknown as { ok: boolean; error?: string };
      if (d.ok) { setMsg({ ok: true, text: `✓ "${pname}" устгагдлаа` }); loadList(); }
      else setMsg({ ok: false, text: `✗ ${d.error}` });
    } catch { setMsg({ ok: false, text: '✗ Сервертэй холбогдохгүй' }); }
    finally { setBusy(''); }
  }

  async function savePeer(e: React.FormEvent) {
    e.preventDefault();
    if (!peerForm.name.trim()) { setMsg({ ok: false, text: '✗ Салбарын нэр оруулна уу' }); return; }
    setBusy('peer'); setMsg(null);
    try {
      const r = await fetch('/api/mikrotik/peer-save', { method: 'POST', headers: H, body: JSON.stringify(peerForm) });
      const d = await r.json() as { ok: boolean; error?: string };
      if (d.ok) {
        setMsg({ ok: true, text: `✓ "${peerForm.name}" салбар хадгалагдлаа` });
        setPeerForm({ ...BLANK_PEER_FORM }); loadPeers();
      } else setMsg({ ok: false, text: `✗ ${d.error}` });
    } catch { setMsg({ ok: false, text: '✗ Сервертэй холбогдохгүй' }); }
    finally { setBusy(''); }
  }

  // Хадгалсан нэвтрэлтээр салбарын FortiGate API-г шалгана (эхлээд хадгалах шаардлагатай)
  async function testFgPeer() {
    if (peerForm.id == null) return;
    setBusy('fgtest'); setMsg(null);
    try {
      const r = await fetch('/api/mikrotik/peer-test', { method: 'POST', headers: H, body: JSON.stringify({ id: peerForm.id }) });
      const d = await r.json() as { ok: boolean; error?: string; hostname?: string; model?: string; version?: string };
      setMsg(d.ok ? { ok: true, text: `✓ ${d.hostname} · ${d.model} · ${d.version}` } : { ok: false, text: `✗ ${d.error}` });
    } catch { setMsg({ ok: false, text: '✗ Сервертэй холбогдохгүй' }); }
    finally { setBusy(''); }
  }

  async function deletePeer(p: DbPeer) {
    if (!window.confirm(`"${p.name}" салбарыг бүртгэлээс устгах уу? (Router дээрх тунелд нөлөөлөхгүй)`)) return;
    setBusy('peerdel'); setMsg(null);
    try {
      const r = await fetch('/api/mikrotik/peer-delete', { method: 'POST', headers: H, body: JSON.stringify({ id: p.id }) });
      const d = await r.json() as { ok: boolean; error?: string };
      if (d.ok) { setMsg({ ok: true, text: `✓ "${p.name}" устгагдлаа` }); if (peerForm.id === p.id) setPeerForm({ ...BLANK_PEER_FORM }); loadPeers(); }
      else setMsg({ ok: false, text: `✗ ${d.error}` });
    } catch { setMsg({ ok: false, text: '✗ Сервертэй холбогдохгүй' }); }
    finally { setBusy(''); }
  }

  const subTabStyle = (t: SubTab): React.CSSProperties => ({
    padding: '6px 16px', fontSize: 13, border: 'none', cursor: 'pointer', borderRadius: 6,
    background: subTab === t ? 'var(--accent-blue)' : 'var(--bg-elevated)',
    color: subTab === t ? 'var(--text-on-accent)' : 'var(--text-secondary)',
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Sub-tab товчлуур */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" style={subTabStyle('conn')} onClick={() => { setSubTab('conn'); setMsg(null); }}>
          MikroTik холболт
        </button>
        <button type="button" style={subTabStyle('peers')} onClick={() => { setSubTab('peers'); setMsg(null); }}>
          Салбар (peer) удирдах
        </button>
      </div>

      {/* ── MikroTik холболт таб ── */}
      {subTab === 'conn' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          <div>
            {sectionTitle('Холболтын тохиргоо')}
            <div style={{ background: 'var(--bg-page)', border: '1px solid var(--bg-elevated)', borderRadius: 8, padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr', gap: 12 }}>
                {field('Хаяг (IP)', host, setHost, '138.252.28.91')}
                {field('Порт', port, setPort, '80')}
                {field('Хэрэглэгч', username, setUsername, 'admin')}
                {field('Нууц үг', password, setPassword, connPassSet ? '•••• хадгалсан (хоосон бол ашиглана)' : '', 'password')}
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-muted)' }}>
                <input type="checkbox" checked={https} onChange={e => setHttps(e.target.checked)} /> HTTPS ашиглах (443)
              </label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <button type="button" onClick={testConn} disabled={busy === 'test'} style={btn('var(--accent-blue)')}>{busy === 'test' ? 'Шалгаж байна…' : 'Холболт шалгах'}</button>
                <button type="button" onClick={loadList} disabled={busy === 'list'} style={{ ...btn('var(--bg-elevated)'), color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>{busy === 'list' ? 'Ачаалж байна…' : 'Тунел жагсаах'}</button>
                <button type="button" onClick={saveConn} disabled={busy === 'conn'} style={{ ...btn('var(--bg-elevated)'), color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>{busy === 'conn' ? 'Хадгалж байна…' : 'Холболт хадгалах'}</button>
                {msg && <span style={{ fontSize: 12, color: msg.ok ? 'var(--accent-green)' : 'var(--accent-red)' }}>{msg.text}</span>}
              </div>
            </div>
          </div>

          {peers && (
            <div>
              {sectionTitle('Одоо байгаа тунелүүд')}
              <div style={{ background: 'var(--bg-page)', border: '1px solid var(--bg-elevated)', borderRadius: 8, padding: 8 }}>
                {peers.length === 0 && <div style={{ padding: 12, fontSize: 13, color: 'var(--text-muted)' }}>(IPsec peer алга)</div>}
                {peers.map(p => (
                  <div key={p.name} style={{ padding: '10px 12px', borderBottom: '1px solid var(--bg-elevated)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 600 }}>{p.name} <span style={{ fontFamily: 'monospace', color: 'var(--text-muted)', fontWeight: 400 }}>→ {p.address}</span></span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: p.state.startsWith('established') ? 'var(--accent-green-strong)' : 'var(--bg-elevated)', color: p.state.startsWith('established') ? 'var(--text-on-accent)' : 'var(--text-muted)' }}>{p.state}{p.uptime ? ' · ' + p.uptime : ''}</span>
                        <button type="button" onClick={() => delTunnel(p.name)} style={{ background: 'none', border: 'none', color: 'var(--accent-red)', fontSize: 12, cursor: 'pointer' }}>Устгах</button>
                      </span>
                    </div>
                    {p.subnets.map((s, i) => <span key={i} style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--text-muted)' }}>{s.src} → {s.dst} {s.ph2 ? `(${s.ph2})` : ''}</span>)}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div>
            {sectionTitle('Шинэ IPsec тунел нэмэх')}
            <form onSubmit={addTunnel} style={{ background: 'var(--bg-page)', border: '1px solid var(--bg-elevated)', borderRadius: 8, padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Peer (салбар)</label>
                  <select
                    value={peerSel}
                    onChange={e => {
                      const v = e.target.value; setPeerSel(v);
                      if (v === '__manual__') { setName(''); setRemoteGw(''); setRemoteSubnets(''); setFgTunnelName(''); return; }
                      const p = knownPeers.find(x => String(x.id) === v);
                      if (p) {
                        setRemoteGw(p.remoteGw); setName(p.name); setRemoteSubnets(p.remoteLan); setFgTunnelName('');
                      }
                      else { setRemoteGw(''); setName(''); setRemoteSubnets(''); setFgTunnelName(''); }
                    }}
                    style={inpStyle}
                  >
                    <option value="">— Сонгох —</option>
                    {knownPeers.map(p => <option key={p.id} value={String(p.id)}>{p.name} ({p.remoteGw}){p.vendor === 'fortigate' ? ' · FortiGate' : ''}{p.enabled ? '' : ' — идэвхгүй'}</option>)}
                    <option value="__manual__">＋ Гараар оруулах</option>
                  </select>
                </div>
                {field('Локал сүлжээ (src)', localSubnet, setLocalSubnet, '', 'text', true)}
              </div>
              {peerSel === '__manual__' && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  {field('Peer нэр', name, setName, 'Salbar5')}
                  {field('Алсын gateway (public IP)', remoteGw, setRemoteGw, '202.21.117.178')}
                </div>
              )}
              {field('Алсын сүлжээ(нүүд) (dst, таслалаар)', remoteSubnets, setRemoteSubnets, '172.16.100.0/24, 192.168.50.0/24')}
              {knownPeers.find(x => String(x.id) === peerSel)?.vendor === 'fortigate' && (
                (() => {
                  const p = knownPeers.find(x => String(x.id) === peerSel)!;
                  const lanIf = p.lanInterface || 'lan';
                  return (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                      {field('FortiGate тунел нэр', fgTunnelName, setFgTunnelName, 'ANPR', 'text', true)}
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Локал хаяг (address объект)</label>
                        <div style={{ ...inpStyle, color: 'var(--text-secondary)', background: 'var(--bg-elevated)' }}>
                          {lanIf}
                          <span style={{ fontSize: 11, color: 'var(--text-faint)', marginLeft: 8 }}>— Салбарын LAN interface нэрийг ашиглана</span>
                        </div>
                      </div>
                    </div>
                  );
                })()
              )}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <button type="submit" disabled={busy === 'add'} style={btn('var(--accent-green-strong)')}>{busy === 'add' ? 'Нэмж байна…' : 'Тунел нэмэх'}</button>
                <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>PSK-г "Салбар удирдах" хэсэгт хадгална · Крипто: MD5 / 3DES / DH group 2 (автоматаар) · FortiGate салбар бол spoke тал автоматаар үүснэ</span>
              </div>
            </form>
          </div>

        </div>
      )}

      {/* ── Салбар (peer) удирдах таб ── */}
      {subTab === 'peers' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {msg && <span style={{ fontSize: 12, color: msg.ok ? 'var(--accent-green)' : 'var(--accent-red)' }}>{msg.text}</span>}

          <div style={{ background: 'var(--bg-page)', border: '1px solid var(--bg-elevated)', borderRadius: 8, padding: 8 }}>
            {knownPeers.length === 0 && <div style={{ padding: 12, fontSize: 13, color: 'var(--text-muted)' }}>(Бүртгэлтэй салбар алга)</div>}
            {knownPeers.map(p => (
              <div key={p.id} style={{ padding: '10px 12px', borderBottom: '1px solid var(--bg-elevated)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                  <span style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 600 }}>
                    {p.name}
                    <span style={{ fontFamily: 'monospace', color: 'var(--text-muted)', fontWeight: 400 }}> → {p.remoteGw || '(gw алга)'}</span>
                    <span style={{ fontSize: 11, marginLeft: 8, padding: '1px 7px', borderRadius: 8, background: 'var(--bg-elevated)', color: p.vendor === 'fortigate' ? 'var(--accent-orange, #e8843c)' : 'var(--text-muted)' }}>{p.vendor === 'fortigate' ? 'FortiGate' : 'MikroTik'}</span>
                    {!p.enabled && <span style={{ fontSize: 11, marginLeft: 8, color: 'var(--accent-red)' }}>идэвхгүй</span>}
                    {p.pskSet && <span style={{ fontSize: 11, marginLeft: 8, color: 'var(--accent-green)' }}>PSK ✓</span>}
                    {p.vendor === 'fortigate' && <span style={{ fontSize: 11, marginLeft: 8, color: p.fgSet ? 'var(--accent-green)' : 'var(--accent-red)' }}>{p.fgSet ? 'API ✓' : 'API нэвтрэлт алга'}</span>}
                  </span>
                  <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--text-muted)' }}>{p.localSubnet || '?'} → {p.remoteLan || '?'}{p.note ? `  · ${p.note}` : ''}</span>
                </div>
                <span style={{ display: 'flex', gap: 12, whiteSpace: 'nowrap' }}>
                  <button type="button" onClick={() => setPeerForm({ ...BLANK_PEER_FORM, ...p, psk: '', fgToken: '', fgUser: '', fgPass: '' })} style={{ background: 'none', border: 'none', color: 'var(--accent-blue)', fontSize: 12, cursor: 'pointer' }}>Засах</button>
                  <button type="button" onClick={() => deletePeer(p)} style={{ background: 'none', border: 'none', color: 'var(--accent-red)', fontSize: 12, cursor: 'pointer' }}>Устгах</button>
                </span>
              </div>
            ))}
          </div>

          <form onSubmit={savePeer} style={{ background: 'var(--bg-page)', border: '1px solid var(--bg-elevated)', borderRadius: 8, padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{peerForm.id ? `Засаж байна: #${peerForm.id}` : 'Шинэ салбар бүртгэх'}</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {field('Салбарын нэр', peerForm.name, v => setPeerForm(f => ({ ...f, name: v })), 'Salbar5')}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Төхөөрөмж</label>
                <select value={peerForm.vendor} onChange={e => setPeerForm(f => ({ ...f, vendor: e.target.value }))} style={inpStyle}>
                  <option value="mikrotik">MikroTik</option>
                  <option value="fortigate">FortiGate</option>
                </select>
              </div>
              {field('Алсын gateway (public IP)', peerForm.remoteGw, v => setPeerForm(f => ({ ...f, remoteGw: v })), '202.21.117.178')}
              {field('Алсын сүлжээ(нүүд) (dst, таслалаар)', peerForm.remoteLan, v => setPeerForm(f => ({ ...f, remoteLan: v })), '172.16.100.0/24')}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>PSK {peerForm.pskSet && <span style={{ color: 'var(--accent-green)' }}>· хадгалсан</span>}</label>
                <input type="password" value={peerForm.psk} onChange={e => setPeerForm(f => ({ ...f, psk: e.target.value }))} placeholder={peerForm.pskSet ? '•••• хадгалсан (хоосон бол хэвээр)' : 'pre-shared-key'} style={inpStyle} />
              </div>
              {field('Тэмдэглэл', peerForm.note, v => setPeerForm(f => ({ ...f, note: v })), '')}
            </div>
            {peerForm.vendor === 'fortigate' && (
              <div style={{ borderTop: '1px solid var(--bg-elevated)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  FortiGate API — "Тунел нэмэх" дархад spoke талын тохиргоог (phase1/phase2, route, policy) автоматаар үүсгэхэд ашиглана
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                  {field('API порт', peerForm.apiPort, v => setPeerForm(f => ({ ...f, apiPort: v })), '443')}
                  {field('WAN interface', peerForm.wanInterface, v => setPeerForm(f => ({ ...f, wanInterface: v })), 'wan1')}
                  {field('LAN interface', peerForm.lanInterface, v => setPeerForm(f => ({ ...f, lanInterface: v })), 'lan')}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>API token {peerForm.fgSet && <span style={{ color: 'var(--accent-green)' }}>· хадгалсан</span>}</label>
                    <input type="password" value={peerForm.fgToken} onChange={e => setPeerForm(f => ({ ...f, fgToken: e.target.value }))} placeholder={peerForm.fgSet ? '•••• хадгалсан (хоосон бол хэвээр)' : 'REST API token'} style={inpStyle} />
                  </div>
                  {field('эсвэл Admin хэрэглэгч', peerForm.fgUser, v => setPeerForm(f => ({ ...f, fgUser: v })), 'admin')}
                  {field('Admin нууц үг', peerForm.fgPass, v => setPeerForm(f => ({ ...f, fgPass: v })), '', 'password')}
                </div>
              </div>
            )}
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-muted)' }}>
              <input type="checkbox" checked={peerForm.enabled} onChange={e => setPeerForm(f => ({ ...f, enabled: e.target.checked }))} /> Идэвхтэй
            </label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <button type="submit" disabled={busy === 'peer'} style={btn('var(--accent-blue)')}>{busy === 'peer' ? 'Хадгалж байна…' : peerForm.id ? 'Засвар хадгалах' : 'Салбар нэмэх'}</button>
              {peerForm.id != null && peerForm.vendor === 'fortigate' && (
                <button type="button" onClick={testFgPeer} disabled={busy === 'fgtest'} style={{ ...btn('var(--bg-elevated)'), color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>{busy === 'fgtest' ? 'Шалгаж байна…' : 'FortiGate шалгах'}</button>
              )}
              {peerForm.id != null && <button type="button" onClick={() => setPeerForm({ ...BLANK_PEER_FORM })} style={{ ...btn('var(--bg-elevated)'), color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>Болих</button>}
            </div>
          </form>
        </div>
      )}

    </div>
  );
}
