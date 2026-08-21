import { createHotContext as __vite__createHotContext } from "/@vite/client";import.meta.hot = __vite__createHotContext("/src/components/ParkingSelector.tsx");const useEffect = __vite__cjsImport0_react["useEffect"]; const useState = __vite__cjsImport0_react["useState"];const _jsxDEV = __vite__cjsImport1_react_jsxDevRuntime["jsxDEV"];import __vite__cjsImport0_react from "/node_modules/.vite/deps/react.js?v=7077b528";
var _jsxFileName = "/home/anpruser/anpr-app/src/components/ParkingSelector.tsx";
import __vite__cjsImport1_react_jsxDevRuntime from "/node_modules/.vite/deps/react_jsx-dev-runtime.js?v=7077b528";
var _s = $RefreshSig$();
const DIR_ARROW = {
	enter: "↑",
	exit: "↓"
};
export default function ParkingSelector({ token, selectedLotId, onSelect, selectedCamId, onSelectCamera }) {
	_s();
	const [lots, setLots] = useState([]);
	const [cameras, setCameras] = useState([]);
	const [loading, setLoading] = useState(true);
	async function load() {
		try {
			const r = await fetch("/api/parking", { headers: { "X-Auth-Token": token } });
			if (!r.ok) return;
			const d = await r.json();
			setLots(d.lots ?? []);
			setCameras(d.cameras ?? []);
		} finally {
			setLoading(false);
		}
	}
	useEffect(() => {
		load();
	}, []);
	if (loading) return null;
	if (lots.length === 0) return /* @__PURE__ */ _jsxDEV("div", {
		className: "parking-selector-empty",
		children: "Зогсоол бүртгэгдээгүй байна — \"Зогсоол\" товчоор нэмнэ үү"
	}, void 0, false, {
		fileName: _jsxFileName,
		lineNumber: 35,
		columnNumber: 5
	}, this);
	const lotCams = cameras.filter((c) => c.parking_lot_id === selectedLotId);
	function handleCamClick(cam) {
		if (selectedCamId === cam.id) {
			onSelectCamera(null);
		} else {
			onSelectCamera(cam.id);
		}
	}
	return /* @__PURE__ */ _jsxDEV("div", {
		className: "parking-selector",
		children: [
			/* @__PURE__ */ _jsxDEV("div", {
				className: "parking-selector-label",
				children: "Зогсоол"
			}, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 52,
				columnNumber: 7
			}, this),
			/* @__PURE__ */ _jsxDEV("select", {
				className: "parking-select",
				value: selectedLotId ?? "",
				onChange: (e) => {
					onSelect(e.target.value ? Number(e.target.value) : null);
					onSelectCamera(null);
				},
				children: [/* @__PURE__ */ _jsxDEV("option", {
					value: "",
					children: "— Бүгд —"
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 59,
					columnNumber: 9
				}, this), lots.map((l) => /* @__PURE__ */ _jsxDEV("option", {
					value: l.id,
					children: [l.name, l.location ? ` · ${l.location}` : ""]
				}, l.id, true, {
					fileName: _jsxFileName,
					lineNumber: 61,
					columnNumber: 11
				}, this))]
			}, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 54,
				columnNumber: 7
			}, this),
			selectedLotId && /* @__PURE__ */ _jsxDEV("div", {
				className: "parking-cam-badges",
				children: [lotCams.length === 0 && /* @__PURE__ */ _jsxDEV("span", {
					style: {
						fontSize: 12,
						color: "var(--text-faint)"
					},
					children: "Камер байхгүй"
				}, void 0, false, {
					fileName: _jsxFileName,
					lineNumber: 69,
					columnNumber: 13
				}, this), ["enter", "exit"].flatMap((dir) => cameras.filter((c) => c.parking_lot_id === selectedLotId && c.direction === dir).map((cam) => {
					const active = selectedCamId === cam.id;
					return /* @__PURE__ */ _jsxDEV("button", {
						className: `cam-badge ${dir}${active ? " active" : ""}`,
						onClick: () => handleCamClick(cam),
						title: `${cam.ip}:${cam.port} — дарж stream харах`,
						children: [
							/* @__PURE__ */ _jsxDEV("span", { className: "cam-badge-dot" }, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 82,
								columnNumber: 21
							}, this),
							/* @__PURE__ */ _jsxDEV("span", {
								style: {
									opacity: .6,
									fontSize: 11
								},
								children: DIR_ARROW[dir]
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 83,
								columnNumber: 21
							}, this),
							cam.label || cam.ip,
							active && /* @__PURE__ */ _jsxDEV("span", {
								className: "cam-badge-live",
								children: "LIVE"
							}, void 0, false, {
								fileName: _jsxFileName,
								lineNumber: 85,
								columnNumber: 32
							}, this)
						]
					}, cam.id, true, {
						fileName: _jsxFileName,
						lineNumber: 76,
						columnNumber: 19
					}, this);
				}))]
			}, void 0, true, {
				fileName: _jsxFileName,
				lineNumber: 67,
				columnNumber: 9
			}, this)
		]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 51,
		columnNumber: 5
	}, this);
}
_s(ParkingSelector, "mXgeh4E5Qw+hTlunYz+S/FXLr1U=");
_c = ParkingSelector;
var _c;
$RefreshReg$(_c, "ParkingSelector");
import * as RefreshRuntime from "/@react-refresh";
const inWebWorker = typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope;
import * as __vite_react_currentExports from "/src/components/ParkingSelector.tsx";
if (import.meta.hot && !inWebWorker) {
  if (!window.$RefreshReg$) {
    throw new Error(
      "@vitejs/plugin-react can't detect preamble. Something is wrong."
    );
  }

  const currentExports = __vite_react_currentExports;
  queueMicrotask(() => {
    RefreshRuntime.registerExportsForReactRefresh("/home/anpruser/anpr-app/src/components/ParkingSelector.tsx", currentExports);
    import.meta.hot.accept((nextExports) => {
      if (!nextExports) return;
      const invalidateMessage = RefreshRuntime.validateRefreshBoundaryAndEnqueueUpdate("/home/anpruser/anpr-app/src/components/ParkingSelector.tsx", currentExports, nextExports);
      if (invalidateMessage) import.meta.hot.invalidate(invalidateMessage);
    });
  });
}
function $RefreshReg$(type, id) { return RefreshRuntime.register(type, "/home/anpruser/anpr-app/src/components/ParkingSelector.tsx" + ' ' + id); }
function $RefreshSig$() { return RefreshRuntime.createSignatureFunctionForTransform(); }

//# sourceMappingURL=data:application/json;base64,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Im5hbWVzIjpbXSwic291cmNlcyI6WyJQYXJraW5nU2VsZWN0b3IudHN4Il0sInZlcnNpb24iOjMsInNvdXJjZXNDb250ZW50IjpbImltcG9ydCB7IHVzZUVmZmVjdCwgdXNlU3RhdGUgfSBmcm9tICdyZWFjdCc7XG5cbmludGVyZmFjZSBQYXJraW5nTG90ICAgIHsgaWQ6IG51bWJlcjsgbmFtZTogc3RyaW5nOyBsb2NhdGlvbjogc3RyaW5nIH1cbmludGVyZmFjZSBQYXJraW5nQ2FtZXJhIHsgaWQ6IG51bWJlcjsgcGFya2luZ19sb3RfaWQ6IG51bWJlcjsgbGFiZWw6IHN0cmluZzsgaXA6IHN0cmluZzsgcG9ydDogbnVtYmVyOyBkaXJlY3Rpb246ICdlbnRlcicgfCAnZXhpdCcgfVxuXG5pbnRlcmZhY2UgUHJvcHMge1xuICB0b2tlbjogc3RyaW5nO1xuICBzZWxlY3RlZExvdElkOiBudW1iZXIgfCBudWxsO1xuICBvblNlbGVjdDogKGlkOiBudW1iZXIgfCBudWxsKSA9PiB2b2lkO1xuICBzZWxlY3RlZENhbUlkOiBudW1iZXIgfCBudWxsO1xuICBvblNlbGVjdENhbWVyYTogKGNhbUlkOiBudW1iZXIgfCBudWxsKSA9PiB2b2lkO1xufVxuXG5jb25zdCBESVJfQVJST1cgPSB7IGVudGVyOiAn4oaRJywgZXhpdDogJ+KGkycgfTtcblxuZXhwb3J0IGRlZmF1bHQgZnVuY3Rpb24gUGFya2luZ1NlbGVjdG9yKHsgdG9rZW4sIHNlbGVjdGVkTG90SWQsIG9uU2VsZWN0LCBzZWxlY3RlZENhbUlkLCBvblNlbGVjdENhbWVyYSB9OiBQcm9wcykge1xuICBjb25zdCBbbG90cywgc2V0TG90c10gICAgICAgPSB1c2VTdGF0ZTxQYXJraW5nTG90W10+KFtdKTtcbiAgY29uc3QgW2NhbWVyYXMsIHNldENhbWVyYXNdID0gdXNlU3RhdGU8UGFya2luZ0NhbWVyYVtdPihbXSk7XG4gIGNvbnN0IFtsb2FkaW5nLCBzZXRMb2FkaW5nXSA9IHVzZVN0YXRlKHRydWUpO1xuXG4gIGFzeW5jIGZ1bmN0aW9uIGxvYWQoKSB7XG4gICAgdHJ5IHtcbiAgICAgIGNvbnN0IHIgPSBhd2FpdCBmZXRjaCgnL2FwaS9wYXJraW5nJywgeyBoZWFkZXJzOiB7ICdYLUF1dGgtVG9rZW4nOiB0b2tlbiB9IH0pO1xuICAgICAgaWYgKCFyLm9rKSByZXR1cm47XG4gICAgICBjb25zdCBkID0gYXdhaXQgci5qc29uKCk7XG4gICAgICBzZXRMb3RzKGQubG90cyA/PyBbXSk7XG4gICAgICBzZXRDYW1lcmFzKGQuY2FtZXJhcyA/PyBbXSk7XG4gICAgfSBmaW5hbGx5IHsgc2V0TG9hZGluZyhmYWxzZSk7IH1cbiAgfVxuXG4gIHVzZUVmZmVjdCgoKSA9PiB7IGxvYWQoKTsgfSwgW10pO1xuXG4gIGlmIChsb2FkaW5nKSByZXR1cm4gbnVsbDtcbiAgaWYgKGxvdHMubGVuZ3RoID09PSAwKSByZXR1cm4gKFxuICAgIDxkaXYgY2xhc3NOYW1lPVwicGFya2luZy1zZWxlY3Rvci1lbXB0eVwiPlxuICAgICAg0JfQvtCz0YHQvtC+0Lsg0LHSr9GA0YLQs9GN0LPQtNGN0Y3Qs9Kv0Lkg0LHQsNC50L3QsCDigJQgXCLQl9C+0LPRgdC+0L7Qu1wiINGC0L7QstGH0L7QvtGAINC90Y3QvNC90Y0g0q/Sr1xuICAgIDwvZGl2PlxuICApO1xuXG4gIGNvbnN0IGxvdENhbXMgID0gY2FtZXJhcy5maWx0ZXIoYyA9PiBjLnBhcmtpbmdfbG90X2lkID09PSBzZWxlY3RlZExvdElkKTtcblxuICBmdW5jdGlvbiBoYW5kbGVDYW1DbGljayhjYW06IFBhcmtpbmdDYW1lcmEpIHtcbiAgICBpZiAoc2VsZWN0ZWRDYW1JZCA9PT0gY2FtLmlkKSB7XG4gICAgICBvblNlbGVjdENhbWVyYShudWxsKTsgICAvLyDQtNCw0YXQuNC9INC00LDRgNCy0LDQuyBzdHJlYW0g0YPQvdGC0YDQsNCw0L3QsFxuICAgIH0gZWxzZSB7XG4gICAgICBvblNlbGVjdENhbWVyYShjYW0uaWQpO1xuICAgIH1cbiAgfVxuXG4gIHJldHVybiAoXG4gICAgPGRpdiBjbGFzc05hbWU9XCJwYXJraW5nLXNlbGVjdG9yXCI+XG4gICAgICA8ZGl2IGNsYXNzTmFtZT1cInBhcmtpbmctc2VsZWN0b3ItbGFiZWxcIj7Ql9C+0LPRgdC+0L7QuzwvZGl2PlxuXG4gICAgICA8c2VsZWN0XG4gICAgICAgIGNsYXNzTmFtZT1cInBhcmtpbmctc2VsZWN0XCJcbiAgICAgICAgdmFsdWU9e3NlbGVjdGVkTG90SWQgPz8gJyd9XG4gICAgICAgIG9uQ2hhbmdlPXtlID0+IHsgb25TZWxlY3QoZS50YXJnZXQudmFsdWUgPyBOdW1iZXIoZS50YXJnZXQudmFsdWUpIDogbnVsbCk7IG9uU2VsZWN0Q2FtZXJhKG51bGwpOyB9fVxuICAgICAgPlxuICAgICAgICA8b3B0aW9uIHZhbHVlPVwiXCI+4oCUINCR0q/Qs9C0IOKAlDwvb3B0aW9uPlxuICAgICAgICB7bG90cy5tYXAobCA9PiAoXG4gICAgICAgICAgPG9wdGlvbiBrZXk9e2wuaWR9IHZhbHVlPXtsLmlkfT57bC5uYW1lfXtsLmxvY2F0aW9uID8gYCDCtyAke2wubG9jYXRpb259YCA6ICcnfTwvb3B0aW9uPlxuICAgICAgICApKX1cbiAgICAgIDwvc2VsZWN0PlxuXG4gICAgICB7LyogQ2FtZXJhIGJhZGdlcyDigJQgY2xpY2thYmxlICovfVxuICAgICAge3NlbGVjdGVkTG90SWQgJiYgKFxuICAgICAgICA8ZGl2IGNsYXNzTmFtZT1cInBhcmtpbmctY2FtLWJhZGdlc1wiPlxuICAgICAgICAgIHtsb3RDYW1zLmxlbmd0aCA9PT0gMCAmJiAoXG4gICAgICAgICAgICA8c3BhbiBzdHlsZT17eyBmb250U2l6ZTogMTIsIGNvbG9yOiAndmFyKC0tdGV4dC1mYWludCknIH19PtCa0LDQvNC10YAg0LHQsNC50YXQs9Kv0Lk8L3NwYW4+XG4gICAgICAgICAgKX1cbiAgICAgICAgICB7KFsnZW50ZXInLCAnZXhpdCddIGFzIGNvbnN0KS5mbGF0TWFwKGRpciA9PlxuICAgICAgICAgICAgY2FtZXJhcy5maWx0ZXIoYyA9PiBjLnBhcmtpbmdfbG90X2lkID09PSBzZWxlY3RlZExvdElkICYmIGMuZGlyZWN0aW9uID09PSBkaXIpXG4gICAgICAgICAgICAgIC5tYXAoY2FtID0+IHtcbiAgICAgICAgICAgICAgICBjb25zdCBhY3RpdmUgPSBzZWxlY3RlZENhbUlkID09PSBjYW0uaWQ7XG4gICAgICAgICAgICAgICAgcmV0dXJuIChcbiAgICAgICAgICAgICAgICAgIDxidXR0b25cbiAgICAgICAgICAgICAgICAgICAga2V5PXtjYW0uaWR9XG4gICAgICAgICAgICAgICAgICAgIGNsYXNzTmFtZT17YGNhbS1iYWRnZSAke2Rpcn0ke2FjdGl2ZSA/ICcgYWN0aXZlJyA6ICcnfWB9XG4gICAgICAgICAgICAgICAgICAgIG9uQ2xpY2s9eygpID0+IGhhbmRsZUNhbUNsaWNrKGNhbSl9XG4gICAgICAgICAgICAgICAgICAgIHRpdGxlPXtgJHtjYW0uaXB9OiR7Y2FtLnBvcnR9IOKAlCDQtNCw0YDQtiBzdHJlYW0g0YXQsNGA0LDRhWB9XG4gICAgICAgICAgICAgICAgICA+XG4gICAgICAgICAgICAgICAgICAgIDxzcGFuIGNsYXNzTmFtZT1cImNhbS1iYWRnZS1kb3RcIiAvPlxuICAgICAgICAgICAgICAgICAgICA8c3BhbiBzdHlsZT17eyBvcGFjaXR5OiAwLjYsIGZvbnRTaXplOiAxMSB9fT57RElSX0FSUk9XW2Rpcl19PC9zcGFuPlxuICAgICAgICAgICAgICAgICAgICB7Y2FtLmxhYmVsIHx8IGNhbS5pcH1cbiAgICAgICAgICAgICAgICAgICAge2FjdGl2ZSAmJiA8c3BhbiBjbGFzc05hbWU9XCJjYW0tYmFkZ2UtbGl2ZVwiPkxJVkU8L3NwYW4+fVxuICAgICAgICAgICAgICAgICAgPC9idXR0b24+XG4gICAgICAgICAgICAgICAgKTtcbiAgICAgICAgICAgICAgfSlcbiAgICAgICAgICApfVxuICAgICAgICA8L2Rpdj5cbiAgICAgICl9XG4gICAgPC9kaXY+XG4gICk7XG59XG4iXX0=