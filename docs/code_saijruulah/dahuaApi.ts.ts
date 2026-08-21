import type { ANPREvent, CameraConfig } from '../types/anpr';

export type { ANPREvent };

// Push camera credentials to the Vite server; it reconnects the event stream
export async function applyConfig(cfg: CameraConfig): Promise<boolean> {
  const res = await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ip: cfg.ip, port: cfg.port, username: cfg.username, password: cfg.password }),
  });
  return res.ok;
}

// Test camera connectivity via Digest-auth proxy
export async function testConnection(): Promise<boolean> {
  const res = await fetch('/camera/cgi-bin/magicBox.cgi?action=getSystemInfo');
  return res.ok;
}

// Subscribe to real-time ANPR events via SSE
export function subscribeEvents(
  onEvent: (ev: ANPREvent) => void,
  onError?: (msg: string) => void,
  onImageUpdate?: (id: string, imageUrl: string) => void,
  onConfig?: (cfg: { maxEvents: number }) => void,
  onOpen?: () => void
): () => void {
  const source = new EventSource('/api/events');

  // Fires on initial connect and on every successful auto-reconnect —
  // used to clear a stale "disconnected" banner once the stream is healthy again.
  source.onopen = () => onOpen?.();

  source.onmessage = (e) => {
    try {
      const raw = JSON.parse(e.data) as ANPREvent;
      const ev: ANPREvent = {
        ...raw,
        direction: (raw.direction as ANPREvent['direction']) ?? 'unknown',
      };
      onEvent(ev);
    } catch {
      // ignore parse errors
    }
  };

  source.addEventListener('config', (e) => {
    try {
      const cfg = JSON.parse((e as MessageEvent).data) as { maxEvents: number };
      onConfig?.(cfg);
    } catch {}
  });

  source.addEventListener('imageUpdate', (e) => {
    try {
      const { id, imageUrl } = JSON.parse((e as MessageEvent).data) as { id: string; imageUrl: string };
      onImageUpdate?.(id, imageUrl);
    } catch {}
  });

  source.onerror = () => {
    onError?.('Event stream disconnected — reconnecting…');
  };

  return () => source.close();
}

// Get all buffered events (REST fallback)
export async function fetchBufferedEvents(): Promise<ANPREvent[]> {
  const res = await fetch('/api/events/list');
  if (!res.ok) return [];
  const raw = await res.json() as ANPREvent[];
  return raw.map(r => ({ ...r, direction: (r.direction as ANPREvent['direction']) ?? 'unknown' }));
}

// Search a given day's events by plate number — hits the DB directly, not limited
// to the in-memory buffer (defaults to today when `date` is omitted)
export async function searchEvents(token: string, opts: { q: string; date?: string; lotId?: number | null }): Promise<ANPREvent[]> {
  const params = new URLSearchParams();
  params.set('q', opts.q);
  if (opts.date) params.set('date', opts.date);
  if (opts.lotId != null) params.set('lotId', String(opts.lotId));
  const res = await fetch(`/api/events/search?${params.toString()}`, { headers: { 'X-Auth-Token': token } });
  if (!res.ok) return [];
  const raw = await res.json() as ANPREvent[];
  return raw.map(r => ({ ...r, direction: (r.direction as ANPREvent['direction']) ?? 'unknown' }));
}
