/* App bootstrap: auth, chrome, global actions, live updates, shortcuts. */

import {
  api,
  bootFailed,
  closeEditor,
  connectEvents,
  debounce,
  esc,
  go,
  onEvent,
  openPanel,
  parseRoute,
  showApp,
  showLogin,
  state,
  toast,
} from "./core.js";
import {
  clearQueue,
  cycleRepeat,
  enqueue,
  initPlayer,
  next,
  player,
  playQueue,
  playQueueIndex,
  prev,
  removeFromQueue,
  restoreQueue,
  toggleMute,
  togglePlay,
  toggleShuffle,
  setVolume,
} from "./player.js";
import { ctx, renderRoute } from "./views/index.js";
import {
  applyArtistCandidate,
  clearSelectionAndRefresh,
  enqueueArtistById,
  openArtistScrape,
  playAlbumById,
  playArtistById,
} from "./views/library.js";
import { openPlaylistEditor, playPlaylist, ruleRow } from "./views/playlists.js";
import { appendTaskLog, openTask, resolveAllBest, resolveDuplicateGroup } from "./views/tasks.js";
import { createRoom, joinRoom, leaveRoom, onRoomState, roomSync, roomToggle } from "./views/rooms.js";
import {
  applyCandidate,
  fetchLyrics,
  openTrackEditor,
  removeCover,
  renameTrack,
  restoreVersion,
  searchCandidates,
} from "./views/editor.js";

/* ------------------------------------------------------------------ boot */
async function boot() {
  try {
    initPlayer();
    bindGlobalEvents();
    window.addEventListener("hashchange", () => renderRoute());
    window.addEventListener("refresh-view", debounce(() => ctx.refresh(), 120));

    try {
      const data = await api("/api/auth/me");
      state.user = data.user;
      showApp();
      await loadChrome();
      await renderRoute();
      await restoreQueue();
      connectEvents();
    } catch {
      showLogin();
    }
  } catch (error) {
    console.error("boot failed", error);
    bootFailed(error);
  }
}

async function login(username, password) {
  const data = await api("/api/auth/login", { method: "POST", body: { username, password } });
  state.token = data.token;
  state.user = data.user;
  localStorage.setItem("omt_token", data.token);
  showApp();
  await loadChrome();
  await renderRoute();
  await restoreQueue();
  connectEvents();
}

async function doLogout() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  state.token = "";
  state.user = null;
  localStorage.removeItem("omt_token");
  showLogin();
}

function bindLogoutButtons() {
  ["logoutBtn", "topLogout"].forEach((id) => {
    const button = document.getElementById(id);
    if (!button || button.dataset.logoutBound) return;
    button.dataset.logoutBound = "1";
    button.addEventListener("click", doLogout);
  });
}

async function loadChrome() {
  try {
    const settings = await api("/api/settings");
    state.settings = settings.items || {};
  } catch {
    /* keep whatever we have */
  }
  document.getElementById("siteName").textContent = state.settings["site.name"] || "Music-Doctor";
  document.getElementById("sideUser").innerHTML = `
    <div class="side-user-main">
      <div class="avatar">${esc((state.user?.display_name || state.user?.username || "?").slice(0, 1).toUpperCase())}</div>
      <div class="who">
        <div class="name">${esc(state.user?.display_name || state.user?.username || "")}</div>
        <div class="role">${esc(state.user?.role || "")}</div>
      </div>
      <button class="icon-btn" id="pwBtn" title="修改密码">⚿</button>
    </div>
    <button class="logout-btn" id="logoutBtn" type="button" title="退出当前账号">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"></path><path d="M10 17l5-5-5-5"></path><path d="M15 12H3"></path>
      </svg>
      退出登录
    </button>`;
  document.getElementById("pwBtn").addEventListener("click", openPasswordDialog);
  bindLogoutButtons();

  try {
    const data = await api("/api/libraries");
    state.libraries = data.items;
    document.getElementById("sideLibraries").innerHTML = data.items.length
      ? data.items.map((lib) => `<a href="#/library" data-lib="${lib.id}" title="${esc(lib.name)} · ${lib.track_count} 首"><span class="side-lib-name">${esc(lib.name)}</span><span class="side-lib-count">${lib.track_count} 首</span></a>`).join("")
      : '<div class="muted tiny" style="padding:6px 10px">尚未添加媒体库</div>';
    document.querySelectorAll("[data-lib]").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        state.filters.q = "";
        state.filters.only = "";
        go("#/library");
      });
    });
  } catch {
    /* non fatal */
  }
  refreshTaskBadge();
}

/* --------------------------------------------------------------- metrics */
async function refreshTaskBadge() {
  try {
    const data = await api("/api/tasks?limit=20");
    const running = data.items.filter((task) => ["running", "queued", "canceling"].includes(task.status));
    state.tasks = data.items;
    const badge = document.getElementById("taskBadge");
    if (!running.length) {
      badge.hidden = true;
      return;
    }
    badge.hidden = false;
    badge.textContent = running.length === 1 ? `${running[0].title} ${Math.round(running[0].progress || 0)}%` : `${running.length} 个任务进行中`;
  } catch {
    /* ignore */
  }
}

/* ---------------------------------------------------------------- search */
function bindSearch() {
  const input = document.getElementById("globalSearch");
  const results = document.getElementById("searchResults");
  const run = debounce(async () => {
    const q = input.value.trim();
    if (!q) {
      results.hidden = true;
      return;
    }
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
      const parts = [];
      if (data.artists.length) {
        parts.push('<div class="sr-group">艺术家</div>');
        data.artists.slice(0, 4).forEach((artist) => parts.push(`
          <div class="sr-item" data-action="open-artist" data-id="${artist.artist_id}">
            <div class="thumb cover-ph">☺</div><div><div>${esc(artist.name)}</div><div class="muted tiny">${artist.album_count} 张专辑</div></div>
          </div>`));
      }
      if (data.albums.length) {
        parts.push('<div class="sr-group">专辑</div>');
        data.albums.slice(0, 4).forEach((album) => parts.push(`
          <div class="sr-item" data-action="open-album" data-id="${album.album_id}">
            ${album.cover_url ? `<img src="${esc(album.cover_url)}" alt="" />` : '<div class="thumb cover-ph">▣</div>'}
            <div><div>${esc(album.name)}</div><div class="muted tiny">${esc(album.artist || "")}</div></div>
          </div>`));
      }
      if (data.tracks.length) {
        parts.push('<div class="sr-group">歌曲</div>');
        data.tracks.slice(0, 8).forEach((track) => parts.push(`
          <div class="sr-item" data-action="play-track" data-id="${track.id}">
            ${track.cover_url ? `<img src="${esc(track.cover_url)}" alt="" />` : '<div class="thumb cover-ph">♪</div>'}
            <div><div>${esc(track.title)}</div><div class="muted tiny">${esc(track.artist || "")} · ${esc(track.album || "")}</div></div>
          </div>`));
      }
      results.innerHTML = parts.join("") || '<div class="sr-group">没有结果</div>';
      results.hidden = false;
    } catch {
      results.hidden = true;
    }
  }, 400);
  let composing = false;
  input.addEventListener("compositionstart", () => {
    composing = true;
  });
  input.addEventListener("compositionend", () => {
    composing = false;
    run();
  });
  input.addEventListener("input", () => {
    if (!composing) run();
  });
  input.addEventListener("focus", () => {
    if (results.innerHTML) results.hidden = false;
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".searchbox")) results.hidden = true;
  });
}

/* ---------------------------------------------------------------- drawing */
function openScanDialog() {
  openPanel("editor", `
    <div class="drawer-head"><h3>扫描媒体库</h3><button class="icon-btn" data-action="close-editor">✕</button></div>
    <div class="drawer-body">
      ${state.libraries.length ? state.libraries.map((lib) => `
        <div class="panel" style="margin-bottom:10px;display:flex;align-items:center;gap:10px">
          <div style="flex:1">
            <strong>${esc(lib.name)}</strong>
            <div class="muted tiny">${esc(lib.path)} · ${lib.track_count} 首</div>
          </div>
          <button class="btn sm primary" data-action="scan-library" data-id="${lib.id}">扫描</button>
          <button class="btn sm" data-action="scan-library" data-id="${lib.id}" data-force="1">全量</button>
        </div>`).join("") : '<div class="empty">还没有媒体库，请先在设置中添加</div>'}
      <div class="toolbar">
        <button class="btn primary" data-action="scan-all">扫描全部</button>
        <button class="btn" data-action="scrape-untagged">刮削未完成曲目</button>
      </div>
      <p class="muted tiny">扫描在后台任务中心执行，可随时离开页面。</p>
    </div>`);
}

function openBatchEditDialog() {
  if (!state.selection.size) return;
  openPanel("editor", `
    <div class="drawer-head"><h3>批量修改 ${state.selection.size} 首</h3><button class="icon-btn" data-action="close-editor">✕</button></div>
    <div class="drawer-body">
      <p class="muted tiny">留空的字段不会被修改。修改会写入音频文件并记录版本历史。</p>
      <form id="batchForm" class="editor-grid" style="margin-top:12px">
        <label class="field">艺术家<input type="text" name="artist" /></label>
        <label class="field">专辑艺术家<input type="text" name="albumartist" /></label>
        <label class="field">专辑<input type="text" name="album" /></label>
        <label class="field">流派<input type="text" name="genre" /></label>
        <label class="field">年份<input name="year" type="number" /></label>
        <label class="field">注释<input type="text" name="comment" /></label>
        <div class="full"><button class="btn primary" type="submit">应用修改</button></div>
      </form>
    </div>`);
  document.getElementById("batchForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = {};
    new FormData(event.target).forEach((value, key) => {
      if (String(value).trim() !== "") fields[key] = value;
    });
    if (!Object.keys(fields).length) return toast("没有填写任何字段", "warn");
    try {
      const result = await api("/api/tracks/batch", {
        method: "POST",
        body: { ids: [...state.selection], action: "edit", fields },
      });
      toast(`已更新 ${result.updated} 首`);
      state.selection.clear();
      closeEditor();
      ctx.refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

function openBatchRenameDialog() {
  if (!state.selection.size) return toast("请先选择曲目", "warn");
  openPanel("editor", `
    <div class="drawer-head"><h3>批量重命名 ${state.selection.size} 首</h3><button class="icon-btn" data-action="close-editor">✕</button></div>
    <div class="drawer-body">
      <form id="renameForm">
        <label class="field">文件名模板
          <input type="text" name="template" value="${esc(state.settings["naming.template"] || "${tracknumber.zfill(2)} - ${title}")}" />
        </label>
        <p class="muted tiny">支持 ${"${变量}"} 表达式；只改文件名，不移动目录；冲突会跳过。</p>
        <div class="toolbar" style="margin-top:10px">
          <button class="btn" type="button" data-action="rename-preview">预览</button>
          <button class="btn primary" type="submit">执行重命名</button>
        </div>
      </form>
      <div id="renamePreview" style="margin-top:14px"></div>
    </div>`);
  const form = document.getElementById("renameForm");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await applyRename(form.template.value);
  });
  form.querySelector("[data-action='rename-preview']").addEventListener("click", () => previewRename(form.template.value));
}

async function previewRename(template) {
  const host = document.getElementById("renamePreview");
  host.innerHTML = '<div class="muted">计算中…</div>';
  try {
    const data = await api("/api/tracks/rename/preview", {
      method: "POST",
      body: { ids: [...state.selection], template },
    });
    const conflicts = data.items.filter((item) => item.conflict || item.error).length;
    host.innerHTML = `
      <div class="muted tiny" style="margin-bottom:8px">${data.items.length} 首 · ${conflicts} 个冲突/跳过</div>
      <div class="table-wrap"><table class="data"><thead><tr><th>原名</th><th>新名</th></tr></thead>
      <tbody>${data.items.map((item) => `<tr>
        <td class="muted tiny">${esc(item.from)}</td>
        <td class="${item.conflict || item.error ? "error" : ""}">${esc(item.to || item.error || "—")}
          ${item.conflict ? '<span class="badge bad">已存在</span>' : ""}</td></tr>`).join("")}</tbody></table></div>`;
  } catch (err) {
    host.innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
}

async function applyRename(template) {
  if (!template) return toast("请填写模板", "warn");
  try {
    const result = await api("/api/tracks/batch", {
      method: "POST",
      body: { ids: [...state.selection], action: "rename", template },
    });
    toast(`已重命名 ${result.renamed} 首${result.errors.length ? `，${result.errors.length} 首失败` : ""}`);
    closeEditor();
    ctx.refresh();
  } catch (err) {
    toast(err.message, "error");
  }
}

function openPasswordDialog() {
  openPanel("editor", `
    <div class="drawer-head"><h3>修改密码</h3><button class="icon-btn" data-action="close-editor">✕</button></div>
    <div class="drawer-body">
      <p class="muted tiny">修改后所有设备上的登录都会失效，需要用新密码重新登录。</p>
      <form id="passwordForm" class="editor-grid" style="margin-top:12px">
        <label class="field full">当前密码<input name="current" type="password" autocomplete="current-password" required /></label>
        <label class="field">新密码<input name="new" type="password" minlength="6" autocomplete="new-password" required /></label>
        <label class="field">确认新密码<input name="confirm" type="password" minlength="6" autocomplete="new-password" required /></label>
        <div class="full"><button class="btn primary" type="submit">修改密码</button></div>
      </form>
      <p class="muted tiny" style="margin-top:12px">忘记密码：在服务器上执行
        <code>docker exec -it open-music-tag python run.py --reset-password admin</code>。</p>
    </div>`);
  const form = document.getElementById("passwordForm");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const current = data.get("current") || "";
    const next = data.get("new") || "";
    if (next.length < 6) return toast("新密码至少 6 位", "warn");
    if (next !== data.get("confirm")) return toast("两次输入的新密码不一致", "warn");
    try {
      await api("/api/auth/password", { method: "POST", body: { current, new: next } });
      toast("密码已修改，请用新密码重新登录");
      state.token = "";
      state.user = null;
      localStorage.removeItem("omt_token");
      closeEditor();
      showLogin();
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

/* ----------------------------------------------------------------- actions */
function bindGlobalEvents() {
  bindSearch();

  document.getElementById("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("loginError");
    error.hidden = true;
    try {
      await login(document.getElementById("loginUser").value.trim(), document.getElementById("loginPass").value);
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });

  document.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;
    const id = Number(target.dataset.id || 0);
    try {
      switch (action) {
        case "toggle-sidebar":
          document.getElementById("sidebar").classList.toggle("open");
          break;
        case "open-scan":
          openScanDialog();
          break;
        case "quick-scan":
          await scanAll();
          break;
        case "scan-all":
          await scanAll();
          break;
        case "scan-library":
          await scanLibrary(Number(target.dataset.id), target.dataset.force === "1");
          break;
        case "scrape-untagged":
          await scrapeUntagged();
          break;
        case "open-tasks":
          go("#/tasks");
          break;
        case "page":
          state.page.offset = Math.max(0, Number(target.dataset.offset || 0));
          ctx.refresh();
          break;
        case "filter":
          state.filters.only = target.dataset.only;
          state.page.offset = 0;
          ctx.refresh();
          break;
        case "select-track":
          toggleSelection(id, target.checked);
          break;
        case "select-all": {
          const ids = state.tracks.map((track) => track.id);
          if (target.checked) ids.forEach((trackId) => state.selection.add(trackId));
          else ids.forEach((trackId) => state.selection.delete(trackId));
          ctx.refresh({ keepScroll: true });
          break;
        }
        case "clear-selection":
          state.selection.clear();
          ctx.refresh({ keepScroll: true });
          break;
        case "batch-scrape":
          await batchScrape([...state.selection]);
          break;
        case "batch-edit":
          openBatchEditDialog();
          break;
        case "batch-rename":
          openBatchRenameDialog();
          break;
        case "rename-preview":
          await previewRename(document.getElementById("renameForm").template.value);
          break;
        case "report-days":
          go(`#/reports?days=${target.dataset.days}`);
          break;
        case "delete-apikey":
          if (!confirm("删除该 API 密钥？使用它的脚本会立即失效。")) break;
          await api(`/api/apikeys/${id}`, { method: "DELETE" });
          toast("密钥已删除");
          ctx.refresh();
          break;
        case "batch-reindex": {
          const result = await api("/api/tracks/batch", {
            method: "POST", body: { ids: [...state.selection], action: "reindex" },
          });
          toast(`已重新读取 ${result.reindexed} 首`);
          ctx.refresh();
          break;
        }
        case "batch-delete": {
          if (!confirm(`确定删除选中的 ${state.selection.size} 首曲目记录？（文件移动到 data/trash）`)) break;
          await api("/api/tracks/batch", {
            method: "POST",
            body: { ids: [...state.selection], action: "delete", delete_files: true },
          });
          state.selection.clear();
          toast("已删除");
          ctx.refresh();
          break;
        }
        case "play-track": {
          const track = await api(`/api/tracks/${id}`);
          playQueue([track], 0, { silent: true });
          break;
        }
        case "enqueue": {
          const track = await api(`/api/tracks/${id}`);
          enqueue([track]);
          break;
        }
        case "edit-track":
          event.preventDefault();
          go(`#/track/${id}`);
          break;
        case "toggle-favorite": {
          const trackId = id || player.current?.id;
          if (!trackId) break;
          const result = await api(`/api/tracks/${trackId}/favorite`, { method: "POST", body: {} });
          if (player.current?.id === trackId) {
            player.current.favorite = result.favorite;
            document.getElementById("npFav").classList.toggle("active", result.favorite);
            document.getElementById("npFav").textContent = result.favorite ? "♥" : "♡";
          }
          toast(result.favorite ? "已收藏" : "已取消收藏");
          break;
        }
        case "open-album":
          go(`#/album/${target.dataset.id}`);
          break;
        case "play-album":
          await playAlbumById(target.dataset.id);
          break;
        case "enqueue-album": {
          const album = await api(`/api/albums/${target.dataset.id}`);
          enqueue(album.tracks || []);
          break;
        }
        case "scrape-album": {
          const album = await api(`/api/albums/${target.dataset.id}`);
          await batchScrape((album.tracks || []).map((track) => track.id));
          break;
        }
        case "scrape-track": {
          if (!id) break;
          try {
            const result = await api(`/api/tracks/${id}/auto-scrape`, { method: "POST", body: {} });
            toast(`已匹配：${result.track?.artist || ""} - ${result.track?.title || ""}（${Math.round((result.score || 0) * 100)}%）`);
          } catch (err) {
            toast(err.message || "没有找到可信匹配", "warn");
          }
          ctx.refresh({ keepScroll: true });
          break;
        }
        case "open-artist":
          go(`#/artist/${target.dataset.id}`);
          break;
        case "play-artist":
          await playArtistById(target.dataset.id);
          break;
        case "enqueue-artist":
          await enqueueArtistById(target.dataset.id);
          break;
        case "scrape-artist":
          await openArtistScrape(target.dataset.id);
          break;
        case "apply-artist-candidate":
          await applyArtistCandidate(target);
          break;
        case "scrape-all-artists": {
          if (!confirm("刮削所有缺少头像/简介的艺术家？会在任务中心后台执行。")) break;
          const result = await api("/api/artists/batch/scrape", { method: "POST", body: {} });
          toast(`已创建艺术家刮削任务（${result.total} 位）`);
          go("#/tasks");
          break;
        }
        case "export-artist-images": {
          try {
            const result = await api("/api/artists/export", { method: "POST", body: {} });
            toast(`已导出 ${result.exported} 张艺术家图片到 ${result.folder}`);
            reportNavidromeArtwork(result.navidrome);
          } catch (err) {
            toast(err.message, "error");
          }
          break;
        }
        case "album-sort":
          state.filters.albumSort = target.dataset.sort;
          ctx.refresh();
          break;
        case "new-playlist":
          openPlaylistEditor();
          break;
        case "new-smart-playlist":
          openPlaylistEditor({ kind: "smart", rules: { match: "all", rules: [], sort: "added", order: "desc", limit: 100 } });
          break;
        case "edit-playlist": {
          const playlist = await api(`/api/playlists/${id}`);
          openPlaylistEditor(playlist);
          break;
        }
        case "delete-playlist": {
          if (!confirm("确定删除这个歌单？")) break;
          await api(`/api/playlists/${id}`, { method: "DELETE" });
          closeEditor();
          toast("歌单已删除");
          go("#/playlists");
          break;
        }
        case "play-playlist":
          await playPlaylist(id);
          break;
        case "enqueue-playlist":
          await playPlaylist(id, true);
          break;
        case "rule-add": {
          const rows = document.getElementById("ruleRows");
          rows.insertAdjacentHTML("beforeend", ruleRow(null));
          break;
        }
        case "rule-remove":
          target.closest(".rule-row")?.remove();
          break;
        case "open-task":
          await openTask(id);
          break;
        case "cancel-task":
          await api(`/api/tasks/${id}/cancel`, { method: "POST", body: {} });
          toast("已请求取消任务");
          break;
        case "dup-auto":
          await resolveDuplicateGroup(String(target.dataset.key), true);
          break;
        case "dup-scan":
          ctx.refresh();
          break;
        case "new-room":
          await createRoom();
          break;
        case "join-room":
          await joinRoom();
          break;
        case "enter-room":
          go(`#/rooms/${target.dataset.code}`);
          break;
        case "delete-room":
          if (!confirm("解散该房间？")) break;
          await api(`/api/rooms/${target.dataset.code}`, { method: "DELETE" });
          toast("房间已解散");
          ctx.refresh();
          break;
        case "room-sync":
          await roomSync();
          break;
        case "room-toggle":
          await roomToggle();
          break;
        case "leave-room":
          await leaveRoom();
          break;
        case "settings-tab":
          go(`#/settings?tab=${target.dataset.tab}`);
          break;
        case "delete-library":
          if (!confirm("移除媒体库？曲目索引会一并删除，文件不受影响。")) break;
          await api(`/api/libraries/${id}`, { method: "DELETE" });
          toast("媒体库已移除");
          ctx.refresh();
          break;
        case "toggle-user":
          await api(`/api/users/${id}`, { method: "PATCH", body: { active: target.dataset.active !== "1" } });
          toast("用户状态已更新");
          ctx.refresh();
          break;
        case "reset-user-password": {
          const next = window.prompt(`为 ${target.dataset.name} 设置新密码（至少 6 位）`);
          if (!next) break;
          if (next.length < 6) { toast("密码至少 6 位", "warn"); break; }
          await api(`/api/users/${id}`, { method: "PATCH", body: { password: next } });
          toast("密码已重置，该用户的登录会话已失效");
          break;
        }
        case "delete-user":
          if (!confirm("确定删除该用户？")) break;
          await api(`/api/users/${id}`, { method: "DELETE" });
          toast("用户已删除");
          ctx.refresh();
          break;
        case "test-provider": {
          const data = await api("/api/providers/test", {
            method: "POST",
            body: { provider: target.dataset.name, title: "test", artist: "test" },
          });
          toast(`${target.dataset.name}: 返回 ${data.items.length} 个候选`, data.items.length ? "info" : "warn");
          break;
        }
        case "save-site": {
          const value = document.getElementById("siteNameInput").value.trim();
          await api("/api/settings", { method: "PUT", body: { "site.name": value } });
          state.settings["site.name"] = value;
          document.getElementById("siteName").textContent = value;
          toast("站点名称已保存");
          break;
        }
        case "use-template":
          document.querySelector("#namingForm textarea").value = target.dataset.template;
          break;
        case "toggle-play":
          togglePlay();
          break;
        case "prev":
          prev();
          break;
        case "next":
          next();
          break;
        case "toggle-shuffle":
          toggleShuffle();
          break;
        case "cycle-repeat":
          cycleRepeat();
          break;
        case "toggle-mute":
          toggleMute();
          break;
        case "toggle-queue": {
          const panel = document.getElementById("queuePanel");
          panel.hidden = !panel.hidden;
          break;
        }
        case "queue-play":
          playQueueIndex(Number(target.dataset.index));
          break;
        case "queue-remove":
          removeFromQueue(Number(target.dataset.index));
          break;
        case "queue-clear":
          clearQueue();
          break;
        case "close-editor":
          closeEditor();
          break;
        case "fetch-lyrics":
          await fetchLyrics();
          break;
        case "rename-track":
          await renameTrack();
          break;
        case "search-candidates":
          await searchCandidates();
          break;
        case "apply-candidate":
          await applyCandidate(target);
          break;
        case "restore-version":
          await restoreVersion(Number(target.dataset.id), Number(target.dataset.version));
          break;
        case "remove-cover":
          await removeCover();
          break;
        case "delete-track": {
          if (!confirm("删除该曲目记录？（文件会移动到 data/trash）")) break;
          await api(`/api/tracks/${id}?file=1`, { method: "DELETE" });
          closeEditor();
          toast("已删除");
          ctx.refresh();
          break;
        }
        default:
          break;
      }
    } catch (err) {
      console.error(err);
      toast(err.message || "操作失败", "error");
    }
  });

  document.addEventListener("change", async (event) => {
    const roleSelect = event.target.closest('[data-action="set-role"]');
    if (roleSelect) {
      try {
        await api(`/api/users/${roleSelect.dataset.id}`, { method: "PATCH", body: { role: roleSelect.value } });
        toast("角色已更新");
      } catch (err) {
        toast(err.message, "error");
      }
    }
  });

  document.getElementById("scrim").addEventListener("click", () => closeEditor());

  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag)) return;
    if (event.code === "Space") {
      event.preventDefault();
      togglePlay();
    } else if (event.code === "ArrowRight" && event.shiftKey) {
      next();
    } else if (event.code === "ArrowLeft" && event.shiftKey) {
      prev();
    } else if (event.key === "Escape") {
      closeEditor();
    } else if (event.key === "/") {
      event.preventDefault();
      document.getElementById("globalSearch")?.focus();
    }
  });

  onEvent((event) => {
    if (event.type?.startsWith("task.")) {
      refreshTaskBadge();
      if (["task.log"].includes(event.type)) appendTaskLog(event);
      if (parseRoute().name === "tasks" && ["task.new", "task.status"].includes(event.type)) {
        debounce(() => ctx.refresh(), 400)();
      }
      if (event.type === "task.progress") {
        const bar = document.querySelector(`.task-row [data-task-progress="${event.task_id}"]`);
        if (bar) bar.style.width = `${event.total ? (event.done / event.total) * 100 : 100}%`;
      }
    } else if (event.type === "room.state") {
      onRoomState(event);
    } else if (["library.scanned", "track.deleted"].includes(event.type)) {
      refreshTaskBadge();
      const name = parseRoute().name;
      if (["library", "dashboard", "albums", "album", "artists", "artist"].includes(name)) {
        debounce(() => ctx.refresh({ keepScroll: true }), 500)();
      }
    }
  });
}

/* ---------------------------------------------------------------- helpers */
function toggleSelection(id, checked) {
  if (checked) state.selection.add(id);
  else state.selection.delete(id);
  const row = document.querySelector(`tr[data-track-id="${id}"]`);
  row?.classList.toggle("selected", checked);
  ctx.refresh({ keepScroll: true });
}

async function scanAll() {
  const libraries = state.libraries.length ? state.libraries : (await api("/api/libraries")).items;
  if (!libraries.length) {
    toast("请先在设置中添加媒体库", "warn");
    go("#/settings");
    return;
  }
  for (const lib of libraries) {
    await api(`/api/libraries/${lib.id}/scan`, { method: "POST", body: { force: false } });
  }
  toast(`已启动 ${libraries.length} 个媒体库扫描`);
  closeEditor();
  go("#/tasks");
}

async function scanLibrary(id, force) {
  await api(`/api/libraries/${id}/scan`, { method: "POST", body: { force: Boolean(force) } });
  toast("扫描任务已创建");
  closeEditor();
  go("#/tasks");
}

/* Navidrome caches "this artist has no art", so new files need a recompute.
   The backend queues it automatically; surface what happened (or why not). */
function reportNavidromeArtwork(info) {
  if (!info) return;
  if (info.started) {
    toast(`已自动触发 Navidrome 图像重算（任务 #${info.task_id}）`);
  } else if (info.enabled && Number(info.exported || 0) > 0 && info.reason) {
    toast(`Navidrome 未自动重算：${info.reason}`, "warn");
  }
}

async function scrapeUntagged() {
  const data = await api("/api/tracks?tagged=0&limit=2000");
  if (!data.items.length) return toast("没有需要刮削的曲目");
  await batchScrape(data.items.map((track) => track.id));
}

async function batchScrape(ids) {
  if (!ids.length) return toast("没有选中曲目", "warn");
  const result = await api("/api/tracks/batch", {
    method: "POST",
    body: { ids, action: "scrape" },
  });
  toast(`已创建刮削任务（${ids.length} 首）`);
  state.selection.clear();
  if (result.task_id) go("#/tasks");
}

document.addEventListener("DOMContentLoaded", boot);

window.addEventListener("error", (event) => {
  console.error(event.error || event.message);
  bootFailed(event.error || new Error(event.message));
});
window.addEventListener("unhandledrejection", (event) => {
  console.error(event.reason);
  bootFailed(event.reason || new Error("unhandled rejection"));
});

export { ctx };
