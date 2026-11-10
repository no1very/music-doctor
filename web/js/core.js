/* Core: state, API client, formatting, toasts, panels, router, live events. */

export const state = {
  user: null,
  token: localStorage.getItem("omt_token") || "",
  settings: {},
  libraries: [],
  tracks: [],
  selection: new Set(),
  page: { offset: 0, limit: 100, total: 0 },
  filters: {
    q: "", sort: "title", order: "asc", only: "", album_id: "", artist_id: "",
    artistQ: "", artistSort: "name",
  },
  tasks: [],
  room: null,
  meta: {},
};

/* ------------------------------------------------------------------ api */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body, raw = false } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    logout(true);
    throw new ApiError("登录已过期，请重新登录", 401);
  }
  if (raw) return response;
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }
  }
  if (!response.ok) throw new ApiError(data.error || `请求失败 (${response.status})`, response.status);
  return data;
}

export function logout(silent) {
  const token = state.token;
  state.token = "";
  state.user = null;
  localStorage.removeItem("omt_token");
  if (token) fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
  showLogin();
  if (!silent) toast("已退出登录");
}

/* ------------------------------------------------------------ formatting */
export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

export function fmtSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[i]}`;
}

export function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(String(value).replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export const debounce = (fn, wait = 250) => {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
};

/* ------------------------------------------------------------------ toast */
export function toast(message, type = "info") {
  const host = document.getElementById("toasts");
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

/* ---------------------------------------------------------------- panels */
export function openPanel(id, html, title) {
  const panel = document.getElementById(id);
  panel.innerHTML = html;
  panel.hidden = false;
  document.getElementById("scrim").hidden = false;
  if (title) panel.querySelector("h3") && (panel.querySelector("h3").textContent = title);
  document.body.dataset.panel = id;
}

export function closePanels() {
  document.querySelectorAll(".drawer").forEach((el) => (el.hidden = true));
  document.getElementById("scrim").hidden = true;
  delete document.body.dataset.panel;
}

export function closeEditor() {
  document.getElementById("editor").hidden = true;
  const others = [...document.querySelectorAll(".drawer")].some((el) => !el.hidden);
  if (!others) document.getElementById("scrim").hidden = true;
}

/* ----------------------------------------------------------------- router */
export function parseRoute() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, queryString] = hash.split("?");
  const parts = path.split("/").filter(Boolean);
  const query = Object.fromEntries(new URLSearchParams(queryString || ""));
  return { name: parts[0] || "dashboard", id: parts[1] || "", query };
}

export function go(hash) {
  if (location.hash === hash) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = hash;
}

/* -------------------------------------------------------------- live feed */
let source = null;
const listeners = new Set();

export function onEvent(handler) {
  listeners.add(handler);
  return () => listeners.delete(handler);
}

export function connectEvents() {
  if (!state.token) return;
  if (source) source.close();
  source = new EventSource(`/api/events?token=${encodeURIComponent(state.token)}`);
  source.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    listeners.forEach((handler) => {
      try {
        handler(payload);
      } catch (err) {
        console.error(err);
      }
    });
  };
  source.onerror = () => {
    source.close();
    source = null;
    setTimeout(connectEvents, 5000);
  };
}

/* ------------------------------------------------------------------ login */
export function showLogin() {
  hideBoot();
  document.getElementById("app").hidden = true;
  document.getElementById("player").hidden = true;
  document.getElementById("login").hidden = false;
  const user = document.getElementById("loginUser");
  if (user && !user.value) user.value = "admin";
  setTimeout(() => document.getElementById("loginPass")?.focus(), 50);
}

export function showApp() {
  hideBoot();
  document.getElementById("login").hidden = true;
  document.getElementById("app").hidden = false;
  document.getElementById("player").hidden = false;
}

function hideBoot() {
  const boot = document.getElementById("boot");
  if (boot) boot.hidden = true;
}

export function bootFailed(error) {
  const boot = document.getElementById("boot");
  if (!boot || boot.hidden) return; // app already booted: keep the console message only
  boot.hidden = false;
  const text = document.getElementById("bootError");
  if (text) {
    text.hidden = false;
    text.textContent = `初始化失败：${error?.message || error}\n请强制刷新（Ctrl+F5）；若仍失败，请检查浏览器控制台。`;
  }
}
