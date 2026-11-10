/**
 * UI smoke test without a browser.
 *
 * The sandbox used during development blocks Chromium IPC, so this script mounts
 * a tiny DOM shim and renders every view against the live API. It catches the
 * runtime errors that a syntax check cannot: undefined fields, broken helpers,
 * rejected promises inside renderers.
 *
 *   node tools/ui_smoke.mjs [baseUrl] [username] [passwordFile]
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const base = process.argv[2] || "http://127.0.0.1:8318";
const username = process.argv[3] || "admin";
const passwordFile = process.argv[4] || path.join(ROOT, "data", "INITIAL_ADMIN.txt");

/* --------------------------------------------------------------- DOM shim */
class ClassList {
  add() {}
  remove() {}
  toggle() {}
  contains() {
    return false;
  }
}

class Element {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.hidden = false;
    this.dataset = {};
    this.style = {};
    this.classList = new ClassList();
    this.files = [];
  }
  addEventListener() {}
  removeEventListener() {}
  querySelector() {
    return null;
  }
  querySelectorAll() {
    return [];
  }
  appendChild() {}
  insertAdjacentHTML() {}
  remove() {}
  setAttribute() {}
  closest() {
    return null;
  }
  focus() {}
}

const elements = new Map();
const domListeners = new Map();
const getElement = (id) => {
  if (!elements.has(id)) elements.set(id, new Element("div", id));
  return elements.get(id);
};

globalThis.document = {
  getElementById: getElement,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => new Element(tag),
  addEventListener(type, handler) {
    if (!domListeners.has(type)) domListeners.set(type, []);
    domListeners.get(type).push(handler);
  },
  body: new Element("body"),
  documentElement: new Element("html"),
  title: "",
};
globalThis.window = {
  addEventListener() {},
  dispatchEvent() {},
  location: { hash: "#/dashboard", origin: base },
  prompt: () => null,
  confirm: () => false,
};
globalThis.location = globalThis.window.location;
globalThis.localStorage = {
  store: new Map(),
  getItem(key) {
    return this.store.get(key) ?? null;
  },
  setItem(key, value) {
    this.store.set(key, String(value));
  },
  removeItem(key) {
    this.store.delete(key);
  },
};
globalThis.CustomEvent = class CustomEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.detail = options.detail;
  }
};
globalThis.HashChangeEvent = globalThis.CustomEvent;
globalThis.MediaMetadata = class MediaMetadata {
  constructor(data) {
    Object.assign(this, data);
  }
};
globalThis.EventSource = class EventSource {
  constructor() {
    this.onmessage = null;
    this.onerror = null;
  }
  close() {}
};
if (!globalThis.navigator) globalThis.navigator = {};
globalThis.navigator.mediaSession = { setActionHandler() {}, metadata: null };

/* Node has no document base URL, so resolve the app's relative /api paths. */
const realFetch = globalThis.fetch;
globalThis.fetch = (input, init) => {
  if (typeof input === "string" && input.startsWith("/")) return realFetch(base + input, init);
  return realFetch(input, init);
};

/* ------------------------------------------------------------------- boot */
const password = readFileSync(passwordFile, "utf8")
  .split(/\r?\n/)
  .find((line) => line.startsWith("password:"))
  ?.split(":")[1]
  ?.trim();
if (!password) throw new Error(`no password found in ${passwordFile}`);

async function api(path) {
  const response = await fetch(base + path, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return response.json();
}

const core = await import(pathToUrl("web/js/core.js"));
const views = await import(pathToUrl("web/js/views/index.js"));
const player = await import(pathToUrl("web/js/player.js"));
const editor = await import(pathToUrl("web/js/views/editor.js"));
const lib = await import(pathToUrl("web/js/views/library.js"));
const listViews = await import(pathToUrl("web/js/views/playlists.js"));
const taskViews = await import(pathToUrl("web/js/views/tasks.js"));
const roomViews = await import(pathToUrl("web/js/views/rooms.js"));
const settingsViews = await import(pathToUrl("web/js/views/settings.js"));
const reportViews = await import(pathToUrl("web/js/views/reports.js"));
const { state } = core;

function pathToUrl(relative) {
  return new URL(relative, `file:///${ROOT.replace(/\\/g, "/")}/`).href;
}

let failures = 0;
const problems = [];

/* ---------------------------------------------------------------- SPA boot
 * Importing app.js and firing DOMContentLoaded catches missing imports and
 * startup errors that the per-view tests cannot see (a black page bug once
 * slipped through exactly this way). */
let bootFailure = null;
process.on("unhandledRejection", (reason) => {
  bootFailure = bootFailure || reason;
});
process.on("uncaughtException", (error) => {
  bootFailure = bootFailure || error;
});
state.token = "";
state.user = null;
try {
  await import(pathToUrl("web/js/app.js"));
  for (const handler of domListeners.get("DOMContentLoaded") || []) handler();
  await new Promise((resolve) => setTimeout(resolve, 700));
  if (bootFailure) throw bootFailure;
  if (getElement("login").hidden) throw new Error("login screen not shown after unauthenticated boot");
  if (!getElement("boot").hidden) throw new Error("boot overlay is still visible");
  console.log(`  PASS  ${"spa-boot".padEnd(18)}`);
} catch (error) {
  failures += 1;
  problems.push(`spa-boot: ${error.message || error}`);
  console.log(`  FAIL  ${"spa-boot".padEnd(18)} ${error.message || error}`);
}

const login = await fetch(base + "/api/auth/login", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username, password }),
});
const session = await login.json();
if (!session.token) throw new Error("login failed: " + JSON.stringify(session));
state.token = session.token;
state.user = session.user;

const [libraries, albums, artists, playlists, tracks] = await Promise.all([
  api("/api/libraries"),
  api("/api/albums?limit=3"),
  api("/api/artists?limit=3"),
  api("/api/playlists"),
  api("/api/tracks?limit=3"),
]);
state.libraries = libraries.items;

const routes = [
  ["dashboard", lib.dashboard, { name: "dashboard", id: "", query: {} }],
  ["library", lib.library, { name: "library", id: "", query: {} }],
  ["albums", lib.albums, { name: "albums", id: "", query: {} }],
  ["artists", lib.artists, { name: "artists", id: "", query: {} }],
  ["playlists", listViews.playlists, { name: "playlists", id: "", query: {} }],
  ["tasks", taskViews.tasks, { name: "tasks", id: "", query: {} }],
  ["reports", reportViews.reports, { name: "reports", id: "", query: { days: "14" } }],
  ["duplicates", taskViews.duplicates, { name: "duplicates", id: "", query: {} }],
  ["rooms", roomViews.rooms, { name: "rooms", id: "", query: {} }],
  ["settings", settingsViews.settings, { name: "settings", id: "", query: {} }],
  ["settings/scrape", settingsViews.settings, { name: "settings", id: "", query: { tab: "scrape" } }],
  ["settings/naming", settingsViews.settings, { name: "settings", id: "", query: { tab: "naming" } }],
  ["settings/users", settingsViews.settings, { name: "settings", id: "", query: { tab: "users" } }],
  ["settings/plugins", settingsViews.settings, { name: "settings", id: "", query: { tab: "plugins" } }],
  ["settings/about", settingsViews.settings, { name: "settings", id: "", query: { tab: "about" } }],
];

if (albums.items[0]) routes.push(["album", lib.albumDetail, { name: "album", id: albums.items[0].album_id, query: {} }]);
if (artists.items[0]) routes.push(["artist", lib.artistDetail, { name: "artist", id: artists.items[0].artist_id, query: {} }]);
if (playlists.items[0]) routes.push(["playlist", listViews.playlistDetail, { name: "playlist", id: String(playlists.items[0].id), query: {} }]);

const ctx = {
  refresh() {},
};

for (const [label, view, route] of routes) {
  const root = new Element("section", "view");
  elements.set("view", root);
  try {
    await view(root, route, ctx);
    const html = root.innerHTML;
    if (!html || html.length < 120) throw new Error(`suspiciously short output (${html.length} chars)`);
    if (html.includes(">undefined<") || html.includes("undefined%")) {
      throw new Error("rendered literal 'undefined'");
    }
    console.log(`  PASS  ${label.padEnd(18)} ${String(html.length).padStart(6)} chars`);
  } catch (error) {
    failures += 1;
    problems.push(`${label}: ${error.message}`);
    console.log(`  FAIL  ${label.padEnd(18)} ${error.message}`);
  }
}

/* router dispatch + task drawer */
try {
  const root = new Element("section", "view");
  elements.set("view", root);
  await views.renderRoute({ name: "dashboard", id: "", query: {} });
  if (!root.innerHTML.includes("仪表盘")) throw new Error("dispatch did not render dashboard");
  console.log(`  PASS  ${"router-dispatch".padEnd(18)}`);
} catch (error) {
  failures += 1;
  problems.push(`router-dispatch: ${error.message}`);
  console.log(`  FAIL  ${"router-dispatch".padEnd(18)} ${error.message}`);
}

/* editor drawer (covers tags/lyrics/scrape/history templates) */
if (tracks.items[0]) {
  try {
    await editor.openTrackEditor(tracks.items[0].id);
    const html = getElement("editor").innerHTML;
    if (!html.includes("trackForm")) throw new Error("editor form missing");
    console.log(`  PASS  ${"track-editor".padEnd(18)} ${String(html.length).padStart(6)} chars`);
    await editor.searchCandidates();
    console.log(`  PASS  ${"scrape-candidates".padEnd(18)}`);
  } catch (error) {
    failures += 1;
    problems.push(`track-editor: ${error.message}`);
    console.log(`  FAIL  ${"track-editor".padEnd(18)} ${error.message}`);
  }
}

/* player queue rendering with real data */
try {
  if (tracks.items.length) {
    player.player.queue = tracks.items;
    player.player.index = 0;
    player.player.current = tracks.items[0];
    player.renderQueue();
    console.log(`  PASS  ${"player-queue".padEnd(18)}`);
  }
} catch (error) {
  failures += 1;
  problems.push(`player-queue: ${error.message}`);
  console.log(`  FAIL  ${"player-queue".padEnd(18)} ${error.message}`);
}

console.log("\n" + "=".repeat(56));
console.log(`  ${routes.length + 5 - failures} passed, ${failures} failed`);
if (problems.length) console.log("  " + problems.join("\n  "));
console.log("=".repeat(56));
process.exit(failures ? 1 : 0);
