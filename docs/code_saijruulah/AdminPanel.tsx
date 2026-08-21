import { createHotContext as __vite__createHotContext } from "/@vite/client";import.meta.hot = __vite__createHotContext("/src/components/AdminPanel.tsx");const useEffect = __vite__cjsImport0_react["useEffect"]; const useState = __vite__cjsImport0_react["useState"];const _jsxDEV = __vite__cjsImport4_react_jsxDevRuntime["jsxDEV"];import __vite__cjsImport0_react from "/node_modules/.vite/deps/react.js?v=7077b528";
import ParkingManagement from "/src/components/ParkingManagement.tsx";
import UserManagement from "/src/components/UserManagement.tsx";
import TunnelsPage from "/src/components/TunnelsPage.tsx";
var _jsxFileName = "/home/anpruser/anpr-app/src/components/AdminPanel.tsx";
import __vite__cjsImport4_react_jsxDevRuntime from "/node_modules/.vite/deps/react_jsx-dev-runtime.js?v=7077b528";
var _s = $RefreshSig$(), _s2 = $RefreshSig$(), _s3 = $RefreshSig$(), _s4 = $RefreshSig$();
const TAB_LABELS = {
	parking: "Зогсоол",
	users: "Хэрэглэгч",
	reasons: "Нээх шалтгаан",
	settings: "Тохиргоо",
	logs: "Лог",
	tunnels: "Салбар"
};
function formatUptime(sec) {
	const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
	return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
// ── Reasons tab ─────────────────────────────────────────────────────────────
function ReasonsTab({ token, canEdit, canDelete }) {
	_s();
	const [reasons, setReasons] = useState([]);
	const [newLabel, setNewLabel] = useState("");
	const [adding, setAdding] = useState(false);
	const H = {
		"Content-Type": "application/json",
		"X-Auth-Token": token
	};
	async function load() {
		const r = await fetch("/api/open-reasons", { headers: H });
		if (r.ok) setReasons(await r.json());
	}
	useEffect(() => {
		load();
	}, []);
	async function addReason(e) {
		e.preventDefault();
		if (!newLabel.trim()) return;
		setAdding(true);
		await fetch("/api/open-reasons", {
			method: "POST",
			headers: H,
			body: JSON.stringify({ label: newLabel.trim() })
		});
		setNewLabel("");
		await load();
		setAdding(false);
	}
	async function deleteReason(id, label) {
		if (!confirm(`"${label}" шалтгааныг устгах уу?`)) return;
		await fetch(`/api/open-reasons/${id}`, {
			method: "DELETE",
			headers: H
		});
		load();
	}
	return /* @__PURE__ */ _jsxDEV("div", { children: [canEdit && /* @__PURE__ */ _jsxDEV("form", {
		onSubmit: addReason,
		style: {
			display: "flex",
			gap: 8,
			marginBottom: 20
		},
		children: [/* @__PURE__ */ _jsxDEV("input", {
			value: newLabel,
			onChange: (e) => setNewLabel(e.target.value),
			placeholder: "Шинэ шалтгаан нэмэх...",
			style: {
				flex: 1,
				background: "var(--bg-page)",
				border: "1px solid var(--border)",
				borderRadius: 6,
				padding: "7px 12px",
				color: "var(--text-primary)",
				fontSize: 13,
				outline: "none"
			}
		}, void 0, false, {
			fileName: _jsxFileName,
			lineNumber: 67,
			columnNumber: 9
		}, this), /* @__PURE__ */ _jsxDEV("button", {
			type: "submit",
			disabled: adding || !newLabel.trim(),
			style: {
				padding: "7px 18px",
				background: "var(--accent-green-strong)",
				border: "none",
				borderRadius: 6,
				color: "var(--text-on-accent)",
				fontSize: 13,
				cursor: "pointer",
				opacity: adding ? .6 : 1
			},
			children: "+ Нэмэх"
		}, void 0, false, {
			fileName: _jsxFileName,
			lineNumber: 73,
			columnNumber: 9
		}, this)]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 66,
		columnNumber: 7
	}, this), /* @__PURE__ */ _jsxDEV("div", {
		style: {
			display: "flex",
			flexDirection: "column",
			gap: 6
		},
		children: [reasons.length === 0 && /* @__PURE__ */ _jsxDEV("div", {
			style: {
				color: "var(--text-faint)",
				fontSize: 13,
				textAlign: "center",
				padding: 32
			},
			children: "Шалтгаан байхгүй байна"
		}, void 0, false, {
			fileName: _jsxFileName,
			lineNumber: 85,
			columnNumber: 11
		}, this), reasons.map((r, i) => /* @__PURE__ */ _jsxDEV("div", {
			style: {
				display: "flex",
				alignItems: "center",
				gap: 10,
				padding: "9px 14px",
				background: "var(--bg-panel)",
				border: "1px solid var(--bg-elevated)",
				borderRadius: 6
			},
			children: [
				/* @__PURE__ */ _jsxDEV("span", {
					style: {
						fontSize: 12,
						color: "var(--text-faint)",
						width: 22,
						textAlign: "right"
					},
					children: i + 1
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 89,
					columnNumber: 13
				}, this),
				/* @__PURE__ */ _jsxDEV("span", {
					style: {
						flex: 1,
						fontSize: 14,
						color: "var(--text-secondary)"
					},
					children: r.label
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 90,
					columnNumber: 13
				}, this),
				canDelete && r.label !== "Бусад" && /* @__PURE__ */ _jsxDEV("button", {
					onClick: () => deleteReason(r.id, r.label),
					style: {
						background: "none",
						border: "none",
						color: "var(--accent-red)",
						fontSize: 14,
						cursor: "pointer",
						padding: "2px 6px",
						borderRadius: 4
					},
					title: "Устгах",
					children: "✕"
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 92,
					columnNumber: 13
				}, this),
				canDelete && r.label === "Бусад" && /* @__PURE__ */ _jsxDEV("span", {
					style: {
						fontSize: 11,
						color: "var(--text-faint)"
					},
					title: "Тайлбар бичих сонголтыг систем ашигладаг тул устгах боломжгүй",
					children: "🔒"
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 99,
					columnNumber: 13
				}, this)
			]
		}, r.id, true, {
			fileName: _jsxFileName,
			lineNumber: 88,
			columnNumber: 11
		}, this))]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 83,
		columnNumber: 7
	}, this)] }, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 64,
		columnNumber: 5
	}, this);
}
_s(ReasonsTab, "TSYPkwtbzBDIqGDNYlD96xVgmn8=");
_c = ReasonsTab;
// ── Logs tab ──────────────────────────────────────────────────────────────────
const TAG_COLOR = {
	"[ANPR_EVENT]": "var(--accent-blue)",
	"[BARRIER_OPEN]": "var(--accent-green)",
	"[BARRIER_CLOSE]": "var(--accent-red)",
	"[LOGIN]": "var(--accent-blue-pale)",
	"[LOGIN_FAIL]": "var(--accent-orange)",
	"[USER_ADD]": "var(--accent-purple)",
	"[USER_DELETE]": "var(--accent-orange)",
	"[USER_UPDATE]": "var(--accent-purple)",
	"[LOT_ADD]": "var(--accent-blue-light)",
	"[LOT_UPDATE]": "var(--accent-blue-light)",
	"[LOT_DELETE]": "var(--accent-orange)",
	"[CAM_ADD]": "var(--accent-green-light)",
	"[CAM_DELETE]": "var(--accent-orange)",
	"[CAM_UPDATE]": "var(--accent-green-light)",
	"[REASON_ADD]": "var(--accent-yellow)",
	"[REASON_DELETE]": "var(--accent-orange)",
	"[COMPLAINT_ADD]": "var(--accent-pink)",
	"[COMPLAINT_UPDATE]": "var(--accent-pink)",
	"[COMPLAINT_PROGRESS]": "var(--accent-pink)",
	"[COMPLAINT_DELETE]": "var(--accent-orange)",
	"[SETTINGS]": "var(--text-muted)"
};
function LogsTab({ token }) {
	_s2();
	const [dates, setDates] = useState([]);
	const [date, setDate] = useState("");
	const [lines, setLines] = useState([]);
	const [filter, setFilter] = useState("");
	const [loading, setLoading] = useState(false);
	const H = { "X-Auth-Token": token };
	useEffect(() => {
		fetch("/api/logs", { headers: H }).then((r) => r.ok ? r.json() : []).then((d) => {
			setDates(d);
			if (d.length) setDate(d[0]);
		});
	}, []);
	useEffect(() => {
		if (!date) return;
		setLoading(true);
		fetch(`/api/logs/${date}`, { headers: H }).then((r) => r.ok ? r.text() : "").then((txt) => {
			setLines(txt.trim() ? txt.trim().split("\n").reverse() : []);
			setLoading(false);
		});
	}, [date]);
	const filtered = filter ? lines.filter((l) => l.toLowerCase().includes(filter.toLowerCase())) : lines;
	function downloadCsv() {
		const header = "date,time,tag,detail";
		const rows = filtered.map((line) => {
			const m = line.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\s+(\[[\w_]+\])\s*(.*)$/);
			const cols = m ? [
				m[1],
				m[2],
				m[3],
				m[4]
			] : [
				"",
				"",
				"",
				line
			];
			return cols.map((c) => `"${c.replace(/"/g, "\"\"")}"`).join(",");
		});
		const csv = [header, ...rows].join("\r\n");
		const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
		const url = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.href = url;
		link.download = `log_${date || "export"}.csv`;
		link.click();
		URL.revokeObjectURL(url);
	}
	function tagColor(line) {
		const m = line.match(/\[[\w_]+\]/);
		return m ? TAG_COLOR[m[0]] ?? "var(--text-muted)" : "var(--text-muted)";
	}
	function highlightLine(line) {
		const parts = line.split(/(\[[\w_]+\])/);
		return parts.map((p, i) => /^\[[\w_]+\]$/.test(p) ? /* @__PURE__ */ _jsxDEV("span", {
			style: {
				color: TAG_COLOR[p] ?? "var(--text-muted)",
				fontWeight: 600
			},
			children: p
		}, i, false, {
			fileName: _jsxFileName,
			lineNumber: 186,
			columnNumber: 11
		}, this) : /* @__PURE__ */ _jsxDEV("span", {
			style: { color: "var(--text-muted)" },
			children: p
		}, i, false, {
			fileName: _jsxFileName,
			lineNumber: 187,
			columnNumber: 11
		}, this));
	}
	return /* @__PURE__ */ _jsxDEV("div", {
		style: {
			display: "flex",
			flexDirection: "column",
			gap: 12,
			height: "100%"
		},
		children: [/* @__PURE__ */ _jsxDEV("div", {
			style: {
				display: "flex",
				gap: 10,
				alignItems: "center"
			},
			children: [
				/* @__PURE__ */ _jsxDEV("select", {
					value: date,
					onChange: (e) => setDate(e.target.value),
					style: {
						background: "var(--bg-page)",
						border: "1px solid var(--border)",
						color: "var(--text-primary)",
						borderRadius: 6,
						padding: "6px 10px",
						fontSize: 13
					},
					children: dates.map((d) => /* @__PURE__ */ _jsxDEV("option", {
						value: d,
						children: d
					}, d, false, {
						fileName: _jsxFileName,
						lineNumber: 199,
						columnNumber: 27
					}, this))
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 194,
					columnNumber: 9
				}, this),
				/* @__PURE__ */ _jsxDEV("input", {
					value: filter,
					onChange: (e) => setFilter(e.target.value),
					placeholder: "Хайх... (plate, user, ip...)",
					style: {
						flex: 1,
						background: "var(--bg-page)",
						border: "1px solid var(--border)",
						borderRadius: 6,
						padding: "6px 12px",
						color: "var(--text-primary)",
						fontSize: 13,
						outline: "none"
					}
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 201,
					columnNumber: 9
				}, this),
				/* @__PURE__ */ _jsxDEV("span", {
					style: {
						fontSize: 12,
						color: "var(--text-faint)",
						whiteSpace: "nowrap"
					},
					children: [filtered.length, " мөр"]
				}, void 0, true, {
					fileName: _jsxFileName,
					lineNumber: 207,
					columnNumber: 9
				}, this),
				/* @__PURE__ */ _jsxDEV("button", {
					className: "btn",
					style: {
						fontSize: 12,
						padding: "6px 12px",
						background: "var(--bg-elevated)",
						border: "1px solid var(--border)",
						color: "var(--text-secondary)",
						whiteSpace: "nowrap"
					},
					disabled: filtered.length === 0,
					onClick: downloadCsv,
					children: "⭳ CSV татах"
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 208,
					columnNumber: 9
				}, this)
			]
		}, void 0, true, {
			fileName: _jsxFileName,
			lineNumber: 193,
			columnNumber: 7
		}, this), /* @__PURE__ */ _jsxDEV("div", {
			style: {
				flex: 1,
				overflowY: "auto",
				background: "var(--bg-page)",
				border: "1px solid var(--bg-elevated)",
				borderRadius: 8,
				padding: "10px 14px",
				fontFamily: "monospace",
				fontSize: 12,
				lineHeight: 1.7
			},
			children: [
				loading && /* @__PURE__ */ _jsxDEV("div", {
					style: { color: "var(--text-faint)" },
					children: "Ачааллаж байна…"
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 219,
					columnNumber: 21
				}, this),
				!loading && filtered.length === 0 && /* @__PURE__ */ _jsxDEV("div", {
					style: { color: "var(--text-faint)" },
					children: "Лог байхгүй"
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 220,
					columnNumber: 47
				}, this),
				!loading && filtered.map((line, i) => /* @__PURE__ */ _jsxDEV("div", {
					style: {
						borderBottom: "1px solid var(--bg-panel)",
						padding: "1px 0",
						color: tagColor(line)
					},
					children: highlightLine(line)
				}, i, false, {
					fileName: _jsxFileName,
					lineNumber: 222,
					columnNumber: 11
				}, this))
			]
		}, void 0, true, {
			fileName: _jsxFileName,
			lineNumber: 218,
			columnNumber: 7
		}, this)]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 192,
		columnNumber: 5
	}, this);
}
_s2(LogsTab, "+aTfpkBzq1+dlAMNn3xOD5DUfGI=");
_c2 = LogsTab;
// ── Settings tab ─────────────────────────────────────────────────────────────
function SettingsTab({ token, canEdit }) {
	_s3();
	const [settings, setSettings] = useState({
		maxEvents: 500,
		reconnectInterval: 15,
		smtpUser: "",
		smtpPass: "",
		smtpFrom: "",
		emailSystem: "",
		emailOperations: "",
		emailFinance: ""
	});
	const [info, setInfo] = useState(null);
	const [emailUsers, setEmailUsers] = useState([]);
	const [saving, setSaving] = useState(false);
	const [saved, setSaved] = useState(false);
	const [testing, setTesting] = useState(false);
	const [testMsg, setTestMsg] = useState("");
	const H = {
		"Content-Type": "application/json",
		"X-Auth-Token": token
	};
	async function testEmail() {
		setTesting(true);
		setTestMsg("");
		try {
			const r = await fetch("/api/settings/test-email", {
				method: "POST",
				headers: H,
				body: "{}"
			});
			const d = await r.json();
			setTestMsg(d.ok ? `✓ Тест имэйл илгээгдлээ → ${d.to}` : `✗ ${d.error || "Алдаа"}`);
		} catch {
			setTestMsg("✗ Сервертэй холбогдохгүй");
		} finally {
			setTesting(false);
		}
	}
	useEffect(() => {
		fetch("/api/settings", { headers: H }).then((r) => r.ok ? r.json() : null).then((d) => {
			if (d) setSettings(d);
		});
		fetch("/api/system-info", { headers: H }).then((r) => r.ok ? r.json() : null).then((d) => {
			if (d) setInfo(d);
		});
		// Имэйлтэй бүртгэлтэй хэрэглэгчид (хариуцагч сонгоход)
		fetch("/api/users", { headers: H }).then((r) => r.ok ? r.json() : []).then((d) => {
			if (Array.isArray(d)) setEmailUsers(d.filter((u) => u.email).map((u) => ({
				username: u.username,
				email: u.email
			})));
		}).catch(() => {});
		const iv = setInterval(() => {
			fetch("/api/system-info", { headers: H }).then((r) => r.ok ? r.json() : null).then((d) => {
				if (d) setInfo(d);
			});
		}, 5e3);
		return () => clearInterval(iv);
	}, []);
	async function save(e) {
		e.preventDefault();
		setSaving(true);
		await fetch("/api/settings", {
			method: "PUT",
			headers: H,
			body: JSON.stringify(settings)
		});
		setSaving(false);
		setSaved(true);
		setTimeout(() => setSaved(false), 2500);
	}
	const row = (label, value) => /* @__PURE__ */ _jsxDEV("div", {
		style: {
			display: "flex",
			justifyContent: "space-between",
			alignItems: "center",
			padding: "8px 0",
			borderBottom: "1px solid var(--bg-elevated)"
		},
		children: [/* @__PURE__ */ _jsxDEV("span", {
			style: {
				fontSize: 13,
				color: "var(--text-muted)"
			},
			children: label
		}, void 0, false, {
			fileName: _jsxFileName,
			lineNumber: 280,
			columnNumber: 7
		}, this), /* @__PURE__ */ _jsxDEV("span", {
			style: {
				fontSize: 13,
				color: "var(--text-secondary)",
				fontFamily: "monospace"
			},
			children: value
		}, void 0, false, {
			fileName: _jsxFileName,
			lineNumber: 281,
			columnNumber: 7
		}, this)]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 279,
		columnNumber: 5
	}, this);
	// input талбар (component биш — фокус алдагдахгүй)
	const inpStyle = {
		background: "var(--bg-panel)",
		border: "1px solid var(--border)",
		borderRadius: 6,
		padding: "6px 10px",
		color: "var(--text-primary)",
		fontSize: 13,
		outline: "none",
		width: "100%",
		boxSizing: "border-box"
	};
	const field = (label, value, onChange, placeholder = "", type = "text") => /* @__PURE__ */ _jsxDEV("div", {
		style: {
			display: "flex",
			flexDirection: "column",
			gap: 4
		},
		children: [/* @__PURE__ */ _jsxDEV("label", {
			style: {
				fontSize: 12,
				color: "var(--text-muted)"
			},
			children: label
		}, void 0, false, {
			fileName: _jsxFileName,
			lineNumber: 289,
			columnNumber: 7
		}, this), /* @__PURE__ */ _jsxDEV("input", {
			type,
			value,
			onChange: (e) => onChange(e.target.value),
			placeholder,
			style: inpStyle
		}, void 0, false, {
			fileName: _jsxFileName,
			lineNumber: 290,
			columnNumber: 7
		}, this)]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 288,
		columnNumber: 5
	}, this);
	// имэйл талбар + бүртгэлтэй хэрэглэгчээс сонгох dropdown
	const emailField = (label, value, onChange, placeholder = "") => /* @__PURE__ */ _jsxDEV("div", {
		style: {
			display: "flex",
			flexDirection: "column",
			gap: 4
		},
		children: [
			/* @__PURE__ */ _jsxDEV("label", {
				style: {
					fontSize: 12,
					color: "var(--text-muted)"
				},
				children: label
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 296,
				columnNumber: 7
			}, this),
			/* @__PURE__ */ _jsxDEV("input", {
				type: "email",
				value,
				onChange: (e) => onChange(e.target.value),
				placeholder,
				style: inpStyle
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 297,
				columnNumber: 7
			}, this),
			emailUsers.length > 0 && /* @__PURE__ */ _jsxDEV("select", {
				value: "",
				onChange: (e) => {
					if (e.target.value) onChange(e.target.value);
				},
				style: {
					...inpStyle,
					fontSize: 12,
					color: "var(--text-muted)"
				},
				children: [/* @__PURE__ */ _jsxDEV("option", {
					value: "",
					children: "— Хэрэглэгчээс сонгох —"
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 300,
					columnNumber: 11
				}, this), emailUsers.map((u) => /* @__PURE__ */ _jsxDEV("option", {
					value: u.email,
					style: { color: "var(--text-primary)" },
					children: [
						u.username,
						" — ",
						u.email
					]
				}, u.username, true, {
					fileName: _jsxFileName,
					lineNumber: 301,
					columnNumber: 32
				}, this))]
			}, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 299,
				columnNumber: 9
			}, this)
		]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 295,
		columnNumber: 5
	}, this);
	return /* @__PURE__ */ _jsxDEV("div", {
		style: {
			display: "flex",
			flexDirection: "column",
			gap: 28
		},
		children: [
			info && /* @__PURE__ */ _jsxDEV("div", { children: [/* @__PURE__ */ _jsxDEV("div", {
				style: {
					fontSize: 12,
					fontWeight: 600,
					color: "var(--accent-blue)",
					marginBottom: 10,
					textTransform: "uppercase",
					letterSpacing: 1
				},
				children: "Систем мэдээлэл"
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 312,
				columnNumber: 11
			}, this), /* @__PURE__ */ _jsxDEV("div", {
				style: {
					background: "var(--bg-page)",
					border: "1px solid var(--bg-elevated)",
					borderRadius: 8,
					padding: "4px 16px"
				},
				children: [
					row("Uptime", formatUptime(info.uptimeSec)),
					row("Холбогдсон камер", `${info.connectedCameras} ш`),
					row("Санах ойн бүртгэл", `${info.eventCount} / ${info.maxEvents}`)
				]
			}, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 313,
				columnNumber: 11
			}, this)] }, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 311,
				columnNumber: 9
			}, this),
			/* @__PURE__ */ _jsxDEV("div", { children: [/* @__PURE__ */ _jsxDEV("div", {
				style: {
					fontSize: 12,
					fontWeight: 600,
					color: "var(--accent-blue)",
					marginBottom: 10,
					textTransform: "uppercase",
					letterSpacing: 1
				},
				children: "Тохиргоо"
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 323,
				columnNumber: 9
			}, this), /* @__PURE__ */ _jsxDEV("form", {
				onSubmit: save,
				style: {
					background: "var(--bg-page)",
					border: "1px solid var(--bg-elevated)",
					borderRadius: 8,
					padding: 16,
					display: "flex",
					flexDirection: "column",
					gap: 16
				},
				children: [
					/* @__PURE__ */ _jsxDEV("div", {
						style: {
							display: "flex",
							flexDirection: "column",
							gap: 6
						},
						children: [/* @__PURE__ */ _jsxDEV("label", {
							style: {
								fontSize: 13,
								color: "var(--text-muted)"
							},
							children: "Event хадгалах дээд тоо"
						}, void 0, false, {
							fileName: _jsxFileName,
							lineNumber: 326,
							columnNumber: 13
						}, this), /* @__PURE__ */ _jsxDEV("div", {
							style: {
								display: "flex",
								alignItems: "center",
								gap: 10
							},
							children: [/* @__PURE__ */ _jsxDEV("input", {
								type: "number",
								min: 50,
								max: 5e3,
								step: 50,
								value: settings.maxEvents,
								onChange: (e) => setSettings((p) => ({
									...p,
									maxEvents: Number(e.target.value)
								})),
								style: {
									width: 100,
									background: "var(--bg-panel)",
									border: "1px solid var(--border)",
									borderRadius: 6,
									padding: "6px 10px",
									color: "var(--text-primary)",
									fontSize: 13,
									outline: "none"
								}
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 328,
								columnNumber: 15
							}, this), /* @__PURE__ */ _jsxDEV("span", {
								style: {
									fontSize: 12,
									color: "var(--text-faint)"
								},
								children: "санах ойд хадгалах (50–5000)"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 334,
								columnNumber: 15
							}, this)]
						}, void 0, true, {
							fileName: _jsxFileName,
							lineNumber: 327,
							columnNumber: 13
						}, this)]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 325,
						columnNumber: 11
					}, this),
					/* @__PURE__ */ _jsxDEV("div", {
						style: {
							display: "flex",
							flexDirection: "column",
							gap: 6
						},
						children: [/* @__PURE__ */ _jsxDEV("label", {
							style: {
								fontSize: 13,
								color: "var(--text-muted)"
							},
							children: "Холболт тасрахад дахин холбох хугацаа"
						}, void 0, false, {
							fileName: _jsxFileName,
							lineNumber: 339,
							columnNumber: 13
						}, this), /* @__PURE__ */ _jsxDEV("div", {
							style: {
								display: "flex",
								alignItems: "center",
								gap: 10
							},
							children: [/* @__PURE__ */ _jsxDEV("input", {
								type: "number",
								min: 5,
								max: 300,
								step: 5,
								value: settings.reconnectInterval,
								onChange: (e) => setSettings((p) => ({
									...p,
									reconnectInterval: Number(e.target.value)
								})),
								style: {
									width: 100,
									background: "var(--bg-panel)",
									border: "1px solid var(--border)",
									borderRadius: 6,
									padding: "6px 10px",
									color: "var(--text-primary)",
									fontSize: 13,
									outline: "none"
								}
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 341,
								columnNumber: 15
							}, this), /* @__PURE__ */ _jsxDEV("span", {
								style: {
									fontSize: 12,
									color: "var(--text-faint)"
								},
								children: "секунд (5–300)"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 347,
								columnNumber: 15
							}, this)]
						}, void 0, true, {
							fileName: _jsxFileName,
							lineNumber: 340,
							columnNumber: 13
						}, this)]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 338,
						columnNumber: 11
					}, this),
					canEdit && /* @__PURE__ */ _jsxDEV("div", {
						style: {
							display: "flex",
							alignItems: "center",
							gap: 12
						},
						children: [/* @__PURE__ */ _jsxDEV("button", {
							type: "submit",
							disabled: saving,
							style: {
								padding: "7px 20px",
								background: "var(--accent-green-strong)",
								border: "none",
								borderRadius: 6,
								color: "var(--text-on-accent)",
								fontSize: 13,
								cursor: "pointer",
								opacity: saving ? .6 : 1
							},
							children: saving ? "Хадгалж байна…" : "Хадгалах"
						}, void 0, false, {
							fileName: _jsxFileName,
							lineNumber: 353,
							columnNumber: 13
						}, this), saved && /* @__PURE__ */ _jsxDEV("span", {
							style: {
								fontSize: 12,
								color: "var(--accent-green)"
							},
							children: "✓ Хадгалагдлаа"
						}, void 0, false, {
							fileName: _jsxFileName,
							lineNumber: 360,
							columnNumber: 23
						}, this)]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 352,
						columnNumber: 11
					}, this)
				]
			}, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 324,
				columnNumber: 9
			}, this)] }, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 322,
				columnNumber: 7
			}, this),
			/* @__PURE__ */ _jsxDEV("div", { children: [/* @__PURE__ */ _jsxDEV("div", {
				style: {
					fontSize: 12,
					fontWeight: 600,
					color: "var(--accent-blue)",
					marginBottom: 10,
					textTransform: "uppercase",
					letterSpacing: 1
				},
				children: "Имэйл (Gmail SMTP) тохиргоо"
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 368,
				columnNumber: 9
			}, this), /* @__PURE__ */ _jsxDEV("form", {
				onSubmit: save,
				style: {
					background: "var(--bg-page)",
					border: "1px solid var(--bg-elevated)",
					borderRadius: 8,
					padding: 16,
					display: "flex",
					flexDirection: "column",
					gap: 14
				},
				children: [
					/* @__PURE__ */ _jsxDEV("div", {
						style: {
							display: "grid",
							gridTemplateColumns: "1fr 1fr",
							gap: 12
						},
						children: [
							field("Gmail хаяг", settings.smtpUser, (v) => setSettings((p) => ({
								...p,
								smtpUser: v
							})), "noreply@gmail.com"),
							field(`App Password${settings.smtpPassSet ? " (хадгалсан)" : ""}`, settings.smtpPass, (v) => setSettings((p) => ({
								...p,
								smtpPass: v
							})), settings.smtpPassSet ? "•••• өөрчлөхгүй бол хоосон" : "16 оронтой App Password", "password"),
							field("From хаяг", settings.smtpFrom, (v) => setSettings((p) => ({
								...p,
								smtpFrom: v
							})), "(хоосон бол Gmail хаягийг ашиглана)")
						]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 370,
						columnNumber: 11
					}, this),
					/* @__PURE__ */ _jsxDEV("div", {
						style: {
							borderTop: "1px solid var(--bg-elevated)",
							paddingTop: 12,
							fontSize: 12,
							color: "var(--text-muted)"
						},
						children: "Хариуцагч хэлтсийн хүлээн авах имэйл"
					}, void 0, false, {
						fileName: _jsxFileName,
						lineNumber: 375,
						columnNumber: 11
					}, this),
					/* @__PURE__ */ _jsxDEV("div", {
						style: {
							display: "grid",
							gridTemplateColumns: "1fr 1fr 1fr",
							gap: 12
						},
						children: [
							emailField("Систем", settings.emailSystem, (v) => setSettings((p) => ({
								...p,
								emailSystem: v
							})), "...@easy-parking.mn"),
							emailField("Үйл ажиллагаа", settings.emailOperations, (v) => setSettings((p) => ({
								...p,
								emailOperations: v
							})), "...@easy-parking.mn"),
							emailField("Санхүү", settings.emailFinance, (v) => setSettings((p) => ({
								...p,
								emailFinance: v
							})), "(заавал биш)")
						]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 376,
						columnNumber: 11
					}, this),
					canEdit && /* @__PURE__ */ _jsxDEV("div", {
						style: {
							display: "flex",
							alignItems: "center",
							gap: 12,
							flexWrap: "wrap"
						},
						children: [
							/* @__PURE__ */ _jsxDEV("button", {
								type: "submit",
								disabled: saving,
								style: {
									padding: "7px 20px",
									background: "var(--accent-green-strong)",
									border: "none",
									borderRadius: 6,
									color: "var(--text-on-accent)",
									fontSize: 13,
									cursor: "pointer",
									opacity: saving ? .6 : 1
								},
								children: saving ? "Хадгалж байна…" : "Хадгалах"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 383,
								columnNumber: 13
							}, this),
							/* @__PURE__ */ _jsxDEV("button", {
								type: "button",
								onClick: testEmail,
								disabled: testing,
								style: {
									padding: "7px 16px",
									background: "var(--bg-elevated)",
									border: "1px solid var(--border)",
									borderRadius: 6,
									color: "var(--text-secondary)",
									fontSize: 13,
									cursor: "pointer"
								},
								children: testing ? "Илгээж байна…" : "Тест имэйл"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 386,
								columnNumber: 13
							}, this),
							testMsg && /* @__PURE__ */ _jsxDEV("span", {
								style: {
									fontSize: 12,
									color: testMsg.startsWith("✓") ? "var(--accent-green)" : "var(--accent-red)"
								},
								children: testMsg
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 389,
								columnNumber: 25
							}, this)
						]
					}, void 0, true, {
						fileName: _jsxFileName,
						lineNumber: 382,
						columnNumber: 11
					}, this)
				]
			}, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 369,
				columnNumber: 9
			}, this)] }, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 367,
				columnNumber: 7
			}, this)
		]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 308,
		columnNumber: 5
	}, this);
}
_s3(SettingsTab, "pTPT2L7nDQpYaIP9h3x5aELS6vg=");
_c3 = SettingsTab;
// ── AdminPanel ────────────────────────────────────────────────────────────────
export default function AdminPanel({ token, role, permissions, onClose }) {
	_s4();
	// Эрхийн шалгалт (backend can()-тэй ижил логик)
	const cap = (menu, action) => {
		if (role === "admin") return true;
		if (role === "operator") {
			if (menu === "logs") return action === "view";
			return false;
		}
		if (role === "manager") return !!permissions?.[menu]?.[action];
		return false;
	};
	const visibleTabs = Object.keys(TAB_LABELS).filter((t) => cap(t, "view"));
	const [tab, setTab] = useState(visibleTabs[0] ?? "parking");
	return /* @__PURE__ */ _jsxDEV("div", {
		className: "admin-page",
		children: [/* @__PURE__ */ _jsxDEV("div", {
			className: "admin-panel-header",
			children: /* @__PURE__ */ _jsxDEV("div", {
				className: "admin-panel-tabs",
				children: visibleTabs.map((t) => /* @__PURE__ */ _jsxDEV("button", {
					className: `admin-tab-btn${tab === t ? " active" : ""}`,
					onClick: () => setTab(t),
					children: TAB_LABELS[t]
				}, t, false, {
					fileName: _jsxFileName,
					lineNumber: 419,
					columnNumber: 13
				}, this))
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 417,
				columnNumber: 9
			}, this)
		}, void 0, false, {
			fileName: _jsxFileName,
			lineNumber: 416,
			columnNumber: 7
		}, this), /* @__PURE__ */ _jsxDEV("div", {
			className: "admin-panel-body",
			children: [
				tab === "parking" && cap("parking", "view") && /* @__PURE__ */ _jsxDEV(ParkingManagement, {
					token,
					onClose,
					embedded: true,
					canEdit: cap("parking", "edit"),
					canDelete: cap("parking", "delete")
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 431,
					columnNumber: 63
				}, this),
				tab === "users" && cap("users", "view") && /* @__PURE__ */ _jsxDEV(UserManagement, {
					token,
					onClose,
					embedded: true,
					canEdit: cap("users", "edit"),
					canDelete: cap("users", "delete")
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 432,
					columnNumber: 63
				}, this),
				tab === "reasons" && cap("reasons", "view") && /* @__PURE__ */ _jsxDEV(ReasonsTab, {
					token,
					canEdit: cap("reasons", "edit"),
					canDelete: cap("reasons", "delete")
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 433,
					columnNumber: 63
				}, this),
				tab === "settings" && cap("settings", "view") && /* @__PURE__ */ _jsxDEV(SettingsTab, {
					token,
					canEdit: cap("settings", "edit")
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 434,
					columnNumber: 63
				}, this),
				tab === "logs" && cap("logs", "view") && /* @__PURE__ */ _jsxDEV(LogsTab, { token }, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 435,
					columnNumber: 63
				}, this),
				tab === "tunnels" && cap("tunnels", "view") && /* @__PURE__ */ _jsxDEV(TunnelsPage, { token }, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 436,
					columnNumber: 63
				}, this)
			]
		}, void 0, true, {
			fileName: _jsxFileName,
			lineNumber: 430,
			columnNumber: 7
		}, this)]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 415,
		columnNumber: 5
	}, this);
}
_s4(AdminPanel, "MyfuKo+u2wwwOpZ+wOB+3S6Irgc=");
_c4 = AdminPanel;
var _c, _c2, _c3, _c4;
$RefreshReg$(_c, "ReasonsTab");
$RefreshReg$(_c2, "LogsTab");
$RefreshReg$(_c3, "SettingsTab");
$RefreshReg$(_c4, "AdminPanel");
import * as RefreshRuntime from "/@react-refresh";
const inWebWorker = typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope;
import * as __vite_react_currentExports from "/src/components/AdminPanel.tsx";
if (import.meta.hot && !inWebWorker) {
  if (!window.$RefreshReg$) {
    throw new Error(
      "@vitejs/plugin-react can't detect preamble. Something is wrong."
    );
  }

  const currentExports = __vite_react_currentExports;
  queueMicrotask(() => {
    RefreshRuntime.registerExportsForReactRefresh("/home/anpruser/anpr-app/src/components/AdminPanel.tsx", currentExports);
    import.meta.hot.accept((nextExports) => {
      if (!nextExports) return;
      const invalidateMessage = RefreshRuntime.validateRefreshBoundaryAndEnqueueUpdate("/home/anpruser/anpr-app/src/components/AdminPanel.tsx", currentExports, nextExports);
      if (invalidateMessage) import.meta.hot.invalidate(invalidateMessage);
    });
  });
}
function $RefreshReg$(type, id) { return RefreshRuntime.register(type, "/home/anpruser/anpr-app/src/components/AdminPanel.tsx" + ' ' + id); }
function $RefreshSig$() { return RefreshRuntime.createSignatureFunctionForTransform(); }

//# sourceMappingURL=data:application/json;base64,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LEVBQW9CLE1BQVE7Ozs7O0dBQy9FOzs7OztVQUNGOzs7Ozs7QUFFVCIsIm5hbWVzIjpbXSwic291cmNlcyI6WyJBZG1pblBhbmVsLnRzeCJdLCJ2ZXJzaW9uIjozLCJzb3VyY2VzQ29udGVudCI6WyJpbXBvcnQgeyB1c2VFZmZlY3QsIHVzZVN0YXRlIH0gZnJvbSAncmVhY3QnO1xuaW1wb3J0IFBhcmtpbmdNYW5hZ2VtZW50IGZyb20gJy4vUGFya2luZ01hbmFnZW1lbnQnO1xuaW1wb3J0IFVzZXJNYW5hZ2VtZW50IGZyb20gJy4vVXNlck1hbmFnZW1lbnQnO1xuaW1wb3J0IFR1bm5lbHNQYWdlIGZyb20gJy4vVHVubmVsc1BhZ2UnO1xuXG50eXBlIFRhYiA9ICdwYXJraW5nJyB8ICd1c2VycycgfCAncmVhc29ucycgfCAnc2V0dGluZ3MnIHwgJ2xvZ3MnIHwgJ3R1bm5lbHMnO1xuXG5pbnRlcmZhY2UgT3BlblJlYXNvbiB7IGlkOiBudW1iZXI7IGxhYmVsOiBzdHJpbmcgfVxuaW50ZXJmYWNlIFN5c1NldHRpbmdzIHtcbiAgbWF4RXZlbnRzOiBudW1iZXI7IHJlY29ubmVjdEludGVydmFsOiBudW1iZXI7XG4gIHNtdHBVc2VyOiBzdHJpbmc7IHNtdHBQYXNzOiBzdHJpbmc7IHNtdHBGcm9tOiBzdHJpbmc7XG4gIGVtYWlsU3lzdGVtOiBzdHJpbmc7IGVtYWlsT3BlcmF0aW9uczogc3RyaW5nOyBlbWFpbEZpbmFuY2U6IHN0cmluZztcbiAgc210cFBhc3NTZXQ/OiBib29sZWFuO1xufVxuaW50ZXJmYWNlIFN5c3RlbUluZm8geyB1cHRpbWVTZWM6IG51bWJlcjsgY29ubmVjdGVkQ2FtZXJhczogbnVtYmVyOyBldmVudENvdW50OiBudW1iZXI7IG1heEV2ZW50czogbnVtYmVyIH1cblxuaW50ZXJmYWNlIE1lbnVQZXJtIHsgdmlldzogYm9vbGVhbjsgZWRpdDogYm9vbGVhbjsgZGVsZXRlOiBib29sZWFuOyBjcmVhdGU6IGJvb2xlYW4gfVxuaW50ZXJmYWNlIFByb3BzIHsgdG9rZW46IHN0cmluZzsgcm9sZTogc3RyaW5nOyBwZXJtaXNzaW9ucz86IFJlY29yZDxzdHJpbmcsIE1lbnVQZXJtPjsgb25DbG9zZTogKCkgPT4gdm9pZCB9XG5cbmNvbnN0IFRBQl9MQUJFTFM6IFJlY29yZDxUYWIsIHN0cmluZz4gPSB7XG4gIHBhcmtpbmc6ICAgICfQl9C+0LPRgdC+0L7QuycsXG4gIHVzZXJzOiAgICAgICfQpdGN0YDRjdCz0LvRjdCz0YcnLFxuICByZWFzb25zOiAgICAn0J3RjdGN0YUg0YjQsNC70YLQs9Cw0LDQvScsXG4gIHNldHRpbmdzOiAgICfQotC+0YXQuNGA0LPQvtC+JyxcbiAgbG9nczogICAgICAgJ9Cb0L7QsycsXG4gIHR1bm5lbHM6ICAgICfQodCw0LvQsdCw0YAnLFxufTtcblxuZnVuY3Rpb24gZm9ybWF0VXB0aW1lKHNlYzogbnVtYmVyKSB7XG4gIGNvbnN0IGggPSBNYXRoLmZsb29yKHNlYyAvIDM2MDApLCBtID0gTWF0aC5mbG9vcigoc2VjICUgMzYwMCkgLyA2MCksIHMgPSBzZWMgJSA2MDtcbiAgcmV0dXJuIGAke1N0cmluZyhoKS5wYWRTdGFydCgyLCAnMCcpfToke1N0cmluZyhtKS5wYWRTdGFydCgyLCAnMCcpfToke1N0cmluZyhzKS5wYWRTdGFydCgyLCAnMCcpfWA7XG59XG5cbi8vIOKUgOKUgCBSZWFzb25zIHRhYiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcbmZ1bmN0aW9uIFJlYXNvbnNUYWIoeyB0b2tlbiwgY2FuRWRpdCwgY2FuRGVsZXRlIH06IHsgdG9rZW46IHN0cmluZzsgY2FuRWRpdDogYm9vbGVhbjsgY2FuRGVsZXRlOiBib29sZWFuIH0pIHtcbiAgY29uc3QgW3JlYXNvbnMsIHNldFJlYXNvbnNdID0gdXNlU3RhdGU8T3BlblJlYXNvbltdPihbXSk7XG4gIGNvbnN0IFtuZXdMYWJlbCwgc2V0TmV3TGFiZWxdID0gdXNlU3RhdGUoJycpO1xuICBjb25zdCBbYWRkaW5nLCBzZXRBZGRpbmddID0gdXNlU3RhdGUoZmFsc2UpO1xuICBjb25zdCBIID0geyAnQ29udGVudC1UeXBlJzogJ2FwcGxpY2F0aW9uL2pzb24nLCAnWC1BdXRoLVRva2VuJzogdG9rZW4gfTtcblxuICBhc3luYyBmdW5jdGlvbiBsb2FkKCkge1xuICAgIGNvbnN0IHIgPSBhd2FpdCBmZXRjaCgnL2FwaS9vcGVuLXJlYXNvbnMnLCB7IGhlYWRlcnM6IEggfSk7XG4gICAgaWYgKHIub2spIHNldFJlYXNvbnMoYXdhaXQgci5qc29uKCkpO1xuICB9XG4gIHVzZUVmZmVjdCgoKSA9PiB7IGxvYWQoKTsgfSwgW10pO1xuXG4gIGFzeW5jIGZ1bmN0aW9uIGFkZFJlYXNvbihlOiBSZWFjdC5Gb3JtRXZlbnQpIHtcbiAgICBlLnByZXZlbnREZWZhdWx0KCk7XG4gICAgaWYgKCFuZXdMYWJlbC50cmltKCkpIHJldHVybjtcbiAgICBzZXRBZGRpbmcodHJ1ZSk7XG4gICAgYXdhaXQgZmV0Y2goJy9hcGkvb3Blbi1yZWFzb25zJywgeyBtZXRob2Q6ICdQT1NUJywgaGVhZGVyczogSCwgYm9keTogSlNPTi5zdHJpbmdpZnkoeyBsYWJlbDogbmV3TGFiZWwudHJpbSgpIH0pIH0pO1xuICAgIHNldE5ld0xhYmVsKCcnKTtcbiAgICBhd2FpdCBsb2FkKCk7XG4gICAgc2V0QWRkaW5nKGZhbHNlKTtcbiAgfVxuXG4gIGFzeW5jIGZ1bmN0aW9uIGRlbGV0ZVJlYXNvbihpZDogbnVtYmVyLCBsYWJlbDogc3RyaW5nKSB7XG4gICAgaWYgKCFjb25maXJtKGBcIiR7bGFiZWx9XCIg0YjQsNC70YLQs9Cw0LDQvdGL0LMg0YPRgdGC0LPQsNGFINGD0YM/YCkpIHJldHVybjtcbiAgICBhd2FpdCBmZXRjaChgL2FwaS9vcGVuLXJlYXNvbnMvJHtpZH1gLCB7IG1ldGhvZDogJ0RFTEVURScsIGhlYWRlcnM6IEggfSk7XG4gICAgbG9hZCgpO1xuICB9XG5cbiAgcmV0dXJuIChcbiAgICA8ZGl2PlxuICAgICAge2NhbkVkaXQgJiYgKFxuICAgICAgPGZvcm0gb25TdWJtaXQ9e2FkZFJlYXNvbn0gc3R5bGU9e3sgZGlzcGxheTogJ2ZsZXgnLCBnYXA6IDgsIG1hcmdpbkJvdHRvbTogMjAgfX0+XG4gICAgICAgIDxpbnB1dFxuICAgICAgICAgIHZhbHVlPXtuZXdMYWJlbH1cbiAgICAgICAgICBvbkNoYW5nZT17ZSA9PiBzZXROZXdMYWJlbChlLnRhcmdldC52YWx1ZSl9XG4gICAgICAgICAgcGxhY2Vob2xkZXI9XCLQqNC40L3RjSDRiNCw0LvRgtCz0LDQsNC9INC90Y3QvNGN0YUuLi5cIlxuICAgICAgICAgIHN0eWxlPXt7IGZsZXg6IDEsIGJhY2tncm91bmQ6ICd2YXIoLS1iZy1wYWdlKScsIGJvcmRlcjogJzFweCBzb2xpZCB2YXIoLS1ib3JkZXIpJywgYm9yZGVyUmFkaXVzOiA2LCBwYWRkaW5nOiAnN3B4IDEycHgnLCBjb2xvcjogJ3ZhcigtLXRleHQtcHJpbWFyeSknLCBmb250U2l6ZTogMTMsIG91dGxpbmU6ICdub25lJyB9fVxuICAgICAgICAvPlxuICAgICAgICA8YnV0dG9uXG4gICAgICAgICAgdHlwZT1cInN1Ym1pdFwiXG4gICAgICAgICAgZGlzYWJsZWQ9e2FkZGluZyB8fCAhbmV3TGFiZWwudHJpbSgpfVxuICAgICAgICAgIHN0eWxlPXt7IHBhZGRpbmc6ICc3cHggMThweCcsIGJhY2tncm91bmQ6ICd2YXIoLS1hY2NlbnQtZ3JlZW4tc3Ryb25nKScsIGJvcmRlcjogJ25vbmUnLCBib3JkZXJSYWRpdXM6IDYsIGNvbG9yOiAndmFyKC0tdGV4dC1vbi1hY2NlbnQpJywgZm9udFNpemU6IDEzLCBjdXJzb3I6ICdwb2ludGVyJywgb3BhY2l0eTogYWRkaW5nID8gMC42IDogMSB9fVxuICAgICAgICA+XG4gICAgICAgICAgKyDQndGN0LzRjdGFXG4gICAgICAgIDwvYnV0dG9uPlxuICAgICAgPC9mb3JtPlxuICAgICAgKX1cblxuICAgICAgPGRpdiBzdHlsZT17eyBkaXNwbGF5OiAnZmxleCcsIGZsZXhEaXJlY3Rpb246ICdjb2x1bW4nLCBnYXA6IDYgfX0+XG4gICAgICAgIHtyZWFzb25zLmxlbmd0aCA9PT0gMCAmJiAoXG4gICAgICAgICAgPGRpdiBzdHlsZT17eyBjb2xvcjogJ3ZhcigtLXRleHQtZmFpbnQpJywgZm9udFNpemU6IDEzLCB0ZXh0QWxpZ246ICdjZW50ZXInLCBwYWRkaW5nOiAzMiB9fT7QqNCw0LvRgtCz0LDQsNC9INCx0LDQudGF0LPSr9C5INCx0LDQudC90LA8L2Rpdj5cbiAgICAgICAgKX1cbiAgICAgICAge3JlYXNvbnMubWFwKChyLCBpKSA9PiAoXG4gICAgICAgICAgPGRpdiBrZXk9e3IuaWR9IHN0eWxlPXt7IGRpc3BsYXk6ICdmbGV4JywgYWxpZ25JdGVtczogJ2NlbnRlcicsIGdhcDogMTAsIHBhZGRpbmc6ICc5cHggMTRweCcsIGJhY2tncm91bmQ6ICd2YXIoLS1iZy1wYW5lbCknLCBib3JkZXI6ICcxcHggc29saWQgdmFyKC0tYmctZWxldmF0ZWQpJywgYm9yZGVyUmFkaXVzOiA2IH19PlxuICAgICAgICAgICAgPHNwYW4gc3R5bGU9e3sgZm9udFNpemU6IDEyLCBjb2xvcjogJ3ZhcigtLXRleHQtZmFpbnQpJywgd2lkdGg6IDIyLCB0ZXh0QWxpZ246ICdyaWdodCcgfX0+e2kgKyAxfTwvc3Bhbj5cbiAgICAgICAgICAgIDxzcGFuIHN0eWxlPXt7IGZsZXg6IDEsIGZvbnRTaXplOiAxNCwgY29sb3I6ICd2YXIoLS10ZXh0LXNlY29uZGFyeSknIH19PntyLmxhYmVsfTwvc3Bhbj5cbiAgICAgICAgICAgIHtjYW5EZWxldGUgJiYgci5sYWJlbCAhPT0gJ9CR0YPRgdCw0LQnICYmIChcbiAgICAgICAgICAgIDxidXR0b25cbiAgICAgICAgICAgICAgb25DbGljaz17KCkgPT4gZGVsZXRlUmVhc29uKHIuaWQsIHIubGFiZWwpfVxuICAgICAgICAgICAgICBzdHlsZT17eyBiYWNrZ3JvdW5kOiAnbm9uZScsIGJvcmRlcjogJ25vbmUnLCBjb2xvcjogJ3ZhcigtLWFjY2VudC1yZWQpJywgZm9udFNpemU6IDE0LCBjdXJzb3I6ICdwb2ludGVyJywgcGFkZGluZzogJzJweCA2cHgnLCBib3JkZXJSYWRpdXM6IDQgfX1cbiAgICAgICAgICAgICAgdGl0bGU9XCLQo9GB0YLQs9Cw0YVcIlxuICAgICAgICAgICAgPuKclTwvYnV0dG9uPlxuICAgICAgICAgICAgKX1cbiAgICAgICAgICAgIHtjYW5EZWxldGUgJiYgci5sYWJlbCA9PT0gJ9CR0YPRgdCw0LQnICYmIChcbiAgICAgICAgICAgIDxzcGFuIHN0eWxlPXt7IGZvbnRTaXplOiAxMSwgY29sb3I6ICd2YXIoLS10ZXh0LWZhaW50KScgfX0gdGl0bGU9XCLQotCw0LnQu9Cx0LDRgCDQsdC40YfQuNGFINGB0L7QvdCz0L7Qu9GC0YvQsyDRgdC40YHRgtC10Lwg0LDRiNC40LPQu9Cw0LTQsNCzINGC0YPQuyDRg9GB0YLQs9Cw0YUg0LHQvtC70L7QvNC20LPSr9C5XCI+8J+Ukjwvc3Bhbj5cbiAgICAgICAgICAgICl9XG4gICAgICAgICAgPC9kaXY+XG4gICAgICAgICkpfVxuICAgICAgPC9kaXY+XG4gICAgPC9kaXY+XG4gICk7XG59XG5cbi8vIOKUgOKUgCBMb2dzIHRhYiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcbmNvbnN0IFRBR19DT0xPUjogUmVjb3JkPHN0cmluZywgc3RyaW5nPiA9IHtcbiAgJ1tBTlBSX0VWRU5UXSc6ICAgICd2YXIoLS1hY2NlbnQtYmx1ZSknLFxuICAnW0JBUlJJRVJfT1BFTl0nOiAgJ3ZhcigtLWFjY2VudC1ncmVlbiknLFxuICAnW0JBUlJJRVJfQ0xPU0VdJzogJ3ZhcigtLWFjY2VudC1yZWQpJyxcbiAgJ1tMT0dJTl0nOiAgICAgICAgICd2YXIoLS1hY2NlbnQtYmx1ZS1wYWxlKScsXG4gICdbTE9HSU5fRkFJTF0nOiAgICAndmFyKC0tYWNjZW50LW9yYW5nZSknLFxuICAnW1VTRVJfQUREXSc6ICAgICAgJ3ZhcigtLWFjY2VudC1wdXJwbGUpJyxcbiAgJ1tVU0VSX0RFTEVURV0nOiAgICd2YXIoLS1hY2NlbnQtb3JhbmdlKScsXG4gICdbVVNFUl9VUERBVEVdJzogICAndmFyKC0tYWNjZW50LXB1cnBsZSknLFxuICAnW0xPVF9BRERdJzogICAgICAgJ3ZhcigtLWFjY2VudC1ibHVlLWxpZ2h0KScsXG4gICdbTE9UX1VQREFURV0nOiAgICAndmFyKC0tYWNjZW50LWJsdWUtbGlnaHQpJyxcbiAgJ1tMT1RfREVMRVRFXSc6ICAgICd2YXIoLS1hY2NlbnQtb3JhbmdlKScsXG4gICdbQ0FNX0FERF0nOiAgICAgICAndmFyKC0tYWNjZW50LWdyZWVuLWxpZ2h0KScsXG4gICdbQ0FNX0RFTEVURV0nOiAgICAndmFyKC0tYWNjZW50LW9yYW5nZSknLFxuICAnW0NBTV9VUERBVEVdJzogICAgJ3ZhcigtLWFjY2VudC1ncmVlbi1saWdodCknLFxuICAnW1JFQVNPTl9BRERdJzogICAgJ3ZhcigtLWFjY2VudC15ZWxsb3cpJyxcbiAgJ1tSRUFTT05fREVMRVRFXSc6ICd2YXIoLS1hY2NlbnQtb3JhbmdlKScsXG4gICdbQ09NUExBSU5UX0FERF0nOiAgICAndmFyKC0tYWNjZW50LXBpbmspJyxcbiAgJ1tDT01QTEFJTlRfVVBEQVRFXSc6ICd2YXIoLS1hY2NlbnQtcGluayknLFxuICAnW0NPTVBMQUlOVF9QUk9HUkVTU10nOiAndmFyKC0tYWNjZW50LXBpbmspJyxcbiAgJ1tDT01QTEFJTlRfREVMRVRFXSc6ICd2YXIoLS1hY2NlbnQtb3JhbmdlKScsXG4gICdbU0VUVElOR1NdJzogICAgICAndmFyKC0tdGV4dC1tdXRlZCknLFxufTtcblxuZnVuY3Rpb24gTG9nc1RhYih7IHRva2VuIH06IHsgdG9rZW46IHN0cmluZyB9KSB7XG4gIGNvbnN0IFtkYXRlcywgc2V0RGF0ZXNdICAgPSB1c2VTdGF0ZTxzdHJpbmdbXT4oW10pO1xuICBjb25zdCBbZGF0ZSwgc2V0RGF0ZV0gICAgID0gdXNlU3RhdGUoJycpO1xuICBjb25zdCBbbGluZXMsIHNldExpbmVzXSAgID0gdXNlU3RhdGU8c3RyaW5nW10+KFtdKTtcbiAgY29uc3QgW2ZpbHRlciwgc2V0RmlsdGVyXSA9IHVzZVN0YXRlKCcnKTtcbiAgY29uc3QgW2xvYWRpbmcsIHNldExvYWRpbmddID0gdXNlU3RhdGUoZmFsc2UpO1xuICBjb25zdCBIID0geyAnWC1BdXRoLVRva2VuJzogdG9rZW4gfTtcblxuICB1c2VFZmZlY3QoKCkgPT4ge1xuICAgIGZldGNoKCcvYXBpL2xvZ3MnLCB7IGhlYWRlcnM6IEggfSlcbiAgICAgIC50aGVuKHIgPT4gci5vayA/IHIuanNvbigpIDogW10pXG4gICAgICAudGhlbigoZDogc3RyaW5nW10pID0+IHsgc2V0RGF0ZXMoZCk7IGlmIChkLmxlbmd0aCkgc2V0RGF0ZShkWzBdKTsgfSk7XG4gIH0sIFtdKTtcblxuICB1c2VFZmZlY3QoKCkgPT4ge1xuICAgIGlmICghZGF0ZSkgcmV0dXJuO1xuICAgIHNldExvYWRpbmcodHJ1ZSk7XG4gICAgZmV0Y2goYC9hcGkvbG9ncy8ke2RhdGV9YCwgeyBoZWFkZXJzOiBIIH0pXG4gICAgICAudGhlbihyID0+IHIub2sgPyByLnRleHQoKSA6ICcnKVxuICAgICAgLnRoZW4odHh0ID0+IHtcbiAgICAgICAgc2V0TGluZXModHh0LnRyaW0oKSA/IHR4dC50cmltKCkuc3BsaXQoJ1xcbicpLnJldmVyc2UoKSA6IFtdKTtcbiAgICAgICAgc2V0TG9hZGluZyhmYWxzZSk7XG4gICAgICB9KTtcbiAgfSwgW2RhdGVdKTtcblxuICBjb25zdCBmaWx0ZXJlZCA9IGZpbHRlclxuICAgID8gbGluZXMuZmlsdGVyKGwgPT4gbC50b0xvd2VyQ2FzZSgpLmluY2x1ZGVzKGZpbHRlci50b0xvd2VyQ2FzZSgpKSlcbiAgICA6IGxpbmVzO1xuXG4gIGZ1bmN0aW9uIGRvd25sb2FkQ3N2KCkge1xuICAgIGNvbnN0IGhlYWRlciA9ICdkYXRlLHRpbWUsdGFnLGRldGFpbCc7XG4gICAgY29uc3Qgcm93cyA9IGZpbHRlcmVkLm1hcChsaW5lID0+IHtcbiAgICAgIGNvbnN0IG0gPSBsaW5lLm1hdGNoKC9eKFxcZHs0fS1cXGR7Mn0tXFxkezJ9KSAoXFxkezJ9OlxcZHsyfTpcXGR7Mn0pXFxzKyhcXFtbXFx3X10rXFxdKVxccyooLiopJC8pO1xuICAgICAgY29uc3QgY29scyA9IG0gPyBbbVsxXSwgbVsyXSwgbVszXSwgbVs0XV0gOiBbJycsICcnLCAnJywgbGluZV07XG4gICAgICByZXR1cm4gY29scy5tYXAoYyA9PiBgXCIke2MucmVwbGFjZSgvXCIvZywgJ1wiXCInKX1cImApLmpvaW4oJywnKTtcbiAgICB9KTtcbiAgICBjb25zdCBjc3YgPSBbaGVhZGVyLCAuLi5yb3dzXS5qb2luKCdcXHJcXG4nKTtcbiAgICBjb25zdCBibG9iID0gbmV3IEJsb2IoWyfvu78nICsgY3N2XSwgeyB0eXBlOiAndGV4dC9jc3Y7Y2hhcnNldD11dGYtODsnIH0pO1xuICAgIGNvbnN0IHVybCA9IFVSTC5jcmVhdGVPYmplY3RVUkwoYmxvYik7XG4gICAgY29uc3QgbGluayA9IGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2EnKTtcbiAgICBsaW5rLmhyZWYgPSB1cmw7IGxpbmsuZG93bmxvYWQgPSBgbG9nXyR7ZGF0ZSB8fCAnZXhwb3J0J30uY3N2YDsgbGluay5jbGljaygpO1xuICAgIFVSTC5yZXZva2VPYmplY3RVUkwodXJsKTtcbiAgfVxuXG4gIGZ1bmN0aW9uIHRhZ0NvbG9yKGxpbmU6IHN0cmluZykge1xuICAgIGNvbnN0IG0gPSBsaW5lLm1hdGNoKC9cXFtbXFx3X10rXFxdLyk7XG4gICAgcmV0dXJuIG0gPyAoVEFHX0NPTE9SW21bMF1dID8/ICd2YXIoLS10ZXh0LW11dGVkKScpIDogJ3ZhcigtLXRleHQtbXV0ZWQpJztcbiAgfVxuXG4gIGZ1bmN0aW9uIGhpZ2hsaWdodExpbmUobGluZTogc3RyaW5nKSB7XG4gICAgY29uc3QgcGFydHMgPSBsaW5lLnNwbGl0KC8oXFxbW1xcd19dK1xcXSkvKTtcbiAgICByZXR1cm4gcGFydHMubWFwKChwLCBpKSA9PlxuICAgICAgL15cXFtbXFx3X10rXFxdJC8udGVzdChwKVxuICAgICAgICA/IDxzcGFuIGtleT17aX0gc3R5bGU9e3sgY29sb3I6IFRBR19DT0xPUltwXSA/PyAndmFyKC0tdGV4dC1tdXRlZCknLCBmb250V2VpZ2h0OiA2MDAgfX0+e3B9PC9zcGFuPlxuICAgICAgICA6IDxzcGFuIGtleT17aX0gc3R5bGU9e3sgY29sb3I6ICd2YXIoLS10ZXh0LW11dGVkKScgfX0+e3B9PC9zcGFuPlxuICAgICk7XG4gIH1cblxuICByZXR1cm4gKFxuICAgIDxkaXYgc3R5bGU9e3sgZGlzcGxheTogJ2ZsZXgnLCBmbGV4RGlyZWN0aW9uOiAnY29sdW1uJywgZ2FwOiAxMiwgaGVpZ2h0OiAnMTAwJScgfX0+XG4gICAgICA8ZGl2IHN0eWxlPXt7IGRpc3BsYXk6ICdmbGV4JywgZ2FwOiAxMCwgYWxpZ25JdGVtczogJ2NlbnRlcicgfX0+XG4gICAgICAgIDxzZWxlY3RcbiAgICAgICAgICB2YWx1ZT17ZGF0ZX1cbiAgICAgICAgICBvbkNoYW5nZT17ZSA9PiBzZXREYXRlKGUudGFyZ2V0LnZhbHVlKX1cbiAgICAgICAgICBzdHlsZT17eyBiYWNrZ3JvdW5kOiAndmFyKC0tYmctcGFnZSknLCBib3JkZXI6ICcxcHggc29saWQgdmFyKC0tYm9yZGVyKScsIGNvbG9yOiAndmFyKC0tdGV4dC1wcmltYXJ5KScsIGJvcmRlclJhZGl1czogNiwgcGFkZGluZzogJzZweCAxMHB4JywgZm9udFNpemU6IDEzIH19XG4gICAgICAgID5cbiAgICAgICAgICB7ZGF0ZXMubWFwKGQgPT4gPG9wdGlvbiBrZXk9e2R9IHZhbHVlPXtkfT57ZH08L29wdGlvbj4pfVxuICAgICAgICA8L3NlbGVjdD5cbiAgICAgICAgPGlucHV0XG4gICAgICAgICAgdmFsdWU9e2ZpbHRlcn1cbiAgICAgICAgICBvbkNoYW5nZT17ZSA9PiBzZXRGaWx0ZXIoZS50YXJnZXQudmFsdWUpfVxuICAgICAgICAgIHBsYWNlaG9sZGVyPVwi0KXQsNC50YUuLi4gKHBsYXRlLCB1c2VyLCBpcC4uLilcIlxuICAgICAgICAgIHN0eWxlPXt7IGZsZXg6IDEsIGJhY2tncm91bmQ6ICd2YXIoLS1iZy1wYWdlKScsIGJvcmRlcjogJzFweCBzb2xpZCB2YXIoLS1ib3JkZXIpJywgYm9yZGVyUmFkaXVzOiA2LCBwYWRkaW5nOiAnNnB4IDEycHgnLCBjb2xvcjogJ3ZhcigtLXRleHQtcHJpbWFyeSknLCBmb250U2l6ZTogMTMsIG91dGxpbmU6ICdub25lJyB9fVxuICAgICAgICAvPlxuICAgICAgICA8c3BhbiBzdHlsZT17eyBmb250U2l6ZTogMTIsIGNvbG9yOiAndmFyKC0tdGV4dC1mYWludCknLCB3aGl0ZVNwYWNlOiAnbm93cmFwJyB9fT57ZmlsdGVyZWQubGVuZ3RofSDQvNOp0YA8L3NwYW4+XG4gICAgICAgIDxidXR0b25cbiAgICAgICAgICBjbGFzc05hbWU9XCJidG5cIlxuICAgICAgICAgIHN0eWxlPXt7IGZvbnRTaXplOiAxMiwgcGFkZGluZzogJzZweCAxMnB4JywgYmFja2dyb3VuZDogJ3ZhcigtLWJnLWVsZXZhdGVkKScsIGJvcmRlcjogJzFweCBzb2xpZCB2YXIoLS1ib3JkZXIpJywgY29sb3I6ICd2YXIoLS10ZXh0LXNlY29uZGFyeSknLCB3aGl0ZVNwYWNlOiAnbm93cmFwJyB9fVxuICAgICAgICAgIGRpc2FibGVkPXtmaWx0ZXJlZC5sZW5ndGggPT09IDB9XG4gICAgICAgICAgb25DbGljaz17ZG93bmxvYWRDc3Z9XG4gICAgICAgID5cbiAgICAgICAgICDirbMgQ1NWINGC0LDRgtCw0YVcbiAgICAgICAgPC9idXR0b24+XG4gICAgICA8L2Rpdj5cblxuICAgICAgPGRpdiBzdHlsZT17eyBmbGV4OiAxLCBvdmVyZmxvd1k6ICdhdXRvJywgYmFja2dyb3VuZDogJ3ZhcigtLWJnLXBhZ2UpJywgYm9yZGVyOiAnMXB4IHNvbGlkIHZhcigtLWJnLWVsZXZhdGVkKScsIGJvcmRlclJhZGl1czogOCwgcGFkZGluZzogJzEwcHggMTRweCcsIGZvbnRGYW1pbHk6ICdtb25vc3BhY2UnLCBmb250U2l6ZTogMTIsIGxpbmVIZWlnaHQ6IDEuNyB9fT5cbiAgICAgICAge2xvYWRpbmcgJiYgPGRpdiBzdHlsZT17eyBjb2xvcjogJ3ZhcigtLXRleHQtZmFpbnQpJyB9fT7QkNGH0LDQsNC70LvQsNC2INCx0LDQudC90LDigKY8L2Rpdj59XG4gICAgICAgIHshbG9hZGluZyAmJiBmaWx0ZXJlZC5sZW5ndGggPT09IDAgJiYgPGRpdiBzdHlsZT17eyBjb2xvcjogJ3ZhcigtLXRleHQtZmFpbnQpJyB9fT7Qm9C+0LMg0LHQsNC50YXQs9Kv0Lk8L2Rpdj59XG4gICAgICAgIHshbG9hZGluZyAmJiBmaWx0ZXJlZC5tYXAoKGxpbmUsIGkpID0+IChcbiAgICAgICAgICA8ZGl2IGtleT17aX0gc3R5bGU9e3sgYm9yZGVyQm90dG9tOiAnMXB4IHNvbGlkIHZhcigtLWJnLXBhbmVsKScsIHBhZGRpbmc6ICcxcHggMCcsIGNvbG9yOiB0YWdDb2xvcihsaW5lKSB9fT5cbiAgICAgICAgICAgIHtoaWdobGlnaHRMaW5lKGxpbmUpfVxuICAgICAgICAgIDwvZGl2PlxuICAgICAgICApKX1cbiAgICAgIDwvZGl2PlxuICAgIDwvZGl2PlxuICApO1xufVxuXG4vLyDilIDilIAgU2V0dGluZ3MgdGFiIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuZnVuY3Rpb24gU2V0dGluZ3NUYWIoeyB0b2tlbiwgY2FuRWRpdCB9OiB7IHRva2VuOiBzdHJpbmc7IGNhbkVkaXQ6IGJvb2xlYW4gfSkge1xuICBjb25zdCBbc2V0dGluZ3MsIHNldFNldHRpbmdzXSA9IHVzZVN0YXRlPFN5c1NldHRpbmdzPih7XG4gICAgbWF4RXZlbnRzOiA1MDAsIHJlY29ubmVjdEludGVydmFsOiAxNSxcbiAgICBzbXRwVXNlcjogJycsIHNtdHBQYXNzOiAnJywgc210cEZyb206ICcnLFxuICAgIGVtYWlsU3lzdGVtOiAnJywgZW1haWxPcGVyYXRpb25zOiAnJywgZW1haWxGaW5hbmNlOiAnJyxcbiAgfSk7XG4gIGNvbnN0IFtpbmZvLCBzZXRJbmZvXSA9IHVzZVN0YXRlPFN5c3RlbUluZm8gfCBudWxsPihudWxsKTtcbiAgY29uc3QgW2VtYWlsVXNlcnMsIHNldEVtYWlsVXNlcnNdID0gdXNlU3RhdGU8eyB1c2VybmFtZTogc3RyaW5nOyBlbWFpbDogc3RyaW5nIH1bXT4oW10pO1xuICBjb25zdCBbc2F2aW5nLCBzZXRTYXZpbmddID0gdXNlU3RhdGUoZmFsc2UpO1xuICBjb25zdCBbc2F2ZWQsIHNldFNhdmVkXSA9IHVzZVN0YXRlKGZhbHNlKTtcbiAgY29uc3QgW3Rlc3RpbmcsIHNldFRlc3RpbmddID0gdXNlU3RhdGUoZmFsc2UpO1xuICBjb25zdCBbdGVzdE1zZywgc2V0VGVzdE1zZ10gPSB1c2VTdGF0ZSgnJyk7XG4gIGNvbnN0IEggPSB7ICdDb250ZW50LVR5cGUnOiAnYXBwbGljYXRpb24vanNvbicsICdYLUF1dGgtVG9rZW4nOiB0b2tlbiB9O1xuXG4gIGFzeW5jIGZ1bmN0aW9uIHRlc3RFbWFpbCgpIHtcbiAgICBzZXRUZXN0aW5nKHRydWUpOyBzZXRUZXN0TXNnKCcnKTtcbiAgICB0cnkge1xuICAgICAgY29uc3QgciA9IGF3YWl0IGZldGNoKCcvYXBpL3NldHRpbmdzL3Rlc3QtZW1haWwnLCB7IG1ldGhvZDogJ1BPU1QnLCBoZWFkZXJzOiBILCBib2R5OiAne30nIH0pO1xuICAgICAgY29uc3QgZCA9IGF3YWl0IHIuanNvbigpO1xuICAgICAgc2V0VGVzdE1zZyhkLm9rID8gYOKckyDQotC10YHRgiDQuNC80Y3QudC7INC40LvQs9GN0Y3Qs9C00LvRjdGNIOKGkiAke2QudG99YCA6IGDinJcgJHtkLmVycm9yIHx8ICfQkNC70LTQsNCwJ31gKTtcbiAgICB9IGNhdGNoIHsgc2V0VGVzdE1zZygn4pyXINCh0LXRgNCy0LXRgNGC0Y3QuSDRhdC+0LvQsdC+0LPQtNC+0YXQs9Kv0LknKTsgfVxuICAgIGZpbmFsbHkgeyBzZXRUZXN0aW5nKGZhbHNlKTsgfVxuICB9XG5cbiAgdXNlRWZmZWN0KCgpID0+IHtcbiAgICBmZXRjaCgnL2FwaS9zZXR0aW5ncycsIHsgaGVhZGVyczogSCB9KS50aGVuKHIgPT4gci5vayA/IHIuanNvbigpIDogbnVsbCkudGhlbihkID0+IHsgaWYgKGQpIHNldFNldHRpbmdzKGQpOyB9KTtcbiAgICBmZXRjaCgnL2FwaS9zeXN0ZW0taW5mbycsIHsgaGVhZGVyczogSCB9KS50aGVuKHIgPT4gci5vayA/IHIuanNvbigpIDogbnVsbCkudGhlbihkID0+IHsgaWYgKGQpIHNldEluZm8oZCk7IH0pO1xuICAgIC8vINCY0LzRjdC50LvRgtGN0Lkg0LHSr9GA0YLQs9GN0LvRgtGN0Lkg0YXRjdGA0Y3Qs9C70Y3Qs9GH0LjQtCAo0YXQsNGA0LjRg9GG0LDQs9GHINGB0L7QvdCz0L7RhdC+0LQpXG4gICAgZmV0Y2goJy9hcGkvdXNlcnMnLCB7IGhlYWRlcnM6IEggfSkudGhlbihyID0+IHIub2sgPyByLmpzb24oKSA6IFtdKS50aGVuKChkOiB7IHVzZXJuYW1lOiBzdHJpbmc7IGVtYWlsPzogc3RyaW5nIH1bXSkgPT4ge1xuICAgICAgaWYgKEFycmF5LmlzQXJyYXkoZCkpIHNldEVtYWlsVXNlcnMoZC5maWx0ZXIodSA9PiB1LmVtYWlsKS5tYXAodSA9PiAoeyB1c2VybmFtZTogdS51c2VybmFtZSwgZW1haWw6IHUuZW1haWwgYXMgc3RyaW5nIH0pKSk7XG4gICAgfSkuY2F0Y2goKCkgPT4ge30pO1xuICAgIGNvbnN0IGl2ID0gc2V0SW50ZXJ2YWwoKCkgPT4ge1xuICAgICAgZmV0Y2goJy9hcGkvc3lzdGVtLWluZm8nLCB7IGhlYWRlcnM6IEggfSkudGhlbihyID0+IHIub2sgPyByLmpzb24oKSA6IG51bGwpLnRoZW4oZCA9PiB7IGlmIChkKSBzZXRJbmZvKGQpOyB9KTtcbiAgICB9LCA1MDAwKTtcbiAgICByZXR1cm4gKCkgPT4gY2xlYXJJbnRlcnZhbChpdik7XG4gIH0sIFtdKTtcblxuICBhc3luYyBmdW5jdGlvbiBzYXZlKGU6IFJlYWN0LkZvcm1FdmVudCkge1xuICAgIGUucHJldmVudERlZmF1bHQoKTtcbiAgICBzZXRTYXZpbmcodHJ1ZSk7XG4gICAgYXdhaXQgZmV0Y2goJy9hcGkvc2V0dGluZ3MnLCB7IG1ldGhvZDogJ1BVVCcsIGhlYWRlcnM6IEgsIGJvZHk6IEpTT04uc3RyaW5naWZ5KHNldHRpbmdzKSB9KTtcbiAgICBzZXRTYXZpbmcoZmFsc2UpO1xuICAgIHNldFNhdmVkKHRydWUpO1xuICAgIHNldFRpbWVvdXQoKCkgPT4gc2V0U2F2ZWQoZmFsc2UpLCAyNTAwKTtcbiAgfVxuXG4gIGNvbnN0IHJvdyA9IChsYWJlbDogc3RyaW5nLCB2YWx1ZTogUmVhY3QuUmVhY3ROb2RlKSA9PiAoXG4gICAgPGRpdiBzdHlsZT17eyBkaXNwbGF5OiAnZmxleCcsIGp1c3RpZnlDb250ZW50OiAnc3BhY2UtYmV0d2VlbicsIGFsaWduSXRlbXM6ICdjZW50ZXInLCBwYWRkaW5nOiAnOHB4IDAnLCBib3JkZXJCb3R0b206ICcxcHggc29saWQgdmFyKC0tYmctZWxldmF0ZWQpJyB9fT5cbiAgICAgIDxzcGFuIHN0eWxlPXt7IGZvbnRTaXplOiAxMywgY29sb3I6ICd2YXIoLS10ZXh0LW11dGVkKScgfX0+e2xhYmVsfTwvc3Bhbj5cbiAgICAgIDxzcGFuIHN0eWxlPXt7IGZvbnRTaXplOiAxMywgY29sb3I6ICd2YXIoLS10ZXh0LXNlY29uZGFyeSknLCBmb250RmFtaWx5OiAnbW9ub3NwYWNlJyB9fT57dmFsdWV9PC9zcGFuPlxuICAgIDwvZGl2PlxuICApO1xuXG4gIC8vIGlucHV0INGC0LDQu9Cx0LDRgCAoY29tcG9uZW50INCx0LjRiCDigJQg0YTQvtC60YPRgSDQsNC70LTQsNCz0LTQsNGF0LPSr9C5KVxuICBjb25zdCBpbnBTdHlsZTogUmVhY3QuQ1NTUHJvcGVydGllcyA9IHsgYmFja2dyb3VuZDogJ3ZhcigtLWJnLXBhbmVsKScsIGJvcmRlcjogJzFweCBzb2xpZCB2YXIoLS1ib3JkZXIpJywgYm9yZGVyUmFkaXVzOiA2LCBwYWRkaW5nOiAnNnB4IDEwcHgnLCBjb2xvcjogJ3ZhcigtLXRleHQtcHJpbWFyeSknLCBmb250U2l6ZTogMTMsIG91dGxpbmU6ICdub25lJywgd2lkdGg6ICcxMDAlJywgYm94U2l6aW5nOiAnYm9yZGVyLWJveCcgfTtcbiAgY29uc3QgZmllbGQgPSAobGFiZWw6IHN0cmluZywgdmFsdWU6IHN0cmluZywgb25DaGFuZ2U6ICh2OiBzdHJpbmcpID0+IHZvaWQsIHBsYWNlaG9sZGVyID0gJycsIHR5cGUgPSAndGV4dCcpID0+IChcbiAgICA8ZGl2IHN0eWxlPXt7IGRpc3BsYXk6ICdmbGV4JywgZmxleERpcmVjdGlvbjogJ2NvbHVtbicsIGdhcDogNCB9fT5cbiAgICAgIDxsYWJlbCBzdHlsZT17eyBmb250U2l6ZTogMTIsIGNvbG9yOiAndmFyKC0tdGV4dC1tdXRlZCknIH19PntsYWJlbH08L2xhYmVsPlxuICAgICAgPGlucHV0IHR5cGU9e3R5cGV9IHZhbHVlPXt2YWx1ZX0gb25DaGFuZ2U9e2UgPT4gb25DaGFuZ2UoZS50YXJnZXQudmFsdWUpfSBwbGFjZWhvbGRlcj17cGxhY2Vob2xkZXJ9IHN0eWxlPXtpbnBTdHlsZX0gLz5cbiAgICA8L2Rpdj5cbiAgKTtcbiAgLy8g0LjQvNGN0LnQuyDRgtCw0LvQsdCw0YAgKyDQsdKv0YDRgtCz0Y3Qu9GC0Y3QuSDRhdGN0YDRjdCz0LvRjdCz0YfRjdGN0YEg0YHQvtC90LPQvtGFIGRyb3Bkb3duXG4gIGNvbnN0IGVtYWlsRmllbGQgPSAobGFiZWw6IHN0cmluZywgdmFsdWU6IHN0cmluZywgb25DaGFuZ2U6ICh2OiBzdHJpbmcpID0+IHZvaWQsIHBsYWNlaG9sZGVyID0gJycpID0+IChcbiAgICA8ZGl2IHN0eWxlPXt7IGRpc3BsYXk6ICdmbGV4JywgZmxleERpcmVjdGlvbjogJ2NvbHVtbicsIGdhcDogNCB9fT5cbiAgICAgIDxsYWJlbCBzdHlsZT17eyBmb250U2l6ZTogMTIsIGNvbG9yOiAndmFyKC0tdGV4dC1tdXRlZCknIH19PntsYWJlbH08L2xhYmVsPlxuICAgICAgPGlucHV0IHR5cGU9XCJlbWFpbFwiIHZhbHVlPXt2YWx1ZX0gb25DaGFuZ2U9e2UgPT4gb25DaGFuZ2UoZS50YXJnZXQudmFsdWUpfSBwbGFjZWhvbGRlcj17cGxhY2Vob2xkZXJ9IHN0eWxlPXtpbnBTdHlsZX0gLz5cbiAgICAgIHtlbWFpbFVzZXJzLmxlbmd0aCA+IDAgJiYgKFxuICAgICAgICA8c2VsZWN0IHZhbHVlPVwiXCIgb25DaGFuZ2U9e2UgPT4geyBpZiAoZS50YXJnZXQudmFsdWUpIG9uQ2hhbmdlKGUudGFyZ2V0LnZhbHVlKTsgfX0gc3R5bGU9e3sgLi4uaW5wU3R5bGUsIGZvbnRTaXplOiAxMiwgY29sb3I6ICd2YXIoLS10ZXh0LW11dGVkKScgfX0+XG4gICAgICAgICAgPG9wdGlvbiB2YWx1ZT1cIlwiPuKAlCDQpdGN0YDRjdCz0LvRjdCz0YfRjdGN0YEg0YHQvtC90LPQvtGFIOKAlDwvb3B0aW9uPlxuICAgICAgICAgIHtlbWFpbFVzZXJzLm1hcCh1ID0+IDxvcHRpb24ga2V5PXt1LnVzZXJuYW1lfSB2YWx1ZT17dS5lbWFpbH0gc3R5bGU9e3sgY29sb3I6ICd2YXIoLS10ZXh0LXByaW1hcnkpJyB9fT57dS51c2VybmFtZX0g4oCUIHt1LmVtYWlsfTwvb3B0aW9uPil9XG4gICAgICAgIDwvc2VsZWN0PlxuICAgICAgKX1cbiAgICA8L2Rpdj5cbiAgKTtcblxuICByZXR1cm4gKFxuICAgIDxkaXYgc3R5bGU9e3sgZGlzcGxheTogJ2ZsZXgnLCBmbGV4RGlyZWN0aW9uOiAnY29sdW1uJywgZ2FwOiAyOCB9fT5cbiAgICAgIHsvKiBTeXN0ZW0gaW5mbyAqL31cbiAgICAgIHtpbmZvICYmIChcbiAgICAgICAgPGRpdj5cbiAgICAgICAgICA8ZGl2IHN0eWxlPXt7IGZvbnRTaXplOiAxMiwgZm9udFdlaWdodDogNjAwLCBjb2xvcjogJ3ZhcigtLWFjY2VudC1ibHVlKScsIG1hcmdpbkJvdHRvbTogMTAsIHRleHRUcmFuc2Zvcm06ICd1cHBlcmNhc2UnLCBsZXR0ZXJTcGFjaW5nOiAxIH19PtCh0LjRgdGC0LXQvCDQvNGN0LTRjdGN0LvRjdC7PC9kaXY+XG4gICAgICAgICAgPGRpdiBzdHlsZT17eyBiYWNrZ3JvdW5kOiAndmFyKC0tYmctcGFnZSknLCBib3JkZXI6ICcxcHggc29saWQgdmFyKC0tYmctZWxldmF0ZWQpJywgYm9yZGVyUmFkaXVzOiA4LCBwYWRkaW5nOiAnNHB4IDE2cHgnIH19PlxuICAgICAgICAgICAge3JvdygnVXB0aW1lJywgZm9ybWF0VXB0aW1lKGluZm8udXB0aW1lU2VjKSl9XG4gICAgICAgICAgICB7cm93KCfQpdC+0LvQsdC+0LPQtNGB0L7QvSDQutCw0LzQtdGAJywgYCR7aW5mby5jb25uZWN0ZWRDYW1lcmFzfSDRiGApfVxuICAgICAgICAgICAge3Jvdygn0KHQsNC90LDRhSDQvtC50L0g0LHSr9GA0YLQs9GN0LsnLCBgJHtpbmZvLmV2ZW50Q291bnR9IC8gJHtpbmZvLm1heEV2ZW50c31gKX1cbiAgICAgICAgICA8L2Rpdj5cbiAgICAgICAgPC9kaXY+XG4gICAgICApfVxuXG4gICAgICB7LyogU2V0dGluZ3MgZm9ybSAqL31cbiAgICAgIDxkaXY+XG4gICAgICAgIDxkaXYgc3R5bGU9e3sgZm9udFNpemU6IDEyLCBmb250V2VpZ2h0OiA2MDAsIGNvbG9yOiAndmFyKC0tYWNjZW50LWJsdWUpJywgbWFyZ2luQm90dG9tOiAxMCwgdGV4dFRyYW5zZm9ybTogJ3VwcGVyY2FzZScsIGxldHRlclNwYWNpbmc6IDEgfX0+0KLQvtGF0LjRgNCz0L7QvjwvZGl2PlxuICAgICAgICA8Zm9ybSBvblN1Ym1pdD17c2F2ZX0gc3R5bGU9e3sgYmFja2dyb3VuZDogJ3ZhcigtLWJnLXBhZ2UpJywgYm9yZGVyOiAnMXB4IHNvbGlkIHZhcigtLWJnLWVsZXZhdGVkKScsIGJvcmRlclJhZGl1czogOCwgcGFkZGluZzogMTYsIGRpc3BsYXk6ICdmbGV4JywgZmxleERpcmVjdGlvbjogJ2NvbHVtbicsIGdhcDogMTYgfX0+XG4gICAgICAgICAgPGRpdiBzdHlsZT17eyBkaXNwbGF5OiAnZmxleCcsIGZsZXhEaXJlY3Rpb246ICdjb2x1bW4nLCBnYXA6IDYgfX0+XG4gICAgICAgICAgICA8bGFiZWwgc3R5bGU9e3sgZm9udFNpemU6IDEzLCBjb2xvcjogJ3ZhcigtLXRleHQtbXV0ZWQpJyB9fT5FdmVudCDRhdCw0LTQs9Cw0LvQsNGFINC00Y3RjdC0INGC0L7QvjwvbGFiZWw+XG4gICAgICAgICAgICA8ZGl2IHN0eWxlPXt7IGRpc3BsYXk6ICdmbGV4JywgYWxpZ25JdGVtczogJ2NlbnRlcicsIGdhcDogMTAgfX0+XG4gICAgICAgICAgICAgIDxpbnB1dFxuICAgICAgICAgICAgICAgIHR5cGU9XCJudW1iZXJcIiBtaW49ezUwfSBtYXg9ezUwMDB9IHN0ZXA9ezUwfVxuICAgICAgICAgICAgICAgIHZhbHVlPXtzZXR0aW5ncy5tYXhFdmVudHN9XG4gICAgICAgICAgICAgICAgb25DaGFuZ2U9e2UgPT4gc2V0U2V0dGluZ3MocCA9PiAoeyAuLi5wLCBtYXhFdmVudHM6IE51bWJlcihlLnRhcmdldC52YWx1ZSkgfSkpfVxuICAgICAgICAgICAgICAgIHN0eWxlPXt7IHdpZHRoOiAxMDAsIGJhY2tncm91bmQ6ICd2YXIoLS1iZy1wYW5lbCknLCBib3JkZXI6ICcxcHggc29saWQgdmFyKC0tYm9yZGVyKScsIGJvcmRlclJhZGl1czogNiwgcGFkZGluZzogJzZweCAxMHB4JywgY29sb3I6ICd2YXIoLS10ZXh0LXByaW1hcnkpJywgZm9udFNpemU6IDEzLCBvdXRsaW5lOiAnbm9uZScgfX1cbiAgICAgICAgICAgICAgLz5cbiAgICAgICAgICAgICAgPHNwYW4gc3R5bGU9e3sgZm9udFNpemU6IDEyLCBjb2xvcjogJ3ZhcigtLXRleHQtZmFpbnQpJyB9fT7RgdCw0L3QsNGFINC+0LnQtCDRhdCw0LTQs9Cw0LvQsNGFICg1MOKAkzUwMDApPC9zcGFuPlxuICAgICAgICAgICAgPC9kaXY+XG4gICAgICAgICAgPC9kaXY+XG5cbiAgICAgICAgICA8ZGl2IHN0eWxlPXt7IGRpc3BsYXk6ICdmbGV4JywgZmxleERpcmVjdGlvbjogJ2NvbHVtbicsIGdhcDogNiB9fT5cbiAgICAgICAgICAgIDxsYWJlbCBzdHlsZT17eyBmb250U2l6ZTogMTMsIGNvbG9yOiAndmFyKC0tdGV4dC1tdXRlZCknIH19PtCl0L7Qu9Cx0L7Qu9GCINGC0LDRgdGA0LDRhdCw0LQg0LTQsNGF0LjQvSDRhdC+0LvQsdC+0YUg0YXRg9Cz0LDRhtCw0LA8L2xhYmVsPlxuICAgICAgICAgICAgPGRpdiBzdHlsZT17eyBkaXNwbGF5OiAnZmxleCcsIGFsaWduSXRlbXM6ICdjZW50ZXInLCBnYXA6IDEwIH19PlxuICAgICAgICAgICAgICA8aW5wdXRcbiAgICAgICAgICAgICAgICB0eXBlPVwibnVtYmVyXCIgbWluPXs1fSBtYXg9ezMwMH0gc3RlcD17NX1cbiAgICAgICAgICAgICAgICB2YWx1ZT17c2V0dGluZ3MucmVjb25uZWN0SW50ZXJ2YWx9XG4gICAgICAgICAgICAgICAgb25DaGFuZ2U9e2UgPT4gc2V0U2V0dGluZ3MocCA9PiAoeyAuLi5wLCByZWNvbm5lY3RJbnRlcnZhbDogTnVtYmVyKGUudGFyZ2V0LnZhbHVlKSB9KSl9XG4gICAgICAgICAgICAgICAgc3R5bGU9e3sgd2lkdGg6IDEwMCwgYmFja2dyb3VuZDogJ3ZhcigtLWJnLXBhbmVsKScsIGJvcmRlcjogJzFweCBzb2xpZCB2YXIoLS1ib3JkZXIpJywgYm9yZGVyUmFkaXVzOiA2LCBwYWRkaW5nOiAnNnB4IDEwcHgnLCBjb2xvcjogJ3ZhcigtLXRleHQtcHJpbWFyeSknLCBmb250U2l6ZTogMTMsIG91dGxpbmU6ICdub25lJyB9fVxuICAgICAgICAgICAgICAvPlxuICAgICAgICAgICAgICA8c3BhbiBzdHlsZT17eyBmb250U2l6ZTogMTIsIGNvbG9yOiAndmFyKC0tdGV4dC1mYWludCknIH19PtGB0LXQutGD0L3QtCAoNeKAkzMwMCk8L3NwYW4+XG4gICAgICAgICAgICA8L2Rpdj5cbiAgICAgICAgICA8L2Rpdj5cblxuICAgICAgICAgIHtjYW5FZGl0ICYmIChcbiAgICAgICAgICA8ZGl2IHN0eWxlPXt7IGRpc3BsYXk6ICdmbGV4JywgYWxpZ25JdGVtczogJ2NlbnRlcicsIGdhcDogMTIgfX0+XG4gICAgICAgICAgICA8YnV0dG9uXG4gICAgICAgICAgICAgIHR5cGU9XCJzdWJtaXRcIlxuICAgICAgICAgICAgICBkaXNhYmxlZD17c2F2aW5nfVxuICAgICAgICAgICAgICBzdHlsZT17eyBwYWRkaW5nOiAnN3B4IDIwcHgnLCBiYWNrZ3JvdW5kOiAndmFyKC0tYWNjZW50LWdyZWVuLXN0cm9uZyknLCBib3JkZXI6ICdub25lJywgYm9yZGVyUmFkaXVzOiA2LCBjb2xvcjogJ3ZhcigtLXRleHQtb24tYWNjZW50KScsIGZvbnRTaXplOiAxMywgY3Vyc29yOiAncG9pbnRlcicsIG9wYWNpdHk6IHNhdmluZyA/IDAuNiA6IDEgfX1cbiAgICAgICAgICAgID5cbiAgICAgICAgICAgICAge3NhdmluZyA/ICfQpdCw0LTQs9Cw0LvQtiDQsdCw0LnQvdCw4oCmJyA6ICfQpdCw0LTQs9Cw0LvQsNGFJ31cbiAgICAgICAgICAgIDwvYnV0dG9uPlxuICAgICAgICAgICAge3NhdmVkICYmIDxzcGFuIHN0eWxlPXt7IGZvbnRTaXplOiAxMiwgY29sb3I6ICd2YXIoLS1hY2NlbnQtZ3JlZW4pJyB9fT7inJMg0KXQsNC00LPQsNC70LDQs9C00LvQsNCwPC9zcGFuPn1cbiAgICAgICAgICA8L2Rpdj5cbiAgICAgICAgICApfVxuICAgICAgICA8L2Zvcm0+XG4gICAgICA8L2Rpdj5cblxuICAgICAgey8qINCY0LzRjdC50LsgKEdtYWlsIFNNVFApINGC0L7RhdC40YDQs9C+0L4gKi99XG4gICAgICA8ZGl2PlxuICAgICAgICA8ZGl2IHN0eWxlPXt7IGZvbnRTaXplOiAxMiwgZm9udFdlaWdodDogNjAwLCBjb2xvcjogJ3ZhcigtLWFjY2VudC1ibHVlKScsIG1hcmdpbkJvdHRvbTogMTAsIHRleHRUcmFuc2Zvcm06ICd1cHBlcmNhc2UnLCBsZXR0ZXJTcGFjaW5nOiAxIH19PtCY0LzRjdC50LsgKEdtYWlsIFNNVFApINGC0L7RhdC40YDQs9C+0L48L2Rpdj5cbiAgICAgICAgPGZvcm0gb25TdWJtaXQ9e3NhdmV9IHN0eWxlPXt7IGJhY2tncm91bmQ6ICd2YXIoLS1iZy1wYWdlKScsIGJvcmRlcjogJzFweCBzb2xpZCB2YXIoLS1iZy1lbGV2YXRlZCknLCBib3JkZXJSYWRpdXM6IDgsIHBhZGRpbmc6IDE2LCBkaXNwbGF5OiAnZmxleCcsIGZsZXhEaXJlY3Rpb246ICdjb2x1bW4nLCBnYXA6IDE0IH19PlxuICAgICAgICAgIDxkaXYgc3R5bGU9e3sgZGlzcGxheTogJ2dyaWQnLCBncmlkVGVtcGxhdGVDb2x1bW5zOiAnMWZyIDFmcicsIGdhcDogMTIgfX0+XG4gICAgICAgICAgICB7ZmllbGQoJ0dtYWlsINGF0LDRj9CzJywgc2V0dGluZ3Muc210cFVzZXIsIHYgPT4gc2V0U2V0dGluZ3MocCA9PiAoeyAuLi5wLCBzbXRwVXNlcjogdiB9KSksICdub3JlcGx5QGdtYWlsLmNvbScpfVxuICAgICAgICAgICAge2ZpZWxkKGBBcHAgUGFzc3dvcmQke3NldHRpbmdzLnNtdHBQYXNzU2V0ID8gJyAo0YXQsNC00LPQsNC70YHQsNC9KScgOiAnJ31gLCBzZXR0aW5ncy5zbXRwUGFzcywgdiA9PiBzZXRTZXR0aW5ncyhwID0+ICh7IC4uLnAsIHNtdHBQYXNzOiB2IH0pKSwgc2V0dGluZ3Muc210cFBhc3NTZXQgPyAn4oCi4oCi4oCi4oCiINOp06nRgNGH0LvTqdGF0LPSr9C5INCx0L7QuyDRhdC+0L7RgdC+0L0nIDogJzE2INC+0YDQvtC90YLQvtC5IEFwcCBQYXNzd29yZCcsICdwYXNzd29yZCcpfVxuICAgICAgICAgICAge2ZpZWxkKCdGcm9tINGF0LDRj9CzJywgc2V0dGluZ3Muc210cEZyb20sIHYgPT4gc2V0U2V0dGluZ3MocCA9PiAoeyAuLi5wLCBzbXRwRnJvbTogdiB9KSksICco0YXQvtC+0YHQvtC9INCx0L7QuyBHbWFpbCDRhdCw0Y/Qs9C40LnQsyDQsNGI0LjQs9C70LDQvdCwKScpfVxuICAgICAgICAgIDwvZGl2PlxuICAgICAgICAgIDxkaXYgc3R5bGU9e3sgYm9yZGVyVG9wOiAnMXB4IHNvbGlkIHZhcigtLWJnLWVsZXZhdGVkKScsIHBhZGRpbmdUb3A6IDEyLCBmb250U2l6ZTogMTIsIGNvbG9yOiAndmFyKC0tdGV4dC1tdXRlZCknIH19PtCl0LDRgNC40YPRhtCw0LPRhyDRhdGN0LvRgtGB0LjQudC9INGF0q/Qu9GN0Y3QvSDQsNCy0LDRhSDQuNC80Y3QudC7PC9kaXY+XG4gICAgICAgICAgPGRpdiBzdHlsZT17eyBkaXNwbGF5OiAnZ3JpZCcsIGdyaWRUZW1wbGF0ZUNvbHVtbnM6ICcxZnIgMWZyIDFmcicsIGdhcDogMTIgfX0+XG4gICAgICAgICAgICB7ZW1haWxGaWVsZCgn0KHQuNGB0YLQtdC8Jywgc2V0dGluZ3MuZW1haWxTeXN0ZW0sIHYgPT4gc2V0U2V0dGluZ3MocCA9PiAoeyAuLi5wLCBlbWFpbFN5c3RlbTogdiB9KSksICcuLi5AZWFzeS1wYXJraW5nLm1uJyl9XG4gICAgICAgICAgICB7ZW1haWxGaWVsZCgn0q7QudC7INCw0LbQuNC70LvQsNCz0LDQsCcsIHNldHRpbmdzLmVtYWlsT3BlcmF0aW9ucywgdiA9PiBzZXRTZXR0aW5ncyhwID0+ICh7IC4uLnAsIGVtYWlsT3BlcmF0aW9uczogdiB9KSksICcuLi5AZWFzeS1wYXJraW5nLm1uJyl9XG4gICAgICAgICAgICB7ZW1haWxGaWVsZCgn0KHQsNC90YXSr9KvJywgc2V0dGluZ3MuZW1haWxGaW5hbmNlLCB2ID0+IHNldFNldHRpbmdzKHAgPT4gKHsgLi4ucCwgZW1haWxGaW5hbmNlOiB2IH0pKSwgJyjQt9Cw0LDQstCw0Lsg0LHQuNGIKScpfVxuICAgICAgICAgIDwvZGl2PlxuICAgICAgICAgIHtjYW5FZGl0ICYmIChcbiAgICAgICAgICA8ZGl2IHN0eWxlPXt7IGRpc3BsYXk6ICdmbGV4JywgYWxpZ25JdGVtczogJ2NlbnRlcicsIGdhcDogMTIsIGZsZXhXcmFwOiAnd3JhcCcgfX0+XG4gICAgICAgICAgICA8YnV0dG9uIHR5cGU9XCJzdWJtaXRcIiBkaXNhYmxlZD17c2F2aW5nfSBzdHlsZT17eyBwYWRkaW5nOiAnN3B4IDIwcHgnLCBiYWNrZ3JvdW5kOiAndmFyKC0tYWNjZW50LWdyZWVuLXN0cm9uZyknLCBib3JkZXI6ICdub25lJywgYm9yZGVyUmFkaXVzOiA2LCBjb2xvcjogJ3ZhcigtLXRleHQtb24tYWNjZW50KScsIGZvbnRTaXplOiAxMywgY3Vyc29yOiAncG9pbnRlcicsIG9wYWNpdHk6IHNhdmluZyA/IDAuNiA6IDEgfX0+XG4gICAgICAgICAgICAgIHtzYXZpbmcgPyAn0KXQsNC00LPQsNC70LYg0LHQsNC50L3QsOKApicgOiAn0KXQsNC00LPQsNC70LDRhSd9XG4gICAgICAgICAgICA8L2J1dHRvbj5cbiAgICAgICAgICAgIDxidXR0b24gdHlwZT1cImJ1dHRvblwiIG9uQ2xpY2s9e3Rlc3RFbWFpbH0gZGlzYWJsZWQ9e3Rlc3Rpbmd9IHN0eWxlPXt7IHBhZGRpbmc6ICc3cHggMTZweCcsIGJhY2tncm91bmQ6ICd2YXIoLS1iZy1lbGV2YXRlZCknLCBib3JkZXI6ICcxcHggc29saWQgdmFyKC0tYm9yZGVyKScsIGJvcmRlclJhZGl1czogNiwgY29sb3I6ICd2YXIoLS10ZXh0LXNlY29uZGFyeSknLCBmb250U2l6ZTogMTMsIGN1cnNvcjogJ3BvaW50ZXInIH19PlxuICAgICAgICAgICAgICB7dGVzdGluZyA/ICfQmNC70LPRjdGN0LYg0LHQsNC50L3QsOKApicgOiAn0KLQtdGB0YIg0LjQvNGN0LnQuyd9XG4gICAgICAgICAgICA8L2J1dHRvbj5cbiAgICAgICAgICAgIHt0ZXN0TXNnICYmIDxzcGFuIHN0eWxlPXt7IGZvbnRTaXplOiAxMiwgY29sb3I6IHRlc3RNc2cuc3RhcnRzV2l0aCgn4pyTJykgPyAndmFyKC0tYWNjZW50LWdyZWVuKScgOiAndmFyKC0tYWNjZW50LXJlZCknIH19Pnt0ZXN0TXNnfTwvc3Bhbj59XG4gICAgICAgICAgPC9kaXY+XG4gICAgICAgICAgKX1cbiAgICAgICAgPC9mb3JtPlxuICAgICAgPC9kaXY+XG4gICAgPC9kaXY+XG4gICk7XG59XG5cblxuLy8g4pSA4pSAIEFkbWluUGFuZWwg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5leHBvcnQgZGVmYXVsdCBmdW5jdGlvbiBBZG1pblBhbmVsKHsgdG9rZW4sIHJvbGUsIHBlcm1pc3Npb25zLCBvbkNsb3NlIH06IFByb3BzKSB7XG4gIC8vINCt0YDRhdC40LnQvSDRiNCw0LvQs9Cw0LvRgiAoYmFja2VuZCBjYW4oKS3RgtGN0Lkg0LjQttC40Lsg0LvQvtCz0LjQuilcbiAgY29uc3QgY2FwID0gKG1lbnU6IFRhYiwgYWN0aW9uOiAndmlldycgfCAnZWRpdCcgfCAnZGVsZXRlJyB8ICdjcmVhdGUnKTogYm9vbGVhbiA9PiB7XG4gICAgaWYgKHJvbGUgPT09ICdhZG1pbicpIHJldHVybiB0cnVlO1xuICAgIGlmIChyb2xlID09PSAnb3BlcmF0b3InKSB7XG4gICAgICBpZiAobWVudSA9PT0gJ2xvZ3MnKSByZXR1cm4gYWN0aW9uID09PSAndmlldyc7XG4gICAgICByZXR1cm4gZmFsc2U7XG4gICAgfVxuICAgIGlmIChyb2xlID09PSAnbWFuYWdlcicpIHJldHVybiAhIXBlcm1pc3Npb25zPy5bbWVudV0/LlthY3Rpb25dO1xuICAgIHJldHVybiBmYWxzZTtcbiAgfTtcbiAgY29uc3QgdmlzaWJsZVRhYnMgPSAoT2JqZWN0LmtleXMoVEFCX0xBQkVMUykgYXMgVGFiW10pLmZpbHRlcih0ID0+IGNhcCh0LCAndmlldycpKTtcbiAgY29uc3QgW3RhYiwgc2V0VGFiXSA9IHVzZVN0YXRlPFRhYj4odmlzaWJsZVRhYnNbMF0gPz8gJ3BhcmtpbmcnKTtcblxuICByZXR1cm4gKFxuICAgIDxkaXYgY2xhc3NOYW1lPVwiYWRtaW4tcGFnZVwiPlxuICAgICAgPGRpdiBjbGFzc05hbWU9XCJhZG1pbi1wYW5lbC1oZWFkZXJcIj5cbiAgICAgICAgPGRpdiBjbGFzc05hbWU9XCJhZG1pbi1wYW5lbC10YWJzXCI+XG4gICAgICAgICAge3Zpc2libGVUYWJzLm1hcCh0ID0+IChcbiAgICAgICAgICAgIDxidXR0b25cbiAgICAgICAgICAgICAga2V5PXt0fVxuICAgICAgICAgICAgICBjbGFzc05hbWU9e2BhZG1pbi10YWItYnRuJHt0YWIgPT09IHQgPyAnIGFjdGl2ZScgOiAnJ31gfVxuICAgICAgICAgICAgICBvbkNsaWNrPXsoKSA9PiBzZXRUYWIodCl9XG4gICAgICAgICAgICA+XG4gICAgICAgICAgICAgIHtUQUJfTEFCRUxTW3RdfVxuICAgICAgICAgICAgPC9idXR0b24+XG4gICAgICAgICAgKSl9XG4gICAgICAgIDwvZGl2PlxuICAgICAgPC9kaXY+XG5cbiAgICAgIDxkaXYgY2xhc3NOYW1lPVwiYWRtaW4tcGFuZWwtYm9keVwiPlxuICAgICAgICB7dGFiID09PSAncGFya2luZycgICAgJiYgY2FwKCdwYXJraW5nJywgJ3ZpZXcnKSAgICAmJiA8UGFya2luZ01hbmFnZW1lbnQgdG9rZW49e3Rva2VufSBvbkNsb3NlPXtvbkNsb3NlfSBlbWJlZGRlZCBjYW5FZGl0PXtjYXAoJ3BhcmtpbmcnLCAnZWRpdCcpfSBjYW5EZWxldGU9e2NhcCgncGFya2luZycsICdkZWxldGUnKX0gLz59XG4gICAgICAgIHt0YWIgPT09ICd1c2VycycgICAgICAmJiBjYXAoJ3VzZXJzJywgJ3ZpZXcnKSAgICAgICYmIDxVc2VyTWFuYWdlbWVudCAgICB0b2tlbj17dG9rZW59IG9uQ2xvc2U9e29uQ2xvc2V9IGVtYmVkZGVkIGNhbkVkaXQ9e2NhcCgndXNlcnMnLCAnZWRpdCcpfSBjYW5EZWxldGU9e2NhcCgndXNlcnMnLCAnZGVsZXRlJyl9IC8+fVxuICAgICAgICB7dGFiID09PSAncmVhc29ucycgICAgJiYgY2FwKCdyZWFzb25zJywgJ3ZpZXcnKSAgICAmJiA8UmVhc29uc1RhYiAgdG9rZW49e3Rva2VufSBjYW5FZGl0PXtjYXAoJ3JlYXNvbnMnLCAnZWRpdCcpfSBjYW5EZWxldGU9e2NhcCgncmVhc29ucycsICdkZWxldGUnKX0gLz59XG4gICAgICAgIHt0YWIgPT09ICdzZXR0aW5ncycgICAmJiBjYXAoJ3NldHRpbmdzJywgJ3ZpZXcnKSAgICYmIDxTZXR0aW5nc1RhYiB0b2tlbj17dG9rZW59IGNhbkVkaXQ9e2NhcCgnc2V0dGluZ3MnLCAnZWRpdCcpfSAvPn1cbiAgICAgICAge3RhYiA9PT0gJ2xvZ3MnICAgICAgICYmIGNhcCgnbG9ncycsICd2aWV3JykgICAgICAgJiYgPExvZ3NUYWIgICAgIHRva2VuPXt0b2tlbn0gLz59XG4gICAgICAgIHt0YWIgPT09ICd0dW5uZWxzJyAgICAmJiBjYXAoJ3R1bm5lbHMnLCAndmlldycpICAgICYmIDxUdW5uZWxzUGFnZSB0b2tlbj17dG9rZW59IC8+fVxuICAgICAgPC9kaXY+XG4gICAgPC9kaXY+XG4gICk7XG59XG4iXX0=