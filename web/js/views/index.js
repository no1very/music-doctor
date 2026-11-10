/* Route table: hash route -> view renderer. */

import { api, parseRoute, state, toast } from "../core.js";
import * as library from "./library.js";
import * as playlists from "./playlists.js";
import * as tasks from "./tasks.js";
import * as rooms from "./rooms.js";
import * as settings from "./settings.js";
import * as reports from "./reports.js";
import { openTrackEditor } from "./editor.js";
import { empty } from "./common.js";

const ROUTES = {
  dashboard: library.dashboard,
  library: library.library,
  albums: library.albums,
  album: library.albumDetail,
  artists: library.artists,
  artist: library.artistDetail,
  playlists: playlists.playlists,
  playlist: playlists.playlistDetail,
  tasks: tasks.tasks,
  reports: reports.reports,
  duplicates: tasks.duplicates,
  rooms: rooms.rooms,
  settings: settings.settings,
};

let rendering = false;
let lastRoute = null;

export async function renderRoute(route = parseRoute(), { keepScroll = false } = {}) {
  const root = document.getElementById("view");
  if (!root) return;
  lastRoute = route;
  const scroll = keepScroll ? root.scrollTop : 0;
  // Live search re-renders the whole view: remember which field the user was
  // typing in (and the caret) so focus does not jump out mid-word.
  const active = document.activeElement;
  const focusState = active && active.id && root.contains(active)
    ? { id: active.id, start: active.selectionStart, end: active.selectionEnd }
    : null;
  rendering = true;
  root.innerHTML = '<div class="empty">加载中…</div>';
  try {
    if (route.name === "task") {
      await renderBackground(root, route);
      await tasks.openTask(route.id);
      return;
    }
    if (route.name === "track") {
      await library.library(root, route, ctx);
      await openTrackEditor(route.id);
      return;
    }
    const view = ROUTES[route.name] || library.dashboard;
    await view(root, route, ctx);
    root.scrollTop = scroll;
  } catch (err) {
    console.error(err);
    root.innerHTML = empty(err.message || "页面加载失败", "!");
    if (err.status === 403 || err.status === 404) toast(err.message, "warn");
  } finally {
    rendering = false;
    markNav(route);
    if (focusState) {
      const el = document.getElementById(focusState.id);
      if (el) {
        el.focus({ preventScroll: true });
        if (typeof focusState.start === "number" && el.setSelectionRange) {
          try {
            el.setSelectionRange(focusState.start, focusState.end);
          } catch {
            /* inputs that do not support selection */
          }
        }
      }
    }
  }
}

async function renderBackground(root, route) {
  // keep a meaningful backdrop behind the task drawer
  void route;
  await tasks.tasks(root, route, ctx);
}

function markNav(route) {
  document.querySelectorAll(".nav a").forEach((link) => {
    link.classList.toggle("active", link.dataset.route === route.name
      || (route.name === "task" && link.dataset.route === "tasks")
      || (route.name === "track" && link.dataset.route === "library"));
  });
}

export const ctx = {
  refresh(options = {}) {
    if (rendering) return;
    return renderRoute(lastRoute || parseRoute(), options);
  },
};

export { api, state };
