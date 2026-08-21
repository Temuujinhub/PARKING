import { createHotContext as __vite__createHotContext } from "/@vite/client";import.meta.hot = __vite__createHotContext("/src/components/CameraStatusPage.tsx");const useEffect = __vite__cjsImport0_react["useEffect"]; const useState = __vite__cjsImport0_react["useState"];const _jsxDEV = __vite__cjsImport1_react_jsxDevRuntime["jsxDEV"];import __vite__cjsImport0_react from "/node_modules/.vite/deps/react.js?v=7077b528";
var _jsxFileName = "/home/anpruser/anpr-app/src/components/CameraStatusPage.tsx";
import __vite__cjsImport1_react_jsxDevRuntime from "/node_modules/.vite/deps/react_jsx-dev-runtime.js?v=7077b528";
var _s = $RefreshSig$();
// Холболтгүй болсноос хойших хугацааг монголоор форматлана
function fmtDowntime(ms) {
	const s = Math.max(0, Math.floor(ms / 1e3));
	if (s < 60) return `${s} сек`;
	const m = Math.floor(s / 60);
	if (m < 60) return `${m} мин`;
	const h = Math.floor(m / 60);
	if (h < 24) return `${h} цаг ${m % 60} мин`;
	const d = Math.floor(h / 24);
	return `${d} өдөр ${h % 24} цаг`;
}
// Огноо-цагийг богино монгол хэлбэрээр
function fmtDateTime(ms) {
	const d = new Date(ms);
	const p = (n) => String(n).padStart(2, "0");
	return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
const STATUS_META = {
	online: {
		color: "var(--accent-green)",
		label: "Онлайн",
		bg: "var(--badge-green-bg)"
	},
	offline: {
		color: "var(--accent-red)",
		label: "Офлайн",
		bg: "var(--badge-red-bg)"
	},
	auth: {
		color: "var(--accent-orange)",
		label: "Нэвтрэлт алдаа",
		bg: "var(--badge-yellow-bg)"
	},
	connecting: {
		color: "var(--text-muted)",
		label: "Холбогдож буй",
		bg: "var(--bg-panel)"
	}
};
export default function CameraStatusPage({ token }) {
	_s();
	const [cams, setCams] = useState([]);
	const [onlyOffline, setOnlyOffline] = useState(false);
	const [loading, setLoading] = useState(true);
	const [now, setNow] = useState(Date.now());
	const [histCam, setHistCam] = useState(null);
	const [outages, setOutages] = useState([]);
	const [histLoading, setHistLoading] = useState(false);
	async function openHistory(cam) {
		setHistCam(cam);
		setOutages([]);
		setHistLoading(true);
		try {
			const r = await fetch(`/api/camera-status/outages?cameraId=${cam.id}`, { headers: { "X-Auth-Token": token } });
			if (r.ok) setOutages(await r.json());
		} catch {} finally {
			setHistLoading(false);
		}
	}
	async function load() {
		setLoading(true);
		try {
			const r = await fetch("/api/camera-status", { headers: { "X-Auth-Token": token } });
			if (r.ok) setCams(await r.json());
		} catch {} finally {
			setLoading(false);
		}
	}
	useEffect(() => {
		load();
		const iv = setInterval(load, 1e4);
		return () => clearInterval(iv);
	}, []);
	// Холболтгүй хугацааг секунд тутам жигд тоолуулна
	useEffect(() => {
		const iv = setInterval(() => setNow(Date.now()), 1e3);
		return () => clearInterval(iv);
	}, []);
	const online = cams.filter((c) => c.status === "online").length;
	const total = cams.length;
	const offlineCount = total - online;
	const shown = onlyOffline ? cams.filter((c) => c.status !== "online") : cams;
	return /* @__PURE__ */ _jsxDEV("div", {
		style: {
			maxWidth: 820,
			margin: "0 auto",
			width: "100%"
		},
		children: [
			/* @__PURE__ */ _jsxDEV("div", {
				style: {
					display: "flex",
					alignItems: "center",
					gap: 16,
					marginBottom: 16,
					flexWrap: "wrap"
				},
				children: [
					/* @__PURE__ */ _jsxDEV("div", {
						style: {
							fontSize: 15,
							fontWeight: 600,
							color: "var(--text-primary)"
						},
						children: ["Камерын төлөв", /* @__PURE__ */ _jsxDEV("span", {
							style: {
								marginLeft: 10,
								fontSize: 13,
								color: online === total ? "var(--accent-green)" : "var(--accent-orange)"
							},
							children: [
								online,
								"/",
								total,
								" онлайн"
							]
						}, void 0, true, {
							fileName: _jsxFileName,
							lineNumber: 93,
							columnNumber: 11
						}, this)]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 91,
						columnNumber: 9
					}, this),
					/* @__PURE__ */ _jsxDEV("label", {
						style: {
							display: "flex",
							alignItems: "center",
							gap: 6,
							fontSize: 13,
							color: "var(--text-muted)",
							cursor: "pointer"
						},
						children: [
							/* @__PURE__ */ _jsxDEV("input", {
								type: "checkbox",
								checked: onlyOffline,
								onChange: (e) => setOnlyOffline(e.target.checked),
								style: { cursor: "pointer" }
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 96,
								columnNumber: 11
							}, this),
							"Зөвхөн офлайн (",
							offlineCount,
							")"
						]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 95,
						columnNumber: 9
					}, this),
					/* @__PURE__ */ _jsxDEV("button", {
						onClick: load,
						disabled: loading,
						title: "Шинэчлэх",
						style: {
							marginLeft: "auto",
							background: "var(--bg-elevated)",
							border: "1px solid var(--border)",
							color: "var(--text-secondary)",
							borderRadius: 6,
							cursor: loading ? "default" : "pointer",
							padding: "6px 14px",
							fontSize: 13,
							opacity: loading ? .6 : 1
						},
						children: loading ? "Шинэчлэгдэж байна…" : "↻ Шинэчлэх"
					}, void 0, false, {
						fileName: _jsxFileName,
						lineNumber: 99,
						columnNumber: 9
					}, this)
				]
			}, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 90,
				columnNumber: 7
			}, this),
			loading && cams.length === 0 && /* @__PURE__ */ _jsxDEV("div", {
				style: {
					color: "var(--text-faint)",
					fontSize: 13,
					textAlign: "center",
					padding: 40
				},
				children: "Ачааллаж байна…"
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 105,
				columnNumber: 40
			}, this),
			!loading && cams.length === 0 && /* @__PURE__ */ _jsxDEV("div", {
				style: {
					color: "var(--text-faint)",
					fontSize: 13,
					textAlign: "center",
					padding: 40
				},
				children: "Камер бүртгэгдээгүй байна"
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 106,
				columnNumber: 41
			}, this),
			cams.length > 0 && shown.length === 0 && /* @__PURE__ */ _jsxDEV("div", {
				style: {
					color: "var(--accent-green)",
					fontSize: 14,
					textAlign: "center",
					padding: 40
				},
				children: "Бүх камер онлайн ✓"
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 107,
				columnNumber: 49
			}, this),
			shown.length > 0 && /* @__PURE__ */ _jsxDEV("div", {
				className: "table-wrapper",
				children: /* @__PURE__ */ _jsxDEV("table", { children: [/* @__PURE__ */ _jsxDEV("thead", { children: /* @__PURE__ */ _jsxDEV("tr", { children: [
					/* @__PURE__ */ _jsxDEV("th", { children: "Төлөв" }, void 0, false, {
						fileName: _jsxFileName,
						lineNumber: 115,
						columnNumber: 17
					}, this),
					/* @__PURE__ */ _jsxDEV("th", { children: "Зогсоол" }, void 0, false, {
						fileName: _jsxFileName,
						lineNumber: 116,
						columnNumber: 17
					}, this),
					/* @__PURE__ */ _jsxDEV("th", { children: "Чиглэл" }, void 0, false, {
						fileName: _jsxFileName,
						lineNumber: 117,
						columnNumber: 17
					}, this),
					/* @__PURE__ */ _jsxDEV("th", { children: "Нэр" }, void 0, false, {
						fileName: _jsxFileName,
						lineNumber: 118,
						columnNumber: 17
					}, this),
					/* @__PURE__ */ _jsxDEV("th", { children: "IP хаяг" }, void 0, false, {
						fileName: _jsxFileName,
						lineNumber: 119,
						columnNumber: 17
					}, this),
					/* @__PURE__ */ _jsxDEV("th", { children: "Холболтгүй хугацаа" }, void 0, false, {
						fileName: _jsxFileName,
						lineNumber: 120,
						columnNumber: 17
					}, this)
				] }, void 0, true, {
					fileName: _jsxFileName,
					lineNumber: 114,
					columnNumber: 15
				}, this) }, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 113,
					columnNumber: 13
				}, this), /* @__PURE__ */ _jsxDEV("tbody", { children: shown.map((c) => {
					const meta = STATUS_META[c.status];
					return /* @__PURE__ */ _jsxDEV("tr", {
						className: "event-row",
						onClick: () => openHistory(c),
						style: { cursor: "pointer" },
						title: "Тасалдлын түүх харах",
						children: [
							/* @__PURE__ */ _jsxDEV("td", { children: /* @__PURE__ */ _jsxDEV("span", {
								style: {
									display: "inline-flex",
									alignItems: "center",
									gap: 7
								},
								children: [/* @__PURE__ */ _jsxDEV("span", { style: {
									width: 9,
									height: 9,
									borderRadius: "50%",
									background: meta.color,
									flexShrink: 0
								} }, void 0, false, {
									fileName: _jsxFileName,
									lineNumber: 130,
									columnNumber: 25
								}, this), /* @__PURE__ */ _jsxDEV("span", {
									style: {
										fontSize: 12,
										color: meta.color
									},
									children: meta.label
								}, void 0, false, {
									fileName: _jsxFileName,
									lineNumber: 131,
									columnNumber: 25
								}, this)]
							}, void 0, true, {
								fileName: _jsxFileName,
								lineNumber: 129,
								columnNumber: 23
							}, this) }, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 128,
								columnNumber: 21
							}, this),
							/* @__PURE__ */ _jsxDEV("td", {
								style: { color: "var(--text-primary)" },
								children: c.parkingLotName || "—"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 134,
								columnNumber: 21
							}, this),
							/* @__PURE__ */ _jsxDEV("td", {
								style: {
									color: "var(--text-muted)",
									fontSize: 12
								},
								children: c.direction === "enter" ? "→ Орох" : "← Гарах"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 135,
								columnNumber: 21
							}, this),
							/* @__PURE__ */ _jsxDEV("td", {
								style: {
									color: "var(--text-muted)",
									fontSize: 12
								},
								children: c.label || "—"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 136,
								columnNumber: 21
							}, this),
							/* @__PURE__ */ _jsxDEV("td", {
								style: {
									color: "var(--text-faint)",
									fontFamily: "monospace",
									fontSize: 12
								},
								children: [
									c.ip,
									":",
									c.port
								]
							}, void 0, true, {
								fileName: _jsxFileName,
								lineNumber: 137,
								columnNumber: 21
							}, this),
							/* @__PURE__ */ _jsxDEV("td", {
								style: {
									fontSize: 12,
									color: c.status === "online" ? "var(--text-faint)" : "var(--accent-red)"
								},
								children: c.status === "online" || c.offlineSince == null ? "—" : fmtDowntime(now - c.offlineSince)
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 138,
								columnNumber: 21
							}, this)
						]
					}, c.id, true, {
						fileName: _jsxFileName,
						lineNumber: 127,
						columnNumber: 19
					}, this);
				}) }, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 123,
					columnNumber: 13
				}, this)] }, void 0, true, {
					fileName: _jsxFileName,
					lineNumber: 112,
					columnNumber: 11
				}, this)
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 111,
				columnNumber: 9
			}, this),
			histCam && /* @__PURE__ */ _jsxDEV("div", {
				onClick: () => setHistCam(null),
				style: {
					position: "fixed",
					inset: 0,
					background: "rgba(0,0,0,0.55)",
					display: "flex",
					alignItems: "center",
					justifyContent: "center",
					zIndex: 1e3,
					padding: 16
				},
				children: /* @__PURE__ */ _jsxDEV("div", {
					onClick: (e) => e.stopPropagation(),
					style: {
						background: "var(--bg-panel)",
						border: "1px solid var(--border)",
						borderRadius: 10,
						maxWidth: 560,
						width: "100%",
						maxHeight: "80vh",
						display: "flex",
						flexDirection: "column"
					},
					children: [/* @__PURE__ */ _jsxDEV("div", {
						style: {
							display: "flex",
							alignItems: "center",
							gap: 10,
							padding: "14px 18px",
							borderBottom: "1px solid var(--border)"
						},
						children: [/* @__PURE__ */ _jsxDEV("div", {
							style: {
								fontSize: 14,
								fontWeight: 600,
								color: "var(--text-primary)"
							},
							children: ["Тасалдлын түүх", /* @__PURE__ */ _jsxDEV("span", {
								style: {
									marginLeft: 8,
									fontSize: 12,
									color: "var(--text-muted)",
									fontWeight: 400
								},
								children: [
									histCam.parkingLotName,
									" · ",
									histCam.direction === "enter" ? "Орох" : "Гарах",
									" · ",
									histCam.ip
								]
							}, void 0, true, {
								fileName: _jsxFileName,
								lineNumber: 158,
								columnNumber: 17
							}, this)]
						}, void 0, true, {
							fileName: _jsxFileName,
							lineNumber: 156,
							columnNumber: 15
						}, this), /* @__PURE__ */ _jsxDEV("button", {
							onClick: () => setHistCam(null),
							style: {
								marginLeft: "auto",
								background: "transparent",
								border: "none",
								color: "var(--text-muted)",
								fontSize: 20,
								cursor: "pointer",
								lineHeight: 1
							},
							children: "×"
						}, void 0, false, {
							fileName: _jsxFileName,
							lineNumber: 162,
							columnNumber: 15
						}, this)]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 155,
						columnNumber: 13
					}, this), /* @__PURE__ */ _jsxDEV("div", {
						style: {
							overflow: "auto",
							padding: "6px 0"
						},
						children: [
							histLoading && /* @__PURE__ */ _jsxDEV("div", {
								style: {
									color: "var(--text-faint)",
									fontSize: 13,
									textAlign: "center",
									padding: 30
								},
								children: "Ачааллаж байна…"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 165,
								columnNumber: 31
							}, this),
							!histLoading && outages.length === 0 && /* @__PURE__ */ _jsxDEV("div", {
								style: {
									color: "var(--accent-green)",
									fontSize: 13,
									textAlign: "center",
									padding: 30
								},
								children: "Тасалдал бүртгэгдээгүй ✓"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 166,
								columnNumber: 56
							}, this),
							!histLoading && outages.length > 0 && /* @__PURE__ */ _jsxDEV("table", { children: [/* @__PURE__ */ _jsxDEV("thead", { children: /* @__PURE__ */ _jsxDEV("tr", { children: [
								/* @__PURE__ */ _jsxDEV("th", { children: "Таслагдсан" }, void 0, false, {
									fileName: _jsxFileName,
									lineNumber: 170,
									columnNumber: 25
								}, this),
								/* @__PURE__ */ _jsxDEV("th", { children: "Сэргэсэн" }, void 0, false, {
									fileName: _jsxFileName,
									lineNumber: 170,
									columnNumber: 44
								}, this),
								/* @__PURE__ */ _jsxDEV("th", { children: "Үргэлжилсэн" }, void 0, false, {
									fileName: _jsxFileName,
									lineNumber: 170,
									columnNumber: 61
								}, this)
							] }, void 0, true, {
								fileName: _jsxFileName,
								lineNumber: 170,
								columnNumber: 21
							}, this) }, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 169,
								columnNumber: 19
							}, this), /* @__PURE__ */ _jsxDEV("tbody", { children: outages.map((o) => /* @__PURE__ */ _jsxDEV("tr", {
								className: "event-row",
								children: [
									/* @__PURE__ */ _jsxDEV("td", {
										style: {
											fontSize: 12,
											color: "var(--text-secondary)"
										},
										children: fmtDateTime(o.startedAt)
									}, void 0, false, {
										fileName: _jsxFileName,
										lineNumber: 175,
										columnNumber: 25
									}, this),
									/* @__PURE__ */ _jsxDEV("td", {
										style: {
											fontSize: 12,
											color: o.ongoing ? "var(--accent-red)" : "var(--text-secondary)"
										},
										children: o.ongoing ? "Үргэлжилж буй" : fmtDateTime(o.endedAt)
									}, void 0, false, {
										fileName: _jsxFileName,
										lineNumber: 176,
										columnNumber: 25
									}, this),
									/* @__PURE__ */ _jsxDEV("td", {
										style: {
											fontSize: 12,
											fontWeight: 600,
											color: o.ongoing ? "var(--accent-red)" : "var(--text-primary)"
										},
										children: fmtDowntime((o.ongoing ? now : o.endedAt) - o.startedAt)
									}, void 0, false, {
										fileName: _jsxFileName,
										lineNumber: 179,
										columnNumber: 25
									}, this)
								]
							}, o.id, true, {
								fileName: _jsxFileName,
								lineNumber: 174,
								columnNumber: 23
							}, this)) }, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 172,
								columnNumber: 19
							}, this)] }, void 0, true, {
								fileName: _jsxFileName,
								lineNumber: 168,
								columnNumber: 17
							}, this)
						]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 164,
						columnNumber: 13
					}, this)]
				}, void 0, true, {
					fileName: _jsxFileName,
					lineNumber: 153,
					columnNumber: 11
				}, this)
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 151,
				columnNumber: 9
			}, this)
		]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 88,
		columnNumber: 5
	}, this);
}
_s(CameraStatusPage, "TiNbN8v/xS77WRiVwBmlLI2MAgE=");
_c = CameraStatusPage;
var _c;
$RefreshReg$(_c, "CameraStatusPage");
import * as RefreshRuntime from "/@react-refresh";
const inWebWorker = typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope;
import * as __vite_react_currentExports from "/src/components/CameraStatusPage.tsx";
if (import.meta.hot && !inWebWorker) {
  if (!window.$RefreshReg$) {
    throw new Error(
      "@vitejs/plugin-react can't detect preamble. Something is wrong."
    );
  }

  const currentExports = __vite_react_currentExports;
  queueMicrotask(() => {
    RefreshRuntime.registerExportsForReactRefresh("/home/anpruser/anpr-app/src/components/CameraStatusPage.tsx", currentExports);
    import.meta.hot.accept((nextExports) => {
      if (!nextExports) return;
      const invalidateMessage = RefreshRuntime.validateRefreshBoundaryAndEnqueueUpdate("/home/anpruser/anpr-app/src/components/CameraStatusPage.tsx", currentExports, nextExports);
      if (invalidateMessage) import.meta.hot.invalidate(invalidateMessage);
    });
  });
}
function $RefreshReg$(type, id) { return RefreshRuntime.register(type, "/home/anpruser/anpr-app/src/components/CameraStatusPage.tsx" + ' ' + id); }
function $RefreshSig$() { return RefreshRuntime.createSignatureFunctionForTransform(); }

//# sourceMappingURL=data:application/json;base64,eyJtYXBwaW5ncyI6IkFBQUEsU0FBUyxXQUFXLGdCQUFnQjs7Ozs7QUF3QnBDLFNBQVMsWUFBWSxJQUFvQjtDQUN2QyxNQUFNLElBQUksS0FBSyxJQUFJLEdBQUcsS0FBSyxNQUFNLEtBQUssR0FBSSxDQUFDO0NBQzNDLElBQUksSUFBSSxJQUFJLE9BQU8sR0FBRyxFQUFFO0NBQ3hCLE1BQU0sSUFBSSxLQUFLLE1BQU0sSUFBSSxFQUFFO0NBQzNCLElBQUksSUFBSSxJQUFJLE9BQU8sR0FBRyxFQUFFO0NBQ3hCLE1BQU0sSUFBSSxLQUFLLE1BQU0sSUFBSSxFQUFFO0NBQzNCLElBQUksSUFBSSxJQUFJLE9BQU8sR0FBRyxFQUFFLE9BQU8sSUFBSSxHQUFHO0NBQ3RDLE1BQU0sSUFBSSxLQUFLLE1BQU0sSUFBSSxFQUFFO0NBQzNCLE9BQU8sR0FBRyxFQUFFLFFBQVEsSUFBSSxHQUFHO0FBQzdCOztBQUdBLFNBQVMsWUFBWSxJQUFvQjtDQUN2QyxNQUFNLElBQUksSUFBSSxLQUFLLEVBQUU7Q0FDckIsTUFBTSxLQUFLLE1BQWMsT0FBTyxDQUFDLEVBQUUsU0FBUyxHQUFHLEdBQUc7Q0FDbEQsT0FBTyxHQUFHLEVBQUUsWUFBWSxFQUFFLEdBQUcsRUFBRSxFQUFFLFNBQVMsSUFBSSxDQUFDLEVBQUUsR0FBRyxFQUFFLEVBQUUsUUFBUSxDQUFDLEVBQUUsR0FBRyxFQUFFLEVBQUUsU0FBUyxDQUFDLEVBQUUsR0FBRyxFQUFFLEVBQUUsV0FBVyxDQUFDO0FBQzNHO0FBRUEsTUFBTSxjQUF1RjtDQUMzRixRQUFZO0VBQUUsT0FBTztFQUF1QixPQUFPO0VBQWlCLElBQUk7Q0FBd0I7Q0FDaEcsU0FBWTtFQUFFLE9BQU87RUFBcUIsT0FBTztFQUFpQixJQUFJO0NBQXNCO0NBQzVGLE1BQVk7RUFBRSxPQUFPO0VBQXdCLE9BQU87RUFBa0IsSUFBSTtDQUF5QjtDQUNuRyxZQUFZO0VBQUUsT0FBTztFQUFxQixPQUFPO0VBQWtCLElBQUk7Q0FBa0I7QUFDM0Y7QUFFQSxlQUFlLFNBQVMsaUJBQWlCLEVBQUUsU0FBNEI7O0NBQ3JFLE1BQU0sQ0FBQyxNQUFNLFdBQVcsU0FBb0IsQ0FBQyxDQUFDO0NBQzlDLE1BQU0sQ0FBQyxhQUFhLGtCQUFrQixTQUFTLEtBQUs7Q0FDcEQsTUFBTSxDQUFDLFNBQVMsY0FBYyxTQUFTLElBQUk7Q0FDM0MsTUFBTSxDQUFDLEtBQUssVUFBVSxTQUFTLEtBQUssSUFBSSxDQUFDO0NBQ3pDLE1BQU0sQ0FBQyxTQUFTLGNBQWMsU0FBeUIsSUFBSTtDQUMzRCxNQUFNLENBQUMsU0FBUyxjQUFjLFNBQW1CLENBQUMsQ0FBQztDQUNuRCxNQUFNLENBQUMsYUFBYSxrQkFBa0IsU0FBUyxLQUFLO0NBRXBELGVBQWUsWUFBWSxLQUFjO0VBQ3ZDLFdBQVcsR0FBRztFQUNkLFdBQVcsQ0FBQyxDQUFDO0VBQ2IsZUFBZSxJQUFJO0VBQ25CLElBQUk7R0FDRixNQUFNLElBQUksTUFBTSxNQUFNLHVDQUF1QyxJQUFJLE1BQU0sRUFBRSxTQUFTLEVBQUUsZ0JBQWdCLE1BQU0sRUFBRSxDQUFDO0dBQzdHLElBQUksRUFBRSxJQUFJLFdBQVcsTUFBTSxFQUFFLEtBQUssQ0FBQztFQUNyQyxRQUFRLENBQWUsVUFDZjtHQUFFLGVBQWUsS0FBSztFQUFHO0NBQ25DO0NBRUEsZUFBZSxPQUFPO0VBQ3BCLFdBQVcsSUFBSTtFQUNmLElBQUk7R0FDRixNQUFNLElBQUksTUFBTSxNQUFNLHNCQUFzQixFQUFFLFNBQVMsRUFBRSxnQkFBZ0IsTUFBTSxFQUFFLENBQUM7R0FDbEYsSUFBSSxFQUFFLElBQUksUUFBUSxNQUFNLEVBQUUsS0FBSyxDQUFDO0VBQ2xDLFFBQVEsQ0FBZSxVQUNmO0dBQUUsV0FBVyxLQUFLO0VBQUc7Q0FDL0I7Q0FDQSxnQkFBZ0I7RUFBRSxLQUFLO0VBQUcsTUFBTSxLQUFLLFlBQVksTUFBTSxHQUFLO0VBQUcsYUFBYSxjQUFjLEVBQUU7Q0FBRyxHQUFHLENBQUMsQ0FBQzs7Q0FFcEcsZ0JBQWdCO0VBQUUsTUFBTSxLQUFLLGtCQUFrQixPQUFPLEtBQUssSUFBSSxDQUFDLEdBQUcsR0FBSTtFQUFHLGFBQWEsY0FBYyxFQUFFO0NBQUcsR0FBRyxDQUFDLENBQUM7Q0FFL0csTUFBTSxTQUFTLEtBQUssUUFBTyxNQUFLLEVBQUUsV0FBVyxRQUFRLEVBQUU7Q0FDdkQsTUFBTSxRQUFRLEtBQUs7Q0FDbkIsTUFBTSxlQUFlLFFBQVE7Q0FDN0IsTUFBTSxRQUFRLGNBQWMsS0FBSyxRQUFPLE1BQUssRUFBRSxXQUFXLFFBQVEsSUFBSTtDQUV0RSxPQUNFLHdCQUFDLE9BQUQ7RUFBSyxPQUFPO0dBQUUsVUFBVTtHQUFLLFFBQVE7R0FBVSxPQUFPO0VBQU87WUFBN0Q7R0FFRSx3QkFBQyxPQUFEO0lBQUssT0FBTztLQUFFLFNBQVM7S0FBUSxZQUFZO0tBQVUsS0FBSztLQUFJLGNBQWM7S0FBSSxVQUFVO0lBQU87Y0FBakc7S0FDRSx3QkFBQyxPQUFEO01BQUssT0FBTztPQUFFLFVBQVU7T0FBSSxZQUFZO09BQUssT0FBTztNQUFzQjtnQkFBMUUsQ0FBNkUsaUJBRTNFLHdCQUFDLFFBQUQ7T0FBTSxPQUFPO1FBQUUsWUFBWTtRQUFJLFVBQVU7UUFBSSxPQUFPLFdBQVcsUUFBUSx3QkFBd0I7T0FBdUI7aUJBQXRIO1FBQTBIO1FBQU87UUFBRTtRQUFNO09BQWE7Ozs7O2NBQ25KOzs7Ozs7S0FDTCx3QkFBQyxTQUFEO01BQU8sT0FBTztPQUFFLFNBQVM7T0FBUSxZQUFZO09BQVUsS0FBSztPQUFHLFVBQVU7T0FBSSxPQUFPO09BQXFCLFFBQVE7TUFBVTtnQkFBM0g7T0FDRSx3QkFBQyxTQUFEO1FBQU8sTUFBSztRQUFXLFNBQVM7UUFBYSxXQUFVLE1BQUssZUFBZSxFQUFFLE9BQU8sT0FBTztRQUFHLE9BQU8sRUFBRSxRQUFRLFVBQVU7T0FBSTs7Ozs7T0FBQztPQUM5RztPQUFhO01BQ3hCOzs7Ozs7S0FDUCx3QkFBQyxVQUFEO01BQVEsU0FBUztNQUFNLFVBQVU7TUFBUyxPQUFNO01BQzlDLE9BQU87T0FBRSxZQUFZO09BQVEsWUFBWTtPQUFzQixRQUFRO09BQTJCLE9BQU87T0FBeUIsY0FBYztPQUFHLFFBQVEsVUFBVSxZQUFZO09BQVcsU0FBUztPQUFZLFVBQVU7T0FBSSxTQUFTLFVBQVUsS0FBTTtNQUFFO2dCQUN6UCxVQUFVLHVCQUF1QjtLQUM1Qjs7Ozs7SUFDTDs7Ozs7O0dBRUosV0FBVyxLQUFLLFdBQVcsS0FBSyx3QkFBQyxPQUFEO0lBQUssT0FBTztLQUFFLE9BQU87S0FBcUIsVUFBVTtLQUFJLFdBQVc7S0FBVSxTQUFTO0lBQUc7Y0FBRztHQUFvQjs7Ozs7R0FDaEosQ0FBQyxXQUFXLEtBQUssV0FBVyxLQUFLLHdCQUFDLE9BQUQ7SUFBSyxPQUFPO0tBQUUsT0FBTztLQUFxQixVQUFVO0tBQUksV0FBVztLQUFVLFNBQVM7SUFBRztjQUFHO0dBQThCOzs7OztHQUMzSixLQUFLLFNBQVMsS0FBSyxNQUFNLFdBQVcsS0FBSyx3QkFBQyxPQUFEO0lBQUssT0FBTztLQUFFLE9BQU87S0FBdUIsVUFBVTtLQUFJLFdBQVc7S0FBVSxTQUFTO0lBQUc7Y0FBRztHQUF1Qjs7Ozs7R0FHOUosTUFBTSxTQUFTLEtBQ2Qsd0JBQUMsT0FBRDtJQUFLLFdBQVU7Y0FDYix3QkFBQyxTQUFELGFBQ0Usd0JBQUMsU0FBRCxZQUNFLHdCQUFDLE1BQUQ7S0FDRSx3QkFBQyxNQUFELFlBQUksUUFBUzs7Ozs7S0FDYix3QkFBQyxNQUFELFlBQUksVUFBVzs7Ozs7S0FDZix3QkFBQyxNQUFELFlBQUksU0FBVTs7Ozs7S0FDZCx3QkFBQyxNQUFELFlBQUksTUFBTzs7Ozs7S0FDWCx3QkFBQyxNQUFELFlBQUksVUFBVzs7Ozs7S0FDZix3QkFBQyxNQUFELFlBQUkscUJBQXNCOzs7OztJQUN4Qjs7OzthQUNDOzs7O2NBQ1Asd0JBQUMsU0FBRCxZQUNHLE1BQU0sS0FBSSxNQUFLO0tBQ2QsTUFBTSxPQUFPLFlBQVksRUFBRTtLQUMzQixPQUNFLHdCQUFDLE1BQUQ7TUFBZSxXQUFVO01BQVksZUFBZSxZQUFZLENBQUM7TUFBRyxPQUFPLEVBQUUsUUFBUSxVQUFVO01BQUcsT0FBTTtnQkFBeEc7T0FDRSx3QkFBQyxNQUFELFlBQ0Usd0JBQUMsUUFBRDtRQUFNLE9BQU87U0FBRSxTQUFTO1NBQWUsWUFBWTtTQUFVLEtBQUs7UUFBRTtrQkFBcEUsQ0FDRSx3QkFBQyxRQUFELEVBQU0sT0FBTztTQUFFLE9BQU87U0FBRyxRQUFRO1NBQUcsY0FBYztTQUFPLFlBQVksS0FBSztTQUFPLFlBQVk7UUFBRSxFQUFJOzs7O2tCQUNuRyx3QkFBQyxRQUFEO1NBQU0sT0FBTztVQUFFLFVBQVU7VUFBSSxPQUFPLEtBQUs7U0FBTTttQkFBSSxLQUFLO1FBQVk7Ozs7Z0JBQ2hFOzs7OztnQkFDSjs7Ozs7T0FDSix3QkFBQyxNQUFEO1FBQUksT0FBTyxFQUFFLE9BQU8sc0JBQXNCO2tCQUFJLEVBQUUsa0JBQWtCO09BQVE7Ozs7O09BQzFFLHdCQUFDLE1BQUQ7UUFBSSxPQUFPO1NBQUUsT0FBTztTQUFxQixVQUFVO1FBQUc7a0JBQUksRUFBRSxjQUFjLFVBQVUsV0FBVztPQUFjOzs7OztPQUM3Ryx3QkFBQyxNQUFEO1FBQUksT0FBTztTQUFFLE9BQU87U0FBcUIsVUFBVTtRQUFHO2tCQUFJLEVBQUUsU0FBUztPQUFROzs7OztPQUM3RSx3QkFBQyxNQUFEO1FBQUksT0FBTztTQUFFLE9BQU87U0FBcUIsWUFBWTtTQUFhLFVBQVU7UUFBRztrQkFBL0U7U0FBbUYsRUFBRTtTQUFHO1NBQUUsRUFBRTtRQUFTOzs7Ozs7T0FDckcsd0JBQUMsTUFBRDtRQUFJLE9BQU87U0FBRSxVQUFVO1NBQUksT0FBTyxFQUFFLFdBQVcsV0FBVyxzQkFBc0I7UUFBb0I7a0JBQ2pHLEVBQUUsV0FBVyxZQUFZLEVBQUUsZ0JBQWdCLE9BQU8sTUFBTSxZQUFZLE1BQU0sRUFBRSxZQUFZO09BQ3ZGOzs7OztNQUNGO1FBZEssRUFBRTs7OztZQWNQO0lBRVIsQ0FBQyxFQUNJOzs7O1lBQ0Y7Ozs7O0dBQ0o7Ozs7O0dBSU4sV0FDQyx3QkFBQyxPQUFEO0lBQUssZUFBZSxXQUFXLElBQUk7SUFDakMsT0FBTztLQUFFLFVBQVU7S0FBUyxPQUFPO0tBQUcsWUFBWTtLQUFvQixTQUFTO0tBQVEsWUFBWTtLQUFVLGdCQUFnQjtLQUFVLFFBQVE7S0FBTSxTQUFTO0lBQUc7Y0FDakssd0JBQUMsT0FBRDtLQUFLLFVBQVMsTUFBSyxFQUFFLGdCQUFnQjtLQUNuQyxPQUFPO01BQUUsWUFBWTtNQUFtQixRQUFRO01BQTJCLGNBQWM7TUFBSSxVQUFVO01BQUssT0FBTztNQUFRLFdBQVc7TUFBUSxTQUFTO01BQVEsZUFBZTtLQUFTO2VBRHpMLENBRUUsd0JBQUMsT0FBRDtNQUFLLE9BQU87T0FBRSxTQUFTO09BQVEsWUFBWTtPQUFVLEtBQUs7T0FBSSxTQUFTO09BQWEsY0FBYztNQUEwQjtnQkFBNUgsQ0FDRSx3QkFBQyxPQUFEO09BQUssT0FBTztRQUFFLFVBQVU7UUFBSSxZQUFZO1FBQUssT0FBTztPQUFzQjtpQkFBMUUsQ0FBNkUsa0JBRTNFLHdCQUFDLFFBQUQ7UUFBTSxPQUFPO1NBQUUsWUFBWTtTQUFHLFVBQVU7U0FBSSxPQUFPO1NBQXFCLFlBQVk7UUFBSTtrQkFBeEY7U0FDRyxRQUFRO1NBQWU7U0FBSSxRQUFRLGNBQWMsVUFBVSxTQUFTO1NBQVE7U0FBSSxRQUFRO1FBQ3JGOzs7OztlQUNIOzs7OztnQkFDTCx3QkFBQyxVQUFEO09BQVEsZUFBZSxXQUFXLElBQUk7T0FBRyxPQUFPO1FBQUUsWUFBWTtRQUFRLFlBQVk7UUFBZSxRQUFRO1FBQVEsT0FBTztRQUFxQixVQUFVO1FBQUksUUFBUTtRQUFXLFlBQVk7T0FBRTtpQkFBRztNQUFTOzs7O2NBQ3JNOzs7OztlQUNMLHdCQUFDLE9BQUQ7TUFBSyxPQUFPO09BQUUsVUFBVTtPQUFRLFNBQVM7TUFBUTtnQkFBakQ7T0FDRyxlQUFlLHdCQUFDLE9BQUQ7UUFBSyxPQUFPO1NBQUUsT0FBTztTQUFxQixVQUFVO1NBQUksV0FBVztTQUFVLFNBQVM7UUFBRztrQkFBRztPQUFvQjs7Ozs7T0FDL0gsQ0FBQyxlQUFlLFFBQVEsV0FBVyxLQUFLLHdCQUFDLE9BQUQ7UUFBSyxPQUFPO1NBQUUsT0FBTztTQUF1QixVQUFVO1NBQUksV0FBVztTQUFVLFNBQVM7UUFBRztrQkFBRztPQUE2Qjs7Ozs7T0FDbkssQ0FBQyxlQUFlLFFBQVEsU0FBUyxLQUNoQyx3QkFBQyxTQUFELGFBQ0Usd0JBQUMsU0FBRCxZQUNFLHdCQUFDLE1BQUQ7UUFBSSx3QkFBQyxNQUFELFlBQUksYUFBYzs7Ozs7UUFBQyx3QkFBQyxNQUFELFlBQUksV0FBWTs7Ozs7UUFBQyx3QkFBQyxNQUFELFlBQUksY0FBZTs7Ozs7T0FBSzs7OztnQkFDM0Q7Ozs7aUJBQ1Asd0JBQUMsU0FBRCxZQUNHLFFBQVEsS0FBSSxNQUNYLHdCQUFDLE1BQUQ7UUFBZSxXQUFVO2tCQUF6QjtTQUNFLHdCQUFDLE1BQUQ7VUFBSSxPQUFPO1dBQUUsVUFBVTtXQUFJLE9BQU87VUFBd0I7b0JBQUksWUFBWSxFQUFFLFNBQVM7U0FBTTs7Ozs7U0FDM0Ysd0JBQUMsTUFBRDtVQUFJLE9BQU87V0FBRSxVQUFVO1dBQUksT0FBTyxFQUFFLFVBQVUsc0JBQXNCO1VBQXdCO29CQUN6RixFQUFFLFVBQVUsa0JBQWtCLFlBQVksRUFBRSxPQUFRO1NBQ25EOzs7OztTQUNKLHdCQUFDLE1BQUQ7VUFBSSxPQUFPO1dBQUUsVUFBVTtXQUFJLFlBQVk7V0FBSyxPQUFPLEVBQUUsVUFBVSxzQkFBc0I7VUFBc0I7b0JBQ3hHLGFBQWEsRUFBRSxVQUFVLE1BQU0sRUFBRSxXQUFZLEVBQUUsU0FBUztTQUN2RDs7Ozs7UUFDRjtVQVJLLEVBQUU7Ozs7Y0FRUCxDQUNMLEVBQ0k7Ozs7ZUFDRjs7Ozs7TUFFTjs7Ozs7YUFDRjs7Ozs7O0dBQ0Y7Ozs7O0VBRUo7Ozs7OztBQUVUIiwibmFtZXMiOltdLCJzb3VyY2VzIjpbIkNhbWVyYVN0YXR1c1BhZ2UudHN4Il0sInZlcnNpb24iOjMsInNvdXJjZXNDb250ZW50IjpbImltcG9ydCB7IHVzZUVmZmVjdCwgdXNlU3RhdGUgfSBmcm9tICdyZWFjdCc7XG5cbmludGVyZmFjZSBDYW1TdGF0IHtcbiAgaWQ6IG51bWJlcjtcbiAgbGFiZWw6IHN0cmluZztcbiAgaXA6IHN0cmluZztcbiAgcG9ydDogbnVtYmVyO1xuICBkaXJlY3Rpb246ICdlbnRlcicgfCAnZXhpdCc7XG4gIHBhcmtpbmdMb3ROYW1lOiBzdHJpbmc7XG4gIHN0YXR1czogJ29ubGluZScgfCAnb2ZmbGluZScgfCAnYXV0aCcgfCAnY29ubmVjdGluZyc7XG4gIHN0cmVhbUNvbm5lY3RlZDogYm9vbGVhbjtcbiAgbGFzdEVycjogc3RyaW5nO1xuICBvZmZsaW5lU2luY2U6IG51bWJlciB8IG51bGw7XG59XG5cbmludGVyZmFjZSBPdXRhZ2Uge1xuICBpZDogbnVtYmVyO1xuICBjYW1lcmFJZDogbnVtYmVyO1xuICBzdGFydGVkQXQ6IG51bWJlcjtcbiAgZW5kZWRBdDogbnVtYmVyIHwgbnVsbDtcbiAgb25nb2luZzogYm9vbGVhbjtcbn1cblxuLy8g0KXQvtC70LHQvtC70YLQs9Kv0Lkg0LHQvtC70YHQvdC+0L7RgSDRhdC+0LnRiNC40YUg0YXRg9Cz0LDRhtCw0LDQsyDQvNC+0L3Qs9C+0LvQvtC+0YAg0YTQvtGA0LzQsNGC0LvQsNC90LBcbmZ1bmN0aW9uIGZtdERvd250aW1lKG1zOiBudW1iZXIpOiBzdHJpbmcge1xuICBjb25zdCBzID0gTWF0aC5tYXgoMCwgTWF0aC5mbG9vcihtcyAvIDEwMDApKTtcbiAgaWYgKHMgPCA2MCkgcmV0dXJuIGAke3N9INGB0LXQumA7XG4gIGNvbnN0IG0gPSBNYXRoLmZsb29yKHMgLyA2MCk7XG4gIGlmIChtIDwgNjApIHJldHVybiBgJHttfSDQvNC40L1gO1xuICBjb25zdCBoID0gTWF0aC5mbG9vcihtIC8gNjApO1xuICBpZiAoaCA8IDI0KSByZXR1cm4gYCR7aH0g0YbQsNCzICR7bSAlIDYwfSDQvNC40L1gO1xuICBjb25zdCBkID0gTWF0aC5mbG9vcihoIC8gMjQpO1xuICByZXR1cm4gYCR7ZH0g06nQtNOp0YAgJHtoICUgMjR9INGG0LDQs2A7XG59XG5cbi8vINCe0LPQvdC+0L4t0YbQsNCz0LjQudCzINCx0L7Qs9C40L3QviDQvNC+0L3Qs9C+0Lsg0YXRjdC70LHRjdGA0Y3RjdGAXG5mdW5jdGlvbiBmbXREYXRlVGltZShtczogbnVtYmVyKTogc3RyaW5nIHtcbiAgY29uc3QgZCA9IG5ldyBEYXRlKG1zKTtcbiAgY29uc3QgcCA9IChuOiBudW1iZXIpID0+IFN0cmluZyhuKS5wYWRTdGFydCgyLCAnMCcpO1xuICByZXR1cm4gYCR7ZC5nZXRGdWxsWWVhcigpfS0ke3AoZC5nZXRNb250aCgpICsgMSl9LSR7cChkLmdldERhdGUoKSl9ICR7cChkLmdldEhvdXJzKCkpfToke3AoZC5nZXRNaW51dGVzKCkpfWA7XG59XG5cbmNvbnN0IFNUQVRVU19NRVRBOiBSZWNvcmQ8Q2FtU3RhdFsnc3RhdHVzJ10sIHsgY29sb3I6IHN0cmluZzsgbGFiZWw6IHN0cmluZzsgYmc6IHN0cmluZyB9PiA9IHtcbiAgb25saW5lOiAgICAgeyBjb2xvcjogJ3ZhcigtLWFjY2VudC1ncmVlbiknLCBsYWJlbDogJ9Ce0L3Qu9Cw0LnQvScsICAgICAgICBiZzogJ3ZhcigtLWJhZGdlLWdyZWVuLWJnKScgfSxcbiAgb2ZmbGluZTogICAgeyBjb2xvcjogJ3ZhcigtLWFjY2VudC1yZWQpJywgbGFiZWw6ICfQntGE0LvQsNC50L0nLCAgICAgICAgYmc6ICd2YXIoLS1iYWRnZS1yZWQtYmcpJyB9LFxuICBhdXRoOiAgICAgICB7IGNvbG9yOiAndmFyKC0tYWNjZW50LW9yYW5nZSknLCBsYWJlbDogJ9Cd0Y3QstGC0YDRjdC70YIg0LDQu9C00LDQsCcsIGJnOiAndmFyKC0tYmFkZ2UteWVsbG93LWJnKScgfSxcbiAgY29ubmVjdGluZzogeyBjb2xvcjogJ3ZhcigtLXRleHQtbXV0ZWQpJywgbGFiZWw6ICfQpdC+0LvQsdC+0LPQtNC+0LYg0LHRg9C5JywgIGJnOiAndmFyKC0tYmctcGFuZWwpJyB9LFxufTtcblxuZXhwb3J0IGRlZmF1bHQgZnVuY3Rpb24gQ2FtZXJhU3RhdHVzUGFnZSh7IHRva2VuIH06IHsgdG9rZW46IHN0cmluZyB9KSB7XG4gIGNvbnN0IFtjYW1zLCBzZXRDYW1zXSA9IHVzZVN0YXRlPENhbVN0YXRbXT4oW10pO1xuICBjb25zdCBbb25seU9mZmxpbmUsIHNldE9ubHlPZmZsaW5lXSA9IHVzZVN0YXRlKGZhbHNlKTtcbiAgY29uc3QgW2xvYWRpbmcsIHNldExvYWRpbmddID0gdXNlU3RhdGUodHJ1ZSk7XG4gIGNvbnN0IFtub3csIHNldE5vd10gPSB1c2VTdGF0ZShEYXRlLm5vdygpKTtcbiAgY29uc3QgW2hpc3RDYW0sIHNldEhpc3RDYW1dID0gdXNlU3RhdGU8Q2FtU3RhdCB8IG51bGw+KG51bGwpO1xuICBjb25zdCBbb3V0YWdlcywgc2V0T3V0YWdlc10gPSB1c2VTdGF0ZTxPdXRhZ2VbXT4oW10pO1xuICBjb25zdCBbaGlzdExvYWRpbmcsIHNldEhpc3RMb2FkaW5nXSA9IHVzZVN0YXRlKGZhbHNlKTtcblxuICBhc3luYyBmdW5jdGlvbiBvcGVuSGlzdG9yeShjYW06IENhbVN0YXQpIHtcbiAgICBzZXRIaXN0Q2FtKGNhbSk7XG4gICAgc2V0T3V0YWdlcyhbXSk7XG4gICAgc2V0SGlzdExvYWRpbmcodHJ1ZSk7XG4gICAgdHJ5IHtcbiAgICAgIGNvbnN0IHIgPSBhd2FpdCBmZXRjaChgL2FwaS9jYW1lcmEtc3RhdHVzL291dGFnZXM/Y2FtZXJhSWQ9JHtjYW0uaWR9YCwgeyBoZWFkZXJzOiB7ICdYLUF1dGgtVG9rZW4nOiB0b2tlbiB9IH0pO1xuICAgICAgaWYgKHIub2spIHNldE91dGFnZXMoYXdhaXQgci5qc29uKCkpO1xuICAgIH0gY2F0Y2ggeyAvKiBpZ25vcmUgKi8gfVxuICAgIGZpbmFsbHkgeyBzZXRIaXN0TG9hZGluZyhmYWxzZSk7IH1cbiAgfVxuXG4gIGFzeW5jIGZ1bmN0aW9uIGxvYWQoKSB7XG4gICAgc2V0TG9hZGluZyh0cnVlKTtcbiAgICB0cnkge1xuICAgICAgY29uc3QgciA9IGF3YWl0IGZldGNoKCcvYXBpL2NhbWVyYS1zdGF0dXMnLCB7IGhlYWRlcnM6IHsgJ1gtQXV0aC1Ub2tlbic6IHRva2VuIH0gfSk7XG4gICAgICBpZiAoci5vaykgc2V0Q2Ftcyhhd2FpdCByLmpzb24oKSk7XG4gICAgfSBjYXRjaCB7IC8qIGlnbm9yZSAqLyB9XG4gICAgZmluYWxseSB7IHNldExvYWRpbmcoZmFsc2UpOyB9XG4gIH1cbiAgdXNlRWZmZWN0KCgpID0+IHsgbG9hZCgpOyBjb25zdCBpdiA9IHNldEludGVydmFsKGxvYWQsIDEwMDAwKTsgcmV0dXJuICgpID0+IGNsZWFySW50ZXJ2YWwoaXYpOyB9LCBbXSk7XG4gIC8vINCl0L7Qu9Cx0L7Qu9GC0LPSr9C5INGF0YPQs9Cw0YbQsNCw0LMg0YHQtdC60YPQvdC0INGC0YPRgtCw0Lwg0LbQuNCz0LQg0YLQvtC+0LvRg9GD0LvQvdCwXG4gIHVzZUVmZmVjdCgoKSA9PiB7IGNvbnN0IGl2ID0gc2V0SW50ZXJ2YWwoKCkgPT4gc2V0Tm93KERhdGUubm93KCkpLCAxMDAwKTsgcmV0dXJuICgpID0+IGNsZWFySW50ZXJ2YWwoaXYpOyB9LCBbXSk7XG5cbiAgY29uc3Qgb25saW5lID0gY2Ftcy5maWx0ZXIoYyA9PiBjLnN0YXR1cyA9PT0gJ29ubGluZScpLmxlbmd0aDtcbiAgY29uc3QgdG90YWwgPSBjYW1zLmxlbmd0aDtcbiAgY29uc3Qgb2ZmbGluZUNvdW50ID0gdG90YWwgLSBvbmxpbmU7XG4gIGNvbnN0IHNob3duID0gb25seU9mZmxpbmUgPyBjYW1zLmZpbHRlcihjID0+IGMuc3RhdHVzICE9PSAnb25saW5lJykgOiBjYW1zO1xuXG4gIHJldHVybiAoXG4gICAgPGRpdiBzdHlsZT17eyBtYXhXaWR0aDogODIwLCBtYXJnaW46ICcwIGF1dG8nLCB3aWR0aDogJzEwMCUnIH19PlxuICAgICAgey8qINCl0Y/QvdCw0LvRgtGL0L0g0LzTqdGAICovfVxuICAgICAgPGRpdiBzdHlsZT17eyBkaXNwbGF5OiAnZmxleCcsIGFsaWduSXRlbXM6ICdjZW50ZXInLCBnYXA6IDE2LCBtYXJnaW5Cb3R0b206IDE2LCBmbGV4V3JhcDogJ3dyYXAnIH19PlxuICAgICAgICA8ZGl2IHN0eWxlPXt7IGZvbnRTaXplOiAxNSwgZm9udFdlaWdodDogNjAwLCBjb2xvcjogJ3ZhcigtLXRleHQtcHJpbWFyeSknIH19PlxuICAgICAgICAgINCa0LDQvNC10YDRi9C9INGC06nQu9Op0LJcbiAgICAgICAgICA8c3BhbiBzdHlsZT17eyBtYXJnaW5MZWZ0OiAxMCwgZm9udFNpemU6IDEzLCBjb2xvcjogb25saW5lID09PSB0b3RhbCA/ICd2YXIoLS1hY2NlbnQtZ3JlZW4pJyA6ICd2YXIoLS1hY2NlbnQtb3JhbmdlKScgfX0+e29ubGluZX0ve3RvdGFsfSDQvtC90LvQsNC50L08L3NwYW4+XG4gICAgICAgIDwvZGl2PlxuICAgICAgICA8bGFiZWwgc3R5bGU9e3sgZGlzcGxheTogJ2ZsZXgnLCBhbGlnbkl0ZW1zOiAnY2VudGVyJywgZ2FwOiA2LCBmb250U2l6ZTogMTMsIGNvbG9yOiAndmFyKC0tdGV4dC1tdXRlZCknLCBjdXJzb3I6ICdwb2ludGVyJyB9fT5cbiAgICAgICAgICA8aW5wdXQgdHlwZT1cImNoZWNrYm94XCIgY2hlY2tlZD17b25seU9mZmxpbmV9IG9uQ2hhbmdlPXtlID0+IHNldE9ubHlPZmZsaW5lKGUudGFyZ2V0LmNoZWNrZWQpfSBzdHlsZT17eyBjdXJzb3I6ICdwb2ludGVyJyB9fSAvPlxuICAgICAgICAgINCX06nQstGF06nQvSDQvtGE0LvQsNC50L0gKHtvZmZsaW5lQ291bnR9KVxuICAgICAgICA8L2xhYmVsPlxuICAgICAgICA8YnV0dG9uIG9uQ2xpY2s9e2xvYWR9IGRpc2FibGVkPXtsb2FkaW5nfSB0aXRsZT1cItCo0LjQvdGN0YfQu9GN0YVcIlxuICAgICAgICAgIHN0eWxlPXt7IG1hcmdpbkxlZnQ6ICdhdXRvJywgYmFja2dyb3VuZDogJ3ZhcigtLWJnLWVsZXZhdGVkKScsIGJvcmRlcjogJzFweCBzb2xpZCB2YXIoLS1ib3JkZXIpJywgY29sb3I6ICd2YXIoLS10ZXh0LXNlY29uZGFyeSknLCBib3JkZXJSYWRpdXM6IDYsIGN1cnNvcjogbG9hZGluZyA/ICdkZWZhdWx0JyA6ICdwb2ludGVyJywgcGFkZGluZzogJzZweCAxNHB4JywgZm9udFNpemU6IDEzLCBvcGFjaXR5OiBsb2FkaW5nID8gMC42IDogMSB9fT5cbiAgICAgICAgICB7bG9hZGluZyA/ICfQqNC40L3RjdGH0LvRjdCz0LTRjdC2INCx0LDQudC90LDigKYnIDogJ+KGuyDQqNC40L3RjdGH0LvRjdGFJ31cbiAgICAgICAgPC9idXR0b24+XG4gICAgICA8L2Rpdj5cblxuICAgICAge2xvYWRpbmcgJiYgY2Ftcy5sZW5ndGggPT09IDAgJiYgPGRpdiBzdHlsZT17eyBjb2xvcjogJ3ZhcigtLXRleHQtZmFpbnQpJywgZm9udFNpemU6IDEzLCB0ZXh0QWxpZ246ICdjZW50ZXInLCBwYWRkaW5nOiA0MCB9fT7QkNGH0LDQsNC70LvQsNC2INCx0LDQudC90LDigKY8L2Rpdj59XG4gICAgICB7IWxvYWRpbmcgJiYgY2Ftcy5sZW5ndGggPT09IDAgJiYgPGRpdiBzdHlsZT17eyBjb2xvcjogJ3ZhcigtLXRleHQtZmFpbnQpJywgZm9udFNpemU6IDEzLCB0ZXh0QWxpZ246ICdjZW50ZXInLCBwYWRkaW5nOiA0MCB9fT7QmtCw0LzQtdGAINCx0q/RgNGC0LPRjdCz0LTRjdGN0LPSr9C5INCx0LDQudC90LA8L2Rpdj59XG4gICAgICB7Y2Ftcy5sZW5ndGggPiAwICYmIHNob3duLmxlbmd0aCA9PT0gMCAmJiA8ZGl2IHN0eWxlPXt7IGNvbG9yOiAndmFyKC0tYWNjZW50LWdyZWVuKScsIGZvbnRTaXplOiAxNCwgdGV4dEFsaWduOiAnY2VudGVyJywgcGFkZGluZzogNDAgfX0+0JHSr9GFINC60LDQvNC10YAg0L7QvdC70LDQudC9IOKckzwvZGl2Pn1cblxuICAgICAgey8qINCa0LDQvNC10YDRi9C9INC20LDQs9GB0LDQsNC70YIgKi99XG4gICAgICB7c2hvd24ubGVuZ3RoID4gMCAmJiAoXG4gICAgICAgIDxkaXYgY2xhc3NOYW1lPVwidGFibGUtd3JhcHBlclwiPlxuICAgICAgICAgIDx0YWJsZT5cbiAgICAgICAgICAgIDx0aGVhZD5cbiAgICAgICAgICAgICAgPHRyPlxuICAgICAgICAgICAgICAgIDx0aD7QotOp0LvTqdCyPC90aD5cbiAgICAgICAgICAgICAgICA8dGg+0JfQvtCz0YHQvtC+0Ls8L3RoPlxuICAgICAgICAgICAgICAgIDx0aD7Qp9C40LPQu9GN0Ls8L3RoPlxuICAgICAgICAgICAgICAgIDx0aD7QndGN0YA8L3RoPlxuICAgICAgICAgICAgICAgIDx0aD5JUCDRhdCw0Y/QszwvdGg+XG4gICAgICAgICAgICAgICAgPHRoPtCl0L7Qu9Cx0L7Qu9GC0LPSr9C5INGF0YPQs9Cw0YbQsNCwPC90aD5cbiAgICAgICAgICAgICAgPC90cj5cbiAgICAgICAgICAgIDwvdGhlYWQ+XG4gICAgICAgICAgICA8dGJvZHk+XG4gICAgICAgICAgICAgIHtzaG93bi5tYXAoYyA9PiB7XG4gICAgICAgICAgICAgICAgY29uc3QgbWV0YSA9IFNUQVRVU19NRVRBW2Muc3RhdHVzXTtcbiAgICAgICAgICAgICAgICByZXR1cm4gKFxuICAgICAgICAgICAgICAgICAgPHRyIGtleT17Yy5pZH0gY2xhc3NOYW1lPVwiZXZlbnQtcm93XCIgb25DbGljaz17KCkgPT4gb3Blbkhpc3RvcnkoYyl9IHN0eWxlPXt7IGN1cnNvcjogJ3BvaW50ZXInIH19IHRpdGxlPVwi0KLQsNGB0LDQu9C00LvRi9C9INGC0q/Sr9GFINGF0LDRgNCw0YVcIj5cbiAgICAgICAgICAgICAgICAgICAgPHRkPlxuICAgICAgICAgICAgICAgICAgICAgIDxzcGFuIHN0eWxlPXt7IGRpc3BsYXk6ICdpbmxpbmUtZmxleCcsIGFsaWduSXRlbXM6ICdjZW50ZXInLCBnYXA6IDcgfX0+XG4gICAgICAgICAgICAgICAgICAgICAgICA8c3BhbiBzdHlsZT17eyB3aWR0aDogOSwgaGVpZ2h0OiA5LCBib3JkZXJSYWRpdXM6ICc1MCUnLCBiYWNrZ3JvdW5kOiBtZXRhLmNvbG9yLCBmbGV4U2hyaW5rOiAwIH19IC8+XG4gICAgICAgICAgICAgICAgICAgICAgICA8c3BhbiBzdHlsZT17eyBmb250U2l6ZTogMTIsIGNvbG9yOiBtZXRhLmNvbG9yIH19PnttZXRhLmxhYmVsfTwvc3Bhbj5cbiAgICAgICAgICAgICAgICAgICAgICA8L3NwYW4+XG4gICAgICAgICAgICAgICAgICAgIDwvdGQ+XG4gICAgICAgICAgICAgICAgICAgIDx0ZCBzdHlsZT17eyBjb2xvcjogJ3ZhcigtLXRleHQtcHJpbWFyeSknIH19PntjLnBhcmtpbmdMb3ROYW1lIHx8ICfigJQnfTwvdGQ+XG4gICAgICAgICAgICAgICAgICAgIDx0ZCBzdHlsZT17eyBjb2xvcjogJ3ZhcigtLXRleHQtbXV0ZWQpJywgZm9udFNpemU6IDEyIH19PntjLmRpcmVjdGlvbiA9PT0gJ2VudGVyJyA/ICfihpIg0J7RgNC+0YUnIDogJ+KGkCDQk9Cw0YDQsNGFJ308L3RkPlxuICAgICAgICAgICAgICAgICAgICA8dGQgc3R5bGU9e3sgY29sb3I6ICd2YXIoLS10ZXh0LW11dGVkKScsIGZvbnRTaXplOiAxMiB9fT57Yy5sYWJlbCB8fCAn4oCUJ308L3RkPlxuICAgICAgICAgICAgICAgICAgICA8dGQgc3R5bGU9e3sgY29sb3I6ICd2YXIoLS10ZXh0LWZhaW50KScsIGZvbnRGYW1pbHk6ICdtb25vc3BhY2UnLCBmb250U2l6ZTogMTIgfX0+e2MuaXB9OntjLnBvcnR9PC90ZD5cbiAgICAgICAgICAgICAgICAgICAgPHRkIHN0eWxlPXt7IGZvbnRTaXplOiAxMiwgY29sb3I6IGMuc3RhdHVzID09PSAnb25saW5lJyA/ICd2YXIoLS10ZXh0LWZhaW50KScgOiAndmFyKC0tYWNjZW50LXJlZCknIH19PlxuICAgICAgICAgICAgICAgICAgICAgIHtjLnN0YXR1cyA9PT0gJ29ubGluZScgfHwgYy5vZmZsaW5lU2luY2UgPT0gbnVsbCA/ICfigJQnIDogZm10RG93bnRpbWUobm93IC0gYy5vZmZsaW5lU2luY2UpfVxuICAgICAgICAgICAgICAgICAgICA8L3RkPlxuICAgICAgICAgICAgICAgICAgPC90cj5cbiAgICAgICAgICAgICAgICApO1xuICAgICAgICAgICAgICB9KX1cbiAgICAgICAgICAgIDwvdGJvZHk+XG4gICAgICAgICAgPC90YWJsZT5cbiAgICAgICAgPC9kaXY+XG4gICAgICApfVxuXG4gICAgICB7Lyog0KLQsNGB0LDQu9C00LvRi9C9INGC0q/Sr9GF0LjQudC9INGG0L7QvdGFICovfVxuICAgICAge2hpc3RDYW0gJiYgKFxuICAgICAgICA8ZGl2IG9uQ2xpY2s9eygpID0+IHNldEhpc3RDYW0obnVsbCl9XG4gICAgICAgICAgc3R5bGU9e3sgcG9zaXRpb246ICdmaXhlZCcsIGluc2V0OiAwLCBiYWNrZ3JvdW5kOiAncmdiYSgwLDAsMCwwLjU1KScsIGRpc3BsYXk6ICdmbGV4JywgYWxpZ25JdGVtczogJ2NlbnRlcicsIGp1c3RpZnlDb250ZW50OiAnY2VudGVyJywgekluZGV4OiAxMDAwLCBwYWRkaW5nOiAxNiB9fT5cbiAgICAgICAgICA8ZGl2IG9uQ2xpY2s9e2UgPT4gZS5zdG9wUHJvcGFnYXRpb24oKX1cbiAgICAgICAgICAgIHN0eWxlPXt7IGJhY2tncm91bmQ6ICd2YXIoLS1iZy1wYW5lbCknLCBib3JkZXI6ICcxcHggc29saWQgdmFyKC0tYm9yZGVyKScsIGJvcmRlclJhZGl1czogMTAsIG1heFdpZHRoOiA1NjAsIHdpZHRoOiAnMTAwJScsIG1heEhlaWdodDogJzgwdmgnLCBkaXNwbGF5OiAnZmxleCcsIGZsZXhEaXJlY3Rpb246ICdjb2x1bW4nIH19PlxuICAgICAgICAgICAgPGRpdiBzdHlsZT17eyBkaXNwbGF5OiAnZmxleCcsIGFsaWduSXRlbXM6ICdjZW50ZXInLCBnYXA6IDEwLCBwYWRkaW5nOiAnMTRweCAxOHB4JywgYm9yZGVyQm90dG9tOiAnMXB4IHNvbGlkIHZhcigtLWJvcmRlciknIH19PlxuICAgICAgICAgICAgICA8ZGl2IHN0eWxlPXt7IGZvbnRTaXplOiAxNCwgZm9udFdlaWdodDogNjAwLCBjb2xvcjogJ3ZhcigtLXRleHQtcHJpbWFyeSknIH19PlxuICAgICAgICAgICAgICAgINCi0LDRgdCw0LvQtNC70YvQvSDRgtKv0q/RhVxuICAgICAgICAgICAgICAgIDxzcGFuIHN0eWxlPXt7IG1hcmdpbkxlZnQ6IDgsIGZvbnRTaXplOiAxMiwgY29sb3I6ICd2YXIoLS10ZXh0LW11dGVkKScsIGZvbnRXZWlnaHQ6IDQwMCB9fT5cbiAgICAgICAgICAgICAgICAgIHtoaXN0Q2FtLnBhcmtpbmdMb3ROYW1lfSDCtyB7aGlzdENhbS5kaXJlY3Rpb24gPT09ICdlbnRlcicgPyAn0J7RgNC+0YUnIDogJ9CT0LDRgNCw0YUnfSDCtyB7aGlzdENhbS5pcH1cbiAgICAgICAgICAgICAgICA8L3NwYW4+XG4gICAgICAgICAgICAgIDwvZGl2PlxuICAgICAgICAgICAgICA8YnV0dG9uIG9uQ2xpY2s9eygpID0+IHNldEhpc3RDYW0obnVsbCl9IHN0eWxlPXt7IG1hcmdpbkxlZnQ6ICdhdXRvJywgYmFja2dyb3VuZDogJ3RyYW5zcGFyZW50JywgYm9yZGVyOiAnbm9uZScsIGNvbG9yOiAndmFyKC0tdGV4dC1tdXRlZCknLCBmb250U2l6ZTogMjAsIGN1cnNvcjogJ3BvaW50ZXInLCBsaW5lSGVpZ2h0OiAxIH19PsOXPC9idXR0b24+XG4gICAgICAgICAgICA8L2Rpdj5cbiAgICAgICAgICAgIDxkaXYgc3R5bGU9e3sgb3ZlcmZsb3c6ICdhdXRvJywgcGFkZGluZzogJzZweCAwJyB9fT5cbiAgICAgICAgICAgICAge2hpc3RMb2FkaW5nICYmIDxkaXYgc3R5bGU9e3sgY29sb3I6ICd2YXIoLS10ZXh0LWZhaW50KScsIGZvbnRTaXplOiAxMywgdGV4dEFsaWduOiAnY2VudGVyJywgcGFkZGluZzogMzAgfX0+0JDRh9Cw0LDQu9C70LDQtiDQsdCw0LnQvdCw4oCmPC9kaXY+fVxuICAgICAgICAgICAgICB7IWhpc3RMb2FkaW5nICYmIG91dGFnZXMubGVuZ3RoID09PSAwICYmIDxkaXYgc3R5bGU9e3sgY29sb3I6ICd2YXIoLS1hY2NlbnQtZ3JlZW4pJywgZm9udFNpemU6IDEzLCB0ZXh0QWxpZ246ICdjZW50ZXInLCBwYWRkaW5nOiAzMCB9fT7QotCw0YHQsNC70LTQsNC7INCx0q/RgNGC0LPRjdCz0LTRjdGN0LPSr9C5IOKckzwvZGl2Pn1cbiAgICAgICAgICAgICAgeyFoaXN0TG9hZGluZyAmJiBvdXRhZ2VzLmxlbmd0aCA+IDAgJiYgKFxuICAgICAgICAgICAgICAgIDx0YWJsZT5cbiAgICAgICAgICAgICAgICAgIDx0aGVhZD5cbiAgICAgICAgICAgICAgICAgICAgPHRyPjx0aD7QotCw0YHQu9Cw0LPQtNGB0LDQvTwvdGg+PHRoPtCh0Y3RgNCz0Y3RgdGN0L08L3RoPjx0aD7SrtGA0LPRjdC70LbQuNC70YHRjdC9PC90aD48L3RyPlxuICAgICAgICAgICAgICAgICAgPC90aGVhZD5cbiAgICAgICAgICAgICAgICAgIDx0Ym9keT5cbiAgICAgICAgICAgICAgICAgICAge291dGFnZXMubWFwKG8gPT4gKFxuICAgICAgICAgICAgICAgICAgICAgIDx0ciBrZXk9e28uaWR9IGNsYXNzTmFtZT1cImV2ZW50LXJvd1wiPlxuICAgICAgICAgICAgICAgICAgICAgICAgPHRkIHN0eWxlPXt7IGZvbnRTaXplOiAxMiwgY29sb3I6ICd2YXIoLS10ZXh0LXNlY29uZGFyeSknIH19PntmbXREYXRlVGltZShvLnN0YXJ0ZWRBdCl9PC90ZD5cbiAgICAgICAgICAgICAgICAgICAgICAgIDx0ZCBzdHlsZT17eyBmb250U2l6ZTogMTIsIGNvbG9yOiBvLm9uZ29pbmcgPyAndmFyKC0tYWNjZW50LXJlZCknIDogJ3ZhcigtLXRleHQtc2Vjb25kYXJ5KScgfX0+XG4gICAgICAgICAgICAgICAgICAgICAgICAgIHtvLm9uZ29pbmcgPyAn0q7RgNCz0Y3Qu9C20LjQu9C2INCx0YPQuScgOiBmbXREYXRlVGltZShvLmVuZGVkQXQhKX1cbiAgICAgICAgICAgICAgICAgICAgICAgIDwvdGQ+XG4gICAgICAgICAgICAgICAgICAgICAgICA8dGQgc3R5bGU9e3sgZm9udFNpemU6IDEyLCBmb250V2VpZ2h0OiA2MDAsIGNvbG9yOiBvLm9uZ29pbmcgPyAndmFyKC0tYWNjZW50LXJlZCknIDogJ3ZhcigtLXRleHQtcHJpbWFyeSknIH19PlxuICAgICAgICAgICAgICAgICAgICAgICAgICB7Zm10RG93bnRpbWUoKG8ub25nb2luZyA/IG5vdyA6IG8uZW5kZWRBdCEpIC0gby5zdGFydGVkQXQpfVxuICAgICAgICAgICAgICAgICAgICAgICAgPC90ZD5cbiAgICAgICAgICAgICAgICAgICAgICA8L3RyPlxuICAgICAgICAgICAgICAgICAgICApKX1cbiAgICAgICAgICAgICAgICAgIDwvdGJvZHk+XG4gICAgICAgICAgICAgICAgPC90YWJsZT5cbiAgICAgICAgICAgICAgKX1cbiAgICAgICAgICAgIDwvZGl2PlxuICAgICAgICAgIDwvZGl2PlxuICAgICAgICA8L2Rpdj5cbiAgICAgICl9XG4gICAgPC9kaXY+XG4gICk7XG59XG4iXX0=