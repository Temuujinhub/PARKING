import { useEffect, useRef, useState } from 'react';

interface OpenReason { id: number; label: string }
interface Props {
  token: string;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

export default function OpenReasonModal({ token, onConfirm, onCancel }: Props) {
  const [reasons, setReasons] = useState<OpenReason[]>([]);
  const [customText, setCustomText] = useState('');
  const [showCustom, setShowCustom] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const H = { 'Content-Type': 'application/json', 'X-Auth-Token': token };

  useEffect(() => {
    fetch('/api/open-reasons', { headers: H })
      .then(r => r.ok ? r.json() : [])
      .then(setReasons);
  }, []);

  useEffect(() => {
    if (showCustom) setTimeout(() => inputRef.current?.focus(), 50);
  }, [showCustom]);

  function handleReason(label: string) {
    if (label === 'Бусад') {
      setShowCustom(true);
    } else {
      onConfirm(label);
    }
  }

  function handleCustomSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = customText.trim();
    if (!text) return;
    onConfirm(`Бусад: ${text}`);
  }

  return (
    <div className="modal-overlay modal-overlay-top" onClick={onCancel}>
      <div className="modal" style={{ maxWidth: 420 }} onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={onCancel}>✕</button>
        <h3 style={{ margin: '0 0 6px', fontSize: 16, color: 'var(--text-secondary)' }}>Нээх шалтгаан</h3>
        <p style={{ margin: '0 0 18px', fontSize: 12, color: 'var(--text-muted)' }}>Шалтгааныг сонгоно уу</p>

        {!showCustom ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {reasons.map(r => (
              <button
                key={r.id}
                onClick={() => handleReason(r.label)}
                style={{
                  width: '100%', padding: '10px 14px', textAlign: 'left',
                  background: 'var(--badge-green-bg2)', border: '1px solid #3fb95066',
                  borderRadius: 6, color: 'var(--text-secondary)', fontSize: 14,
                  cursor: 'pointer', transition: 'background 0.15s',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--badge-green-bg-hover)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'var(--badge-green-bg2)')}
              >
                ↑ {r.label}
              </button>
            ))}
            {reasons.length === 0 && (
              <div style={{ color: 'var(--text-faint)', fontSize: 13, textAlign: 'center', padding: 16 }}>
                Шалтгаан байхгүй
              </div>
            )}
          </div>
        ) : (
          <form onSubmit={handleCustomSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>Тайлбар бичнэ үү:</div>
            <input
              ref={inputRef}
              value={customText}
              onChange={e => setCustomText(e.target.value)}
              placeholder="Шалтгааны тайлбар..."
              style={{
                background: 'var(--bg-page)', border: '1px solid var(--border)', borderRadius: 6,
                padding: '9px 12px', color: 'var(--text-primary)', fontSize: 14, outline: 'none',
              }}
              onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent-blue)')}
              onBlur={e => (e.currentTarget.style.borderColor = 'var(--border)')}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                type="button"
                onClick={() => { setShowCustom(false); setCustomText(''); }}
                style={{ flex: 1, padding: '8px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text-muted)', fontSize: 13, cursor: 'pointer' }}
              >
                ← Буцах
              </button>
              <button
                type="submit"
                disabled={!customText.trim()}
                style={{ flex: 2, padding: '8px', background: 'var(--badge-green-bg2)', border: '1px solid #3fb95066', borderRadius: 6, color: 'var(--accent-green)', fontSize: 13, cursor: 'pointer', opacity: customText.trim() ? 1 : 0.5 }}
              >
                ↑ Нээх
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
