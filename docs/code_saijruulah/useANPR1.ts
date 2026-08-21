import { useState, useCallback, useEffect, useRef } from 'react';
import type { ANPREvent, CameraConfig, FetchState } from '../types/anpr';
import { applyConfig, testConnection, subscribeEvents, fetchBufferedEvents } from '../api/dahuaApi';

export function useANPR() {
  const [events, setEvents] = useState<ANPREvent[]>([]);
  const [state, setState] = useState<FetchState>({ loading: false, error: null, connected: false });
  const [config, setConfig] = useState<CameraConfig>({
    ip: '10.0.119.10',
    port: 80,
    username: 'admin',
    password: 'admin123',
    channel: 1,
  });
  const unsubRef = useRef<(() => void) | null>(null);
  const maxEventsRef = useRef(200);

  const connect = useCallback(async () => {
    setState(s => ({ ...s, loading: true, error: null, connected: false }));
    try {
      // 1. Push credentials to Vite server (it will reconnect the camera stream)
      const applied = await applyConfig(config);
      if (!applied) throw new Error('Config хадгалах амжилтгүй боллоо');

      // 2. Wait briefly for stream to establish, then test connectivity
      await new Promise(r => setTimeout(r, 1200));
      const ok = await testConnection();
      if (!ok) throw new Error(`Камерт холбогдож чадсангүй (${config.ip}:${config.port})`);

      setState(s => ({ ...s, connected: true, loading: false, error: null }));
      // 3. Load any events already buffered during reconnect window
      const buffered = await fetchBufferedEvents();
      if (buffered.length) setEvents(buffered);
    } catch (e: unknown) {
      setState(s => ({ ...s, error: (e instanceof Error ? e.message : String(e)), loading: false, connected: false }));
    }
  }, [config]);

  // Auto-start SSE subscription on mount
  useEffect(() => {
    const unsub = subscribeEvents(
      (ev) => {
        setEvents(prev => {
          // Avoid duplicates
          if (prev.some(e => e.id === ev.id)) return prev;
          return [ev, ...prev].slice(0, maxEventsRef.current);
        });
        // Events flowing ⇒ stream is healthy; clear any stale "disconnected" banner.
        setState(s => (s.error || !s.connected ? { ...s, connected: true, error: null } : s));
      },
      (msg) => setState(s => ({ ...s, error: msg })),
      (id, imageUrl) => {
        setEvents(prev => prev.map(e => e.id === id ? { ...e, imageUrl } : e));
      },
      (cfg) => {
        if (cfg.maxEvents > 0) {
          maxEventsRef.current = cfg.maxEvents;
          setEvents(prev => prev.length > cfg.maxEvents ? prev.slice(0, cfg.maxEvents) : prev);
        }
      },
      // onOpen: SSE (re)connected — mark connected and drop the reconnecting banner.
      () => setState(s => ({ ...s, connected: true, error: null }))
    );
    unsubRef.current = unsub;

    // Also load buffered events on mount
    fetchBufferedEvents().then(evs => {
      if (evs.length) setEvents(evs);
    });

    return () => unsub();
  }, []);

  const clearEvents = useCallback(() => setEvents([]), []);

  return { events, state, config, setConfig, connect, clearEvents };
}
