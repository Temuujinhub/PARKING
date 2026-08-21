import { useEffect, useRef, useState } from 'react';
import { format } from 'date-fns';
import './App.css';
import ControlBar from './components/ControlBar';
import EventTable, { RetryImg } from './components/EventTable';
import OpenReasonModal from './components/OpenReasonModal';
import AdminPanel from './components/AdminPanel';
import ComplaintManagement from './components/ComplaintManagement';
import ReportsPage from './components/ReportsPage';
import LoginPage from './components/LoginPage';
import ParkingSelector from './components/ParkingSelector';
import CameraStatus from './components/CameraStatus';
import CameraStatusPage from './components/CameraStatusPage';
import { useANPR } from './hooks/useANPR';
import type { ANPREvent } from './types/anpr';

interface MenuPerm { view: boolean; edit: boolean; delete: boolean; create: boolean }
interface AuthInfo { token: string; username: string; role: string; permissions?: Record<string, MenuPerm> }
interface ComplaintPrefill { plateNumber: string; location: string; occurredAt: string }

function getStoredAuth(): AuthInfo | null {
  try {
    const token = sessionStorage.getItem('anpr_token');
    const user  = sessionStorage.getItem('anpr_user');
    if (token && user) return { token, ...JSON.parse(user) };
  } catch {}
  return null;
}

type Theme = 'dark' | 'light';
function getStoredTheme(): Theme {
  return localStorage.getItem('anpr_theme') === 'light' ? 'light' : 'dark';
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(getStoredTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('anpr_theme', theme);
  }, [theme]);

  const [auth, setAuth] = useState<AuthInfo | null>(getStoredAuth);
  const [showAdmin, setShowAdmin] = useState(false);
  const [showComplaints, setShowComplaints] = useState(false);
  const [showCameras, setShowCameras] = useState(false);
  const [showReports, setShowReports] = useState(false);
  const [selectedLotId, setSelectedLotId] = useState<number | null>(null);
  const [selectedCamId, setSelectedCamId] = useState<number | null>(null);

  // Validate stored token on mount — clear if server no longer recognises it
  useEffect(() => {
    const stored = getStoredAuth();
    if (!stored) return;
    fetch('/api/auth/me', { headers: { 'X-Auth-Token': stored.token } })
      .then(r => { if (!r.ok) { sessionStorage.removeItem('anpr_token'); sessionStorage.removeItem('anpr_user'); setAuth(null); } })
      .catch(() => {});
  }, []);
  const { events, state, clearEvents } = useANPR();
  const [snapSrc, setSnapSrc] = useState('');
  const [streamError, setStreamError] = useState(false);
  const [streamLoading, setStreamLoading] = useState(false);
  const [barrierMsg, setBarrierMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [barrierBusy, setBarrierBusy] = useState<'open'|'close'|null>(null);
  const [showReasonModal, setShowReasonModal] = useState(false);
  const [zoomedCam, setZoomedCam] = useState(false);
  const [complaintPrefill, setComplaintPrefill] = useState<ComplaintPrefill | null>(null);
  const STREAM_TIMEOUT = 120;
  const [streamSecsLeft, setStreamSecsLeft] = useState(0);
  const streamTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (streamTimerRef.current) clearInterval(streamTimerRef.current);
    if (selectedCamId === null) { setStreamSecsLeft(0); return; }
    setStreamSecsLeft(STREAM_TIMEOUT);
    streamTimerRef.current = setInterval(() => {
      setStreamSecsLeft(s => {
        if (s <= 1) {
          clearInterval(streamTimerRef.current!);
          handleSelectCamera(null);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => { if (streamTimerRef.current) clearInterval(streamTimerRef.current); };
  }, [selectedCamId]);

  function handleComplain(ev: ANPREvent) {
    setComplaintPrefill({
      plateNumber: ev.plateNumber ?? '',
      location: ev.parkingLotName ?? '',
      occurredAt: format(new Date(ev.timestamp), "yyyy-MM-dd'T'HH:mm"),
    });
    setShowAdmin(false); setShowCameras(false); setShowComplaints(true);
  }


  async function handleSelectCamera(camId: number | null) {
    setSelectedCamId(camId);
    setStreamError(false);
    if (camId === null) { setSnapSrc(''); setStreamLoading(false); return; }
    setStreamLoading(true);
    setSnapSrc('');
    await fetch('/api/stream-switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Auth-Token': auth!.token },
      body: JSON.stringify({ camId }),
    });
    setStreamLoading(false);
    setSnapSrc(`/api/stream?t=${Date.now()}`);
  }

  function handleLogin(result: AuthInfo) {
    setAuth(result);
  }

  async function handleBarrier(action: 'open' | 'close', reason?: string) {
    if (!auth || barrierBusy) return;
    setBarrierBusy(action);
    setBarrierMsg(null);
    try {
      const r = await fetch('/api/barrier-open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Auth-Token': auth.token },
        body: JSON.stringify({ camId: selectedCamId, action, reason }),
      });
      const d = await r.json() as { ok: boolean; msg?: string; error?: string };
      setBarrierMsg({ ok: d.ok, text: d.ok ? (d.msg ?? (action === 'open' ? 'Хаалт нээгдлээ' : 'Хаалт хаагдлаа')) : (d.msg ?? d.error ?? 'Алдаа гарлаа') });
    } catch {
      setBarrierMsg({ ok: false, text: 'Холбогдохгүй байна' });
    } finally {
      setBarrierBusy(null);
      setTimeout(() => setBarrierMsg(null), 4000);
    }
  }

  function handleLogout() {
    sessionStorage.removeItem('anpr_token');
    sessionStorage.removeItem('anpr_user');
    setAuth(null);
  }

  if (!auth) {
    return <LoginPage onLogin={handleLogin} />;
  }

  const isStaff = auth.role === 'admin' || auth.role === 'operator' || auth.role === 'manager';

  // Гомдлын эрх (admin бүх, operator харах+засах, manager тохиргоогоор)
  const capComplaints = (action: 'view' | 'edit' | 'delete' | 'create'): boolean => {
    if (auth.role === 'admin') return true;
    if (auth.role === 'operator') return action !== 'delete';
    if (auth.role === 'manager') return !!auth.permissions?.complaints?.[action];
    return false;
  };
  const canComplaints = capComplaints('view');

  // Тайлангийн эрх (admin бүх, operator зөвхөн харах, manager тохиргоогоор)
  const capReports = (action: 'view' | 'edit' | 'delete' | 'create'): boolean => {
    if (auth.role === 'admin') return true;
    if (auth.role === 'operator') return action === 'view';
    if (auth.role === 'manager') return !!auth.permissions?.reports?.[action];
    return false;
  };
  const canReports = capReports('view');

  // ── Menubar навигаци ──
  const activeView: 'home' | 'complaints' | 'cameras' | 'admin' | 'reports' =
    showAdmin ? 'admin' : showComplaints ? 'complaints' : showCameras ? 'cameras' : showReports ? 'reports' : 'home';
  const go = (v: 'home' | 'complaints' | 'cameras' | 'admin' | 'reports') => {
    setShowAdmin(v === 'admin'); setShowComplaints(v === 'complaints'); setShowCameras(v === 'cameras'); setShowReports(v === 'reports');
  };
  const menuCls = (v: string) => `menu-item${activeView === v ? ' active' : ''}`;

  const header = (
    <header className="app-header">
      <div className="header-left">
        <img src="/Easy-Parking-Logo-04.webp" alt="Easy Parking" className="header-logo-img" onClick={() => go('home')} title="Нүүр" />
        <nav className="menubar">
          <button className={menuCls('home')} onClick={() => go('home')}>Нүүр</button>
          {canComplaints && <button className={menuCls('complaints')} onClick={() => go('complaints')}>Гомдол</button>}
          {canReports && <button className={menuCls('reports')} onClick={() => go('reports')}>Тайлан</button>}
          <CameraStatus token={auth.token} active={activeView === 'cameras'} onOpen={() => go('cameras')} />
          {isStaff && <button className={menuCls('admin')} onClick={() => go('admin')}>Удирдлага</button>}
        </nav>
      </div>
      <div className="header-right">
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{auth.username}</span>
        <button
          className="theme-toggle-btn"
          onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
          title={theme === 'dark' ? 'Цагаан горим' : 'Хар горим'}
        >
          {theme === 'dark' ? '☀' : '🌙'}
        </button>
        <button className="logout-btn" onClick={handleLogout}>Гарах</button>
      </div>
    </header>
  );

  if (showCameras) {
    return (
      <div className="app">
        {header}
        <div className="admin-page">
          <div className="admin-panel-body">
            <CameraStatusPage token={auth.token} />
          </div>
        </div>
      </div>
    );
  }

  if (showComplaints && canComplaints) {
    return (
      <div className="app">
        {header}
        <div className="admin-page">
          <div className="admin-panel-body">
            <ComplaintManagement token={auth.token} username={auth.username} role={auth.role} canEdit={capComplaints('edit')} canDelete={capComplaints('delete')} canCreate={capComplaints('create')} prefill={complaintPrefill} onPrefillConsumed={() => setComplaintPrefill(null)} />
          </div>
        </div>
      </div>
    );
  }

  if (showReports && canReports) {
    return (
      <div className="app">
        {header}
        <div className="admin-page">
          <div className="admin-panel-body">
            <ReportsPage token={auth.token} />
          </div>
        </div>
      </div>
    );
  }

  if (showAdmin && isStaff) {
    return (
      <div className="app">
        {header}
        <AdminPanel token={auth.token} role={auth.role} permissions={auth.permissions} onClose={() => setShowAdmin(false)} />
      </div>
    );
  }

  return (
    <div className="app">
      {header}

      <main className="app-main">
        <div className="top-row">
          <div className="latest-capture-card">
            <div className="latest-capture-header">
              <span className="latest-capture-dot" />
              Сүүлийн бүртгэл
            </div>
            {events[0] ? (
              <div className="latest-capture-body">
                {events[0].imageUrl && (
                  <RetryImg
                    className="latest-capture-img"
                    src={events[0].imageUrl}
                    alt={events[0].plateNumber}
                  />
                )}
                <div className={events[0].imageUrl ? 'latest-capture-overlay' : 'latest-capture-overlay latest-capture-overlay-noimg'}>
                  <span className="latest-capture-plate">{events[0].plateNumber}</span>
                  <div className="latest-capture-meta">
                    <span>{format(new Date(events[0].timestamp), 'yyyy-MM-dd HH:mm:ss')}</span>
                    {events[0].parkingLotName && (
                      <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                        {events[0].parkingLotName}
                        {events[0].camDirection && (
                          <span style={{
                            fontSize: 10, padding: '1px 6px', borderRadius: 10,
                            background: events[0].camDirection === 'enter' ? 'var(--badge-green-bg)' : 'var(--badge-red-bg)',
                            color: events[0].camDirection === 'enter' ? 'var(--accent-green)' : 'var(--accent-red)',
                          }}>
                            {events[0].camDirection === 'enter' ? 'Орох' : 'Гарах'}
                          </span>
                        )}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="latest-capture-empty">
                Бүртгэл байхгүй
              </div>
            )}
          </div>

          {selectedCamId !== null && (snapSrc || streamLoading) && (
            <div className="snapshot-card">
              <div className="snapshot-header">
                Зогсоолын камер
                {streamSecsLeft > 0 && (
                  <span style={{ fontSize: 11, color: streamSecsLeft <= 30 ? 'var(--accent-red)' : 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
                    {Math.floor(streamSecsLeft / 60)}:{String(streamSecsLeft % 60).padStart(2, '0')}
                  </span>
                )}
                {selectedCamId !== null && (
                  <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <button
                      onClick={() => { if (!barrierBusy) setShowReasonModal(true); }}
                      disabled={barrierBusy !== null}
                      style={{ fontSize: 11, padding: '3px 10px', background: 'var(--badge-green-bg2)', border: '1px solid var(--accent-green)', color: 'var(--accent-green)', borderRadius: 4, cursor: barrierBusy ? 'not-allowed' : 'pointer', opacity: barrierBusy === 'open' ? 0.6 : 1 }}
                      title="Хаалт нээх"
                    >
                      {barrierBusy === 'open' ? '...' : '↑ Нээх'}
                    </button>
                    <button
                      onClick={() => handleBarrier('close')}
                      disabled={barrierBusy !== null}
                      style={{ fontSize: 11, padding: '3px 10px', background: 'var(--badge-red-bg)', border: '1px solid var(--accent-red)', color: 'var(--accent-red)', borderRadius: 4, cursor: barrierBusy ? 'not-allowed' : 'pointer', opacity: barrierBusy === 'close' ? 0.6 : 1 }}
                      title="Хаалт хаах"
                    >
                      {barrierBusy === 'close' ? '...' : '↓ Хаах'}
                    </button>
                    <button
                      onClick={() => handleSelectCamera(null)}
                      style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 13, padding: '2px 7px', borderRadius: 4, lineHeight: 1 }}
                      title="Камер хаах"
                    >✕</button>
                  </div>
                )}
              </div>
              <div style={{ height: 3, background: 'var(--bg-elevated)' }}>
                <div style={{ height: '100%', background: streamSecsLeft <= 30 ? 'var(--accent-red)' : 'var(--accent-blue)', transition: 'width 1s linear', width: `${(streamSecsLeft / STREAM_TIMEOUT) * 100}%` }} />
              </div>
              {barrierMsg && (
                <div style={{ padding: '4px 14px', fontSize: 11, background: barrierMsg.ok ? 'var(--badge-green-bg)' : 'var(--badge-red-bg)', color: barrierMsg.ok ? 'var(--accent-green)' : 'var(--accent-red)', borderBottom: '1px solid var(--border)' }}>
                  {barrierMsg.text}
                </div>
              )}
              {streamLoading ? (
                <div className="snap-placeholder">Камер холбогдож байна...</div>
              ) : !streamError ? (
                <img className="snapshot-img" src={snapSrc} alt="Live" onError={() => setStreamError(true)} onClick={() => setZoomedCam(true)} style={{ cursor: 'zoom-in' }} />
              ) : (
                <div className="snap-placeholder" style={{ flexDirection: 'column', gap: 8, height: 100 }}>
                  <span style={{ color: 'var(--accent-red)' }}>Stream холбогдохгүй байна</span>
                  {selectedCamId !== null && (
                    <button
                      onClick={() => handleSelectCamera(selectedCamId)}
                      style={{ fontSize: 11, padding: '4px 12px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', color: 'var(--text-secondary)', borderRadius: 4, cursor: 'pointer' }}
                    >
                      Дахин оролдох
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {zoomedCam && snapSrc && (
          <div className="zoom-overlay" onClick={() => setZoomedCam(false)}>
            <div
              onClick={e => e.stopPropagation()}
              style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', width: 'min(90vw, 1100px)' }}
            >
              <div className="snapshot-header">
                Зогсоолын камер
                {streamSecsLeft > 0 && (
                  <span style={{ fontSize: 11, color: streamSecsLeft <= 30 ? 'var(--accent-red)' : 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
                    {Math.floor(streamSecsLeft / 60)}:{String(streamSecsLeft % 60).padStart(2, '0')}
                  </span>
                )}
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <button
                    onClick={() => { if (!barrierBusy) setShowReasonModal(true); }}
                    disabled={barrierBusy !== null}
                    style={{ fontSize: 12, padding: '4px 14px', background: 'var(--badge-green-bg2)', border: '1px solid var(--accent-green)', color: 'var(--accent-green)', borderRadius: 4, cursor: barrierBusy ? 'not-allowed' : 'pointer', opacity: barrierBusy === 'open' ? 0.6 : 1 }}
                  >{barrierBusy === 'open' ? '...' : '↑ Нээх'}</button>
                  <button
                    onClick={() => handleBarrier('close')}
                    disabled={barrierBusy !== null}
                    style={{ fontSize: 12, padding: '4px 14px', background: 'var(--badge-red-bg)', border: '1px solid var(--accent-red)', color: 'var(--accent-red)', borderRadius: 4, cursor: barrierBusy ? 'not-allowed' : 'pointer', opacity: barrierBusy === 'close' ? 0.6 : 1 }}
                  >{barrierBusy === 'close' ? '...' : '↓ Хаах'}</button>
                  <button
                    onClick={() => setZoomedCam(false)}
                    style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 14, padding: '3px 9px', borderRadius: 4, lineHeight: 1 }}
                  >✕</button>
                </div>
              </div>
              <div style={{ height: 3, background: 'var(--bg-elevated)' }}>
                <div style={{ height: '100%', background: streamSecsLeft <= 30 ? 'var(--accent-red)' : 'var(--accent-blue)', transition: 'width 1s linear', width: `${(streamSecsLeft / STREAM_TIMEOUT) * 100}%` }} />
              </div>
              {barrierMsg && (
                <div style={{ padding: '4px 14px', fontSize: 12, background: barrierMsg.ok ? 'var(--badge-green-bg)' : 'var(--badge-red-bg)', color: barrierMsg.ok ? 'var(--accent-green)' : 'var(--accent-red)', borderBottom: '1px solid var(--border)' }}>
                  {barrierMsg.text}
                </div>
              )}
              <img src={snapSrc} alt="Live" style={{ width: '100%', display: 'block', maxHeight: '80vh', objectFit: 'contain' }} />
            </div>
          </div>
        )}

        {state.error && <div className="error-banner">Алдаа: {state.error}</div>}

        <ParkingSelector
          token={auth.token}
          selectedLotId={selectedLotId}
          onSelect={setSelectedLotId}
          selectedCamId={selectedCamId}
          onSelectCamera={handleSelectCamera}
        />

        <ControlBar onClear={clearEvents} eventCount={events.length} connected={state.connected} />
        <EventTable
          events={selectedLotId ? events.filter(e => e.parkingLotId === selectedLotId) : events}
          loading={false}
          token={auth.token}
          selectedLotId={selectedLotId}
          onComplain={capComplaints('create') ? handleComplain : undefined}
        />
      </main>

      {showReasonModal && (
        <OpenReasonModal
          token={auth.token}
          onConfirm={reason => { setShowReasonModal(false); handleBarrier('open', reason); }}
          onCancel={() => setShowReasonModal(false)}
        />
      )}
    </div>
  );
}
