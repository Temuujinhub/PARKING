import type { ANPREvent } from '../types/anpr';
import { format } from 'date-fns';
import { useEffect, useRef, useState } from 'react';
import { searchEvents } from '../api/dahuaApi';

export function RetryImg({ src, alt, className, onClick, style }: { src: string; alt: string; className?: string; onClick?: (e: React.MouseEvent) => void; style?: React.CSSProperties }) {
  const [attempt, setAttempt] = useState(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  function handleError() {
    if (attempt < 4) {
      timerRef.current = setTimeout(() => setAttempt(a => a + 1), 600);
    }
  }

  return (
    <img
      src={`${src}${attempt > 0 ? `?r=${attempt}` : ''}`}
      alt={alt}
      className={className}
      style={style}
      onClick={onClick}
      onError={handleError}
    />
  );
}

interface Props {
  events: ANPREvent[];
  loading: boolean;
  token: string;
  selectedLotId?: number | null;
  onComplain?: (ev: ANPREvent) => void;
}

const DIR_LABEL: Record<string, string> = {
  entering: '↘ Орж байна',
  exiting: '↗ Гарж байна',
  unknown: '—',
};

export default function EventTable({ events, loading, token, selectedLotId, onComplain }: Props) {
  const [selected, setSelected] = useState<ANPREvent | null>(null);
  const [zoomed, setZoomed] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [searchResults, setSearchResults] = useState<ANPREvent[] | null>(null);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const q = search.trim();

  // Хайх үед зөвхөн санах ойн буфер (сүүлийн N бүртгэл) биш, тухайн өдрийн БҮХ бүртгэлээс DB-с шууд хайна
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!q) {
      debounceRef.current = setTimeout(() => { setSearchResults(null); setSearching(false); }, 0);
      return () => clearTimeout(debounceRef.current!);
    }
    debounceRef.current = setTimeout(() => {
      setSearching(true);
      searchEvents(token, { q, lotId: selectedLotId ?? null })
        .then(setSearchResults)
        .finally(() => setSearching(false));
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [q, token, selectedLotId]);

  const filtered = q ? (searchResults ?? []) : events;

  return (
    <div className="event-section">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Улсын дугаараар хайх (өнөөдрийн бүх бүртгэлээс)…"
          style={{ background: 'var(--bg-page)', border: '1px solid var(--border)', borderRadius: 6, padding: '7px 12px', color: 'var(--text-primary)', fontSize: 13, outline: 'none', width: 280 }}
        />
        {search && <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>{searching ? 'Хайж байна…' : `${filtered.length} илэрц`}</span>}
        {search && <button onClick={() => setSearch('')} style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', color: 'var(--text-muted)', borderRadius: 6, padding: '6px 10px', fontSize: 12, cursor: 'pointer' }}>Цэвэрлэх</button>}
      </div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Улсын дугаар</th>
              <th>Огноо / Цаг</th>
              <th>Итгэлцэл %</th>
              <th>Чиглэл</th>
              <th>Зогсоол</th>
              <th>Дугаарын өнгө</th>
              <th>Зураг</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={8} className="center-cell">Уншиж байна…</td>
              </tr>
            )}
            {!loading && !searching && filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="center-cell empty-state">{q ? 'Өнөөдрийн бүртгэлээс тохирох дугаар олдсонгүй' : 'Өгөгдөл байхгүй'}</td>
              </tr>
            )}
            {filtered.map((ev, i) => (
              <tr
                key={ev.id}
                className="event-row"
                onClick={() => setSelected(ev)}
              >
                <td>{i + 1}</td>
                <td className="plate-cell">{ev.plateNumber}</td>
                <td>{format(new Date(ev.timestamp), 'yyyy-MM-dd HH:mm:ss')}</td>
                <td>{ev.confidence ? `${ev.confidence}%` : '—'}</td>
                <td>{DIR_LABEL[ev.direction ?? 'unknown']}</td>
                <td>
                  {ev.parkingLotName
                    ? <span>
                        {ev.parkingLotName}
                        {ev.camDirection && (
                          <span style={{ marginLeft: 5, fontSize: 10, padding: '1px 6px', borderRadius: 10,
                            background: ev.camDirection === 'enter' ? 'var(--badge-green-bg)' : 'var(--badge-red-bg)',
                            color:      ev.camDirection === 'enter' ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                            {ev.camDirection === 'enter' ? 'Орох' : 'Гарах'}
                          </span>
                        )}
                      </span>
                    : '—'}
                </td>
                <td>{ev.plateColor ?? '—'}</td>
                <td>
                  {ev.imageUrl ? (
                    <RetryImg
                      src={ev.imageUrl}
                      alt={ev.plateNumber}
                      className="thumb"
                      onClick={e => { e.stopPropagation(); setSelected(ev); }}
                    />
                  ) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {zoomed && (
        <div className="zoom-overlay" onClick={() => setZoomed(null)}>
          <img src={zoomed} alt="zoom" className="zoom-img" />
        </div>
      )}

      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setSelected(null)}>✕</button>
            <h3 className="plate-large">{selected.plateNumber}</h3>
            <p>{format(new Date(selected.timestamp), 'yyyy-MM-dd HH:mm:ss')}</p>
            <div className="modal-images">
              {selected.imageUrl && (
                <div>
                  <p>Бүртгэлийн зураг</p>
                  <RetryImg
                    src={selected.imageUrl}
                    alt={selected.plateNumber}
                    style={{ cursor: 'zoom-in' }}
                    onClick={() => setZoomed(selected.imageUrl!)}
                  />
                </div>
              )}
            </div>
            <table className="detail-table">
              <tbody>
                {selected.parkingLotName && (
                  <tr><td>Зогсоол</td><td>{selected.parkingLotName}{selected.camDirection && ` · ${selected.camDirection === 'enter' ? 'Орох' : 'Гарах'}`}</td></tr>
                )}
                <tr><td>Итгэлцэл</td><td>{selected.confidence}%</td></tr>
                <tr><td>Чиглэл</td><td>{DIR_LABEL[selected.direction ?? 'unknown']}</td></tr>
                <tr><td>Тэрэгний өнгө</td><td>{selected.vehicleColor ?? '—'}</td></tr>
                <tr><td>Дугаарын өнгө</td><td>{selected.plateColor ?? '—'}</td></tr>
              </tbody>
            </table>
            {onComplain && (
              <button
                className="btn"
                style={{ marginTop: 14, fontSize: 13, padding: '7px 18px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', color: 'var(--accent-yellow)', cursor: 'pointer' }}
                onClick={() => { onComplain(selected); setSelected(null); }}
              >
                Гомдол
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
