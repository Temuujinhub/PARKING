import { createHotContext as __vite__createHotContext } from "/@vite/client";import.meta.hot = __vite__createHotContext("/src/components/CameraStatus.tsx");const useEffect = __vite__cjsImport0_react["useEffect"]; const useState = __vite__cjsImport0_react["useState"];const _jsxDEV = __vite__cjsImport1_react_jsxDevRuntime["jsxDEV"];import __vite__cjsImport0_react from "/node_modules/.vite/deps/react.js?v=7077b528";
var _jsxFileName = "/home/anpruser/anpr-app/src/components/CameraStatus.tsx";
import __vite__cjsImport1_react_jsxDevRuntime from "/node_modules/.vite/deps/react_jsx-dev-runtime.js?v=7077b528";
var _s = $RefreshSig$();
// Menubar item — камерын онлайн/нийт тоог харуулж, дарвал бүтэн хуудас нээнэ
export default function CameraStatus({ token, onOpen, active }) {
	_s();
	const [cams, setCams] = useState([]);
	async function load() {
		try {
			const r = await fetch("/api/camera-status", { headers: { "X-Auth-Token": token } });
			if (r.ok) setCams(await r.json());
		} catch {}
	}
	useEffect(() => {
		load();
		const iv = setInterval(load, 1e4);
		return () => clearInterval(iv);
	}, []);
	const online = cams.filter((c) => c.status === "online").length;
	const total = cams.length;
	const dotColor = total === 0 ? "var(--text-muted)" : online === total ? "var(--accent-green)" : online === 0 ? "var(--accent-red)" : "var(--accent-orange)";
	return /* @__PURE__ */ _jsxDEV("button", {
		className: `menu-item${active ? " active" : ""}`,
		onClick: onOpen,
		title: "Камерын төлөв",
		style: {
			display: "inline-flex",
			alignItems: "center",
			gap: 6
		},
		children: [
			/* @__PURE__ */ _jsxDEV("span", { style: {
				display: "inline-block",
				width: 8,
				height: 8,
				borderRadius: "50%",
				background: dotColor
			} }, void 0, false, {
				fileName: _jsxFileName,
				lineNumber: 23,
				columnNumber: 7
			}, this),
			"Камер ",
			online,
			"/",
			total
		]
	}, void 0, true, {
		fileName: _jsxFileName,
		lineNumber: 22,
		columnNumber: 5
	}, this);
}
_s(CameraStatus, "tiFQFXAwrzaH3mhp7mC6JI3eQpQ=");
_c = CameraStatus;
var _c;
$RefreshReg$(_c, "CameraStatus");
import * as RefreshRuntime from "/@react-refresh";
const inWebWorker = typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope;
import * as __vite_react_currentExports from "/src/components/CameraStatus.tsx";
if (import.meta.hot && !inWebWorker) {
  if (!window.$RefreshReg$) {
    throw new Error(
      "@vitejs/plugin-react can't detect preamble. Something is wrong."
    );
  }

  const currentExports = __vite_react_currentExports;
  queueMicrotask(() => {
    RefreshRuntime.registerExportsForReactRefresh("/home/anpruser/anpr-app/src/components/CameraStatus.tsx", currentExports);
    import.meta.hot.accept((nextExports) => {
      if (!nextExports) return;
      const invalidateMessage = RefreshRuntime.validateRefreshBoundaryAndEnqueueUpdate("/home/anpruser/anpr-app/src/components/CameraStatus.tsx", currentExports, nextExports);
      if (invalidateMessage) import.meta.hot.invalidate(invalidateMessage);
    });
  });
}
function $RefreshReg$(type, id) { return RefreshRuntime.register(type, "/home/anpruser/anpr-app/src/components/CameraStatus.tsx" + ' ' + id); }
function $RefreshSig$() { return RefreshRuntime.createSignatureFunctionForTransform(); }

//# sourceMappingURL=data:application/json;base64,eyJtYXBwaW5ncyI6IkFBQUEsU0FBUyxXQUFXLGdCQUFnQjs7Ozs7QUFLcEMsZUFBZSxTQUFTLGFBQWEsRUFBRSxPQUFPLFFBQVEsVUFBbUU7O0NBQ3ZILE1BQU0sQ0FBQyxNQUFNLFdBQVcsU0FBb0IsQ0FBQyxDQUFDO0NBRTlDLGVBQWUsT0FBTztFQUNwQixJQUFJO0dBQ0YsTUFBTSxJQUFJLE1BQU0sTUFBTSxzQkFBc0IsRUFBRSxTQUFTLEVBQUUsZ0JBQWdCLE1BQU0sRUFBRSxDQUFDO0dBQ2xGLElBQUksRUFBRSxJQUFJLFFBQVEsTUFBTSxFQUFFLEtBQUssQ0FBQztFQUNsQyxRQUFRLENBQWU7Q0FDekI7Q0FDQSxnQkFBZ0I7RUFBRSxLQUFLO0VBQUcsTUFBTSxLQUFLLFlBQVksTUFBTSxHQUFLO0VBQUcsYUFBYSxjQUFjLEVBQUU7Q0FBRyxHQUFHLENBQUMsQ0FBQztDQUVwRyxNQUFNLFNBQVMsS0FBSyxRQUFPLE1BQUssRUFBRSxXQUFXLFFBQVEsRUFBRTtDQUN2RCxNQUFNLFFBQVEsS0FBSztDQUNuQixNQUFNLFdBQVcsVUFBVSxJQUFJLHNCQUFzQixXQUFXLFFBQVEsd0JBQXdCLFdBQVcsSUFBSSxzQkFBc0I7Q0FFckksT0FDRSx3QkFBQyxVQUFEO0VBQVEsV0FBVyxZQUFZLFNBQVMsWUFBWTtFQUFNLFNBQVM7RUFBUSxPQUFNO0VBQWdCLE9BQU87R0FBRSxTQUFTO0dBQWUsWUFBWTtHQUFVLEtBQUs7RUFBRTtZQUEvSjtHQUNFLHdCQUFDLFFBQUQsRUFBTSxPQUFPO0lBQUUsU0FBUztJQUFnQixPQUFPO0lBQUcsUUFBUTtJQUFHLGNBQWM7SUFBTyxZQUFZO0dBQVMsRUFBSTs7Ozs7R0FBQztHQUNyRztHQUFPO0dBQUU7RUFDVjs7Ozs7O0FBRVoiLCJuYW1lcyI6W10sInNvdXJjZXMiOlsiQ2FtZXJhU3RhdHVzLnRzeCJdLCJ2ZXJzaW9uIjozLCJzb3VyY2VzQ29udGVudCI6WyJpbXBvcnQgeyB1c2VFZmZlY3QsIHVzZVN0YXRlIH0gZnJvbSAncmVhY3QnO1xuXG5pbnRlcmZhY2UgQ2FtU3RhdCB7IHN0YXR1czogJ29ubGluZScgfCAnb2ZmbGluZScgfCAnYXV0aCcgfCAnY29ubmVjdGluZycgfVxuXG4vLyBNZW51YmFyIGl0ZW0g4oCUINC60LDQvNC10YDRi9C9INC+0L3Qu9Cw0LnQvS/QvdC40LnRgiDRgtC+0L7QsyDRhdCw0YDRg9GD0LvQtiwg0LTQsNGA0LLQsNC7INCx0q/RgtGN0L0g0YXRg9GD0LTQsNGBINC90Y3RjdC90Y1cbmV4cG9ydCBkZWZhdWx0IGZ1bmN0aW9uIENhbWVyYVN0YXR1cyh7IHRva2VuLCBvbk9wZW4sIGFjdGl2ZSB9OiB7IHRva2VuOiBzdHJpbmc7IG9uT3BlbjogKCkgPT4gdm9pZDsgYWN0aXZlPzogYm9vbGVhbiB9KSB7XG4gIGNvbnN0IFtjYW1zLCBzZXRDYW1zXSA9IHVzZVN0YXRlPENhbVN0YXRbXT4oW10pO1xuXG4gIGFzeW5jIGZ1bmN0aW9uIGxvYWQoKSB7XG4gICAgdHJ5IHtcbiAgICAgIGNvbnN0IHIgPSBhd2FpdCBmZXRjaCgnL2FwaS9jYW1lcmEtc3RhdHVzJywgeyBoZWFkZXJzOiB7ICdYLUF1dGgtVG9rZW4nOiB0b2tlbiB9IH0pO1xuICAgICAgaWYgKHIub2spIHNldENhbXMoYXdhaXQgci5qc29uKCkpO1xuICAgIH0gY2F0Y2ggeyAvKiBpZ25vcmUgKi8gfVxuICB9XG4gIHVzZUVmZmVjdCgoKSA9PiB7IGxvYWQoKTsgY29uc3QgaXYgPSBzZXRJbnRlcnZhbChsb2FkLCAxMDAwMCk7IHJldHVybiAoKSA9PiBjbGVhckludGVydmFsKGl2KTsgfSwgW10pO1xuXG4gIGNvbnN0IG9ubGluZSA9IGNhbXMuZmlsdGVyKGMgPT4gYy5zdGF0dXMgPT09ICdvbmxpbmUnKS5sZW5ndGg7XG4gIGNvbnN0IHRvdGFsID0gY2Ftcy5sZW5ndGg7XG4gIGNvbnN0IGRvdENvbG9yID0gdG90YWwgPT09IDAgPyAndmFyKC0tdGV4dC1tdXRlZCknIDogb25saW5lID09PSB0b3RhbCA/ICd2YXIoLS1hY2NlbnQtZ3JlZW4pJyA6IG9ubGluZSA9PT0gMCA/ICd2YXIoLS1hY2NlbnQtcmVkKScgOiAndmFyKC0tYWNjZW50LW9yYW5nZSknO1xuXG4gIHJldHVybiAoXG4gICAgPGJ1dHRvbiBjbGFzc05hbWU9e2BtZW51LWl0ZW0ke2FjdGl2ZSA/ICcgYWN0aXZlJyA6ICcnfWB9IG9uQ2xpY2s9e29uT3Blbn0gdGl0bGU9XCLQmtCw0LzQtdGA0YvQvSDRgtOp0LvTqdCyXCIgc3R5bGU9e3sgZGlzcGxheTogJ2lubGluZS1mbGV4JywgYWxpZ25JdGVtczogJ2NlbnRlcicsIGdhcDogNiB9fT5cbiAgICAgIDxzcGFuIHN0eWxlPXt7IGRpc3BsYXk6ICdpbmxpbmUtYmxvY2snLCB3aWR0aDogOCwgaGVpZ2h0OiA4LCBib3JkZXJSYWRpdXM6ICc1MCUnLCBiYWNrZ3JvdW5kOiBkb3RDb2xvciB9fSAvPlxuICAgICAg0JrQsNC80LXRgCB7b25saW5lfS97dG90YWx9XG4gICAgPC9idXR0b24+XG4gICk7XG59XG4iXX0=