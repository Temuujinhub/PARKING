const useState = __vite__cjsImport0_react["useState"]; const useCallback = __vite__cjsImport0_react["useCallback"]; const useEffect = __vite__cjsImport0_react["useEffect"]; const useRef = __vite__cjsImport0_react["useRef"];import __vite__cjsImport0_react from "/node_modules/.vite/deps/react.js?v=7077b528";
import { applyConfig, testConnection, subscribeEvents, fetchBufferedEvents } from "/src/api/dahuaApi.ts";
export function useANPR() {
	const [events, setEvents] = useState([]);
	const [state, setState] = useState({
		loading: false,
		error: null,
		connected: false
	});
	const [config, setConfig] = useState({
		ip: "10.0.119.10",
		port: 80,
		username: "admin",
		password: "admin123",
		channel: 1
	});
	const unsubRef = useRef(null);
	const maxEventsRef = useRef(200);
	const connect = useCallback(async () => {
		setState((s) => ({
			...s,
			loading: true,
			error: null,
			connected: false
		}));
		try {
			// 1. Push credentials to Vite server (it will reconnect the camera stream)
			const applied = await applyConfig(config);
			if (!applied) throw new Error("Config хадгалах амжилтгүй боллоо");
			// 2. Wait briefly for stream to establish, then test connectivity
			await new Promise((r) => setTimeout(r, 1200));
			const ok = await testConnection();
			if (!ok) throw new Error(`Камерт холбогдож чадсангүй (${config.ip}:${config.port})`);
			setState((s) => ({
				...s,
				connected: true,
				loading: false,
				error: null
			}));
			// 3. Load any events already buffered during reconnect window
			const buffered = await fetchBufferedEvents();
			if (buffered.length) setEvents(buffered);
		} catch (e) {
			setState((s) => ({
				...s,
				error: e instanceof Error ? e.message : String(e),
				loading: false,
				connected: false
			}));
		}
	}, [config]);
	// Auto-start SSE subscription on mount
	useEffect(() => {
		const unsub = subscribeEvents(
			(ev) => {
				setEvents((prev) => {
					// Avoid duplicates
					if (prev.some((e) => e.id === ev.id)) return prev;
					return [ev, ...prev].slice(0, maxEventsRef.current);
				});
				// Events flowing ⇒ stream is healthy; clear any stale "disconnected" banner.
				setState((s) => s.error || !s.connected ? {
					...s,
					connected: true,
					error: null
				} : s);
			},
			(msg) => setState((s) => ({
				...s,
				error: msg
			})),
			(id, imageUrl) => {
				setEvents((prev) => prev.map((e) => e.id === id ? {
					...e,
					imageUrl
				} : e));
			},
			(cfg) => {
				if (cfg.maxEvents > 0) {
					maxEventsRef.current = cfg.maxEvents;
					setEvents((prev) => prev.length > cfg.maxEvents ? prev.slice(0, cfg.maxEvents) : prev);
				}
			},
			// onOpen: SSE (re)connected — mark connected and drop the reconnecting banner.
			() => setState((s) => ({
				...s,
				connected: true,
				error: null
			}))
		);
		unsubRef.current = unsub;
		// Also load buffered events on mount
		fetchBufferedEvents().then((evs) => {
			if (evs.length) setEvents(evs);
		});
		return () => unsub();
	}, []);
	const clearEvents = useCallback(() => setEvents([]), []);
	return {
		events,
		state,
		config,
		setConfig,
		connect,
		clearEvents
	};
}

//# sourceMappingURL=data:application/json;base64,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LCJuYW1lcyI6W10sInNvdXJjZXMiOlsidXNlQU5QUi50cyJdLCJ2ZXJzaW9uIjozLCJzb3VyY2VzQ29udGVudCI6WyJpbXBvcnQgeyB1c2VTdGF0ZSwgdXNlQ2FsbGJhY2ssIHVzZUVmZmVjdCwgdXNlUmVmIH0gZnJvbSAncmVhY3QnO1xuaW1wb3J0IHR5cGUgeyBBTlBSRXZlbnQsIENhbWVyYUNvbmZpZywgRmV0Y2hTdGF0ZSB9IGZyb20gJy4uL3R5cGVzL2FucHInO1xuaW1wb3J0IHsgYXBwbHlDb25maWcsIHRlc3RDb25uZWN0aW9uLCBzdWJzY3JpYmVFdmVudHMsIGZldGNoQnVmZmVyZWRFdmVudHMgfSBmcm9tICcuLi9hcGkvZGFodWFBcGknO1xuXG5leHBvcnQgZnVuY3Rpb24gdXNlQU5QUigpIHtcbiAgY29uc3QgW2V2ZW50cywgc2V0RXZlbnRzXSA9IHVzZVN0YXRlPEFOUFJFdmVudFtdPihbXSk7XG4gIGNvbnN0IFtzdGF0ZSwgc2V0U3RhdGVdID0gdXNlU3RhdGU8RmV0Y2hTdGF0ZT4oeyBsb2FkaW5nOiBmYWxzZSwgZXJyb3I6IG51bGwsIGNvbm5lY3RlZDogZmFsc2UgfSk7XG4gIGNvbnN0IFtjb25maWcsIHNldENvbmZpZ10gPSB1c2VTdGF0ZTxDYW1lcmFDb25maWc+KHtcbiAgICBpcDogJzEwLjAuMTE5LjEwJyxcbiAgICBwb3J0OiA4MCxcbiAgICB1c2VybmFtZTogJ2FkbWluJyxcbiAgICBwYXNzd29yZDogJ2FkbWluMTIzJyxcbiAgICBjaGFubmVsOiAxLFxuICB9KTtcbiAgY29uc3QgdW5zdWJSZWYgPSB1c2VSZWY8KCgpID0+IHZvaWQpIHwgbnVsbD4obnVsbCk7XG4gIGNvbnN0IG1heEV2ZW50c1JlZiA9IHVzZVJlZigyMDApO1xuXG4gIGNvbnN0IGNvbm5lY3QgPSB1c2VDYWxsYmFjayhhc3luYyAoKSA9PiB7XG4gICAgc2V0U3RhdGUocyA9PiAoeyAuLi5zLCBsb2FkaW5nOiB0cnVlLCBlcnJvcjogbnVsbCwgY29ubmVjdGVkOiBmYWxzZSB9KSk7XG4gICAgdHJ5IHtcbiAgICAgIC8vIDEuIFB1c2ggY3JlZGVudGlhbHMgdG8gVml0ZSBzZXJ2ZXIgKGl0IHdpbGwgcmVjb25uZWN0IHRoZSBjYW1lcmEgc3RyZWFtKVxuICAgICAgY29uc3QgYXBwbGllZCA9IGF3YWl0IGFwcGx5Q29uZmlnKGNvbmZpZyk7XG4gICAgICBpZiAoIWFwcGxpZWQpIHRocm93IG5ldyBFcnJvcignQ29uZmlnINGF0LDQtNCz0LDQu9Cw0YUg0LDQvNC20LjQu9GC0LPSr9C5INCx0L7Qu9C70L7QvicpO1xuXG4gICAgICAvLyAyLiBXYWl0IGJyaWVmbHkgZm9yIHN0cmVhbSB0byBlc3RhYmxpc2gsIHRoZW4gdGVzdCBjb25uZWN0aXZpdHlcbiAgICAgIGF3YWl0IG5ldyBQcm9taXNlKHIgPT4gc2V0VGltZW91dChyLCAxMjAwKSk7XG4gICAgICBjb25zdCBvayA9IGF3YWl0IHRlc3RDb25uZWN0aW9uKCk7XG4gICAgICBpZiAoIW9rKSB0aHJvdyBuZXcgRXJyb3IoYNCa0LDQvNC10YDRgiDRhdC+0LvQsdC+0LPQtNC+0LYg0YfQsNC00YHQsNC90LPSr9C5ICgke2NvbmZpZy5pcH06JHtjb25maWcucG9ydH0pYCk7XG5cbiAgICAgIHNldFN0YXRlKHMgPT4gKHsgLi4ucywgY29ubmVjdGVkOiB0cnVlLCBsb2FkaW5nOiBmYWxzZSwgZXJyb3I6IG51bGwgfSkpO1xuICAgICAgLy8gMy4gTG9hZCBhbnkgZXZlbnRzIGFscmVhZHkgYnVmZmVyZWQgZHVyaW5nIHJlY29ubmVjdCB3aW5kb3dcbiAgICAgIGNvbnN0IGJ1ZmZlcmVkID0gYXdhaXQgZmV0Y2hCdWZmZXJlZEV2ZW50cygpO1xuICAgICAgaWYgKGJ1ZmZlcmVkLmxlbmd0aCkgc2V0RXZlbnRzKGJ1ZmZlcmVkKTtcbiAgICB9IGNhdGNoIChlOiB1bmtub3duKSB7XG4gICAgICBzZXRTdGF0ZShzID0+ICh7IC4uLnMsIGVycm9yOiAoZSBpbnN0YW5jZW9mIEVycm9yID8gZS5tZXNzYWdlIDogU3RyaW5nKGUpKSwgbG9hZGluZzogZmFsc2UsIGNvbm5lY3RlZDogZmFsc2UgfSkpO1xuICAgIH1cbiAgfSwgW2NvbmZpZ10pO1xuXG4gIC8vIEF1dG8tc3RhcnQgU1NFIHN1YnNjcmlwdGlvbiBvbiBtb3VudFxuICB1c2VFZmZlY3QoKCkgPT4ge1xuICAgIGNvbnN0IHVuc3ViID0gc3Vic2NyaWJlRXZlbnRzKFxuICAgICAgKGV2KSA9PiB7XG4gICAgICAgIHNldEV2ZW50cyhwcmV2ID0+IHtcbiAgICAgICAgICAvLyBBdm9pZCBkdXBsaWNhdGVzXG4gICAgICAgICAgaWYgKHByZXYuc29tZShlID0+IGUuaWQgPT09IGV2LmlkKSkgcmV0dXJuIHByZXY7XG4gICAgICAgICAgcmV0dXJuIFtldiwgLi4ucHJldl0uc2xpY2UoMCwgbWF4RXZlbnRzUmVmLmN1cnJlbnQpO1xuICAgICAgICB9KTtcbiAgICAgICAgLy8gRXZlbnRzIGZsb3dpbmcg4oeSIHN0cmVhbSBpcyBoZWFsdGh5OyBjbGVhciBhbnkgc3RhbGUgXCJkaXNjb25uZWN0ZWRcIiBiYW5uZXIuXG4gICAgICAgIHNldFN0YXRlKHMgPT4gKHMuZXJyb3IgfHwgIXMuY29ubmVjdGVkID8geyAuLi5zLCBjb25uZWN0ZWQ6IHRydWUsIGVycm9yOiBudWxsIH0gOiBzKSk7XG4gICAgICB9LFxuICAgICAgKG1zZykgPT4gc2V0U3RhdGUocyA9PiAoeyAuLi5zLCBlcnJvcjogbXNnIH0pKSxcbiAgICAgIChpZCwgaW1hZ2VVcmwpID0+IHtcbiAgICAgICAgc2V0RXZlbnRzKHByZXYgPT4gcHJldi5tYXAoZSA9PiBlLmlkID09PSBpZCA/IHsgLi4uZSwgaW1hZ2VVcmwgfSA6IGUpKTtcbiAgICAgIH0sXG4gICAgICAoY2ZnKSA9PiB7XG4gICAgICAgIGlmIChjZmcubWF4RXZlbnRzID4gMCkge1xuICAgICAgICAgIG1heEV2ZW50c1JlZi5jdXJyZW50ID0gY2ZnLm1heEV2ZW50cztcbiAgICAgICAgICBzZXRFdmVudHMocHJldiA9PiBwcmV2Lmxlbmd0aCA+IGNmZy5tYXhFdmVudHMgPyBwcmV2LnNsaWNlKDAsIGNmZy5tYXhFdmVudHMpIDogcHJldik7XG4gICAgICAgIH1cbiAgICAgIH0sXG4gICAgICAvLyBvbk9wZW46IFNTRSAocmUpY29ubmVjdGVkIOKAlCBtYXJrIGNvbm5lY3RlZCBhbmQgZHJvcCB0aGUgcmVjb25uZWN0aW5nIGJhbm5lci5cbiAgICAgICgpID0+IHNldFN0YXRlKHMgPT4gKHsgLi4ucywgY29ubmVjdGVkOiB0cnVlLCBlcnJvcjogbnVsbCB9KSlcbiAgICApO1xuICAgIHVuc3ViUmVmLmN1cnJlbnQgPSB1bnN1YjtcblxuICAgIC8vIEFsc28gbG9hZCBidWZmZXJlZCBldmVudHMgb24gbW91bnRcbiAgICBmZXRjaEJ1ZmZlcmVkRXZlbnRzKCkudGhlbihldnMgPT4ge1xuICAgICAgaWYgKGV2cy5sZW5ndGgpIHNldEV2ZW50cyhldnMpO1xuICAgIH0pO1xuXG4gICAgcmV0dXJuICgpID0+IHVuc3ViKCk7XG4gIH0sIFtdKTtcblxuICBjb25zdCBjbGVhckV2ZW50cyA9IHVzZUNhbGxiYWNrKCgpID0+IHNldEV2ZW50cyhbXSksIFtdKTtcblxuICByZXR1cm4geyBldmVudHMsIHN0YXRlLCBjb25maWcsIHNldENvbmZpZywgY29ubmVjdCwgY2xlYXJFdmVudHMgfTtcbn1cbiJdfQ==