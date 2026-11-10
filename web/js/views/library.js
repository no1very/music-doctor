/* Dashboard, library browser, album and artist pages. */

import { api, closeEditor, esc, go, openPanel, state, toast } from "../core.js";
import { playQueue, enqueue } from "../player.js";
import {
  albumGrid,
  cover,
  empty,
  fmtRange,
  pageHead,
  pager,
  statCard,
  trackTable,
} from "./common.js";

/* ------------------------------------------------------------- dashboard */
/**
 * Search-as-you-type that behaves: waits until the user stops typing, never
 * fires in the middle of an IME composition (Chinese input), and lets the view
 * refresh without stealing focus (see renderRoute in ./index.js).
 */
function bindLiveSearch(input, apply, ctx, delay = 600) {
  if (!input) return;
  let timer;
  let composing = false;
  let lastValue = input.value.trim();

  const commit = () => {
    const value = input.value.trim();
    if (value === lastValue) return;
    lastValue = value;
    apply(value);
    ctx.refresh({ keepScroll: true });
  };
  const schedule = () => {
    if (composing) return;
    clearTimeout(timer);
    timer = setTimeout(commit, delay);
  };

  input.addEventListener("compositionstart", () => {
    composing = true;
    clearTimeout(timer);
  });
  input.addEventListener("compositionend", () => {
    composing = false;
    schedule();
  });
  input.addEventListener("input", schedule);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      composing = false;
      clearTimeout(timer);
      commit();
    } else if (event.key === "Escape") {
      clearTimeout(timer);
      input.value = "";
      commit();
    }
  });
}

export async function dashboard(root, _route, ctx) {
  const [stats, albums, untagged] = await Promise.all([
    api("/api/stats"),
    api("/api/albums?sort=added&limit=10"),
    api("/api/tracks?tagged=0&limit=5"),
  ]);
  const playing = stats.tasks_running
    ? `<span class="task-badge" data-action="open-tasks">${stats.tasks_running} 个任务进行中</span>`
    : "";
  root.innerHTML = `
    ${pageHead("仪表盘", `媒体库概览 · 共 ${fmtRange(stats.duration)} 音乐`, `
      <button class="btn" data-action="open-scan">扫描媒体库</button>
      <button class="btn primary" data-action="scrape-untagged">刮削未完成 (${stats.untagged})</button>`)}
    <div class="cards">
      ${statCard("曲目", stats.tracks.toLocaleString(), `${fmtRange(stats.duration)} · ${(stats.size / 1e9).toFixed(1)} GB`)}
      ${statCard("专辑", stats.albums.toLocaleString(), "按专辑艺术家聚合")}
      ${statCard("艺术家", stats.artists.toLocaleString(), `${stats.libraries} 个媒体库`)}
      ${statCard("未刮削", stats.untagged.toLocaleString(), "缺少标题/艺术家/专辑")}
      ${statCard("重复文件", stats.duplicates.toLocaleString(), "标题+艺术家+时长近似")}
      ${statCard("播放记录", stats.plays.toLocaleString(), stats.missing ? `${stats.missing} 个文件缺失` : "文件全部在线")}
    </div>
    ${playing}
    <div class="section">
      <div class="section-head"><h2>最近入库</h2><a class="muted tiny" href="#/albums">查看全部专辑 →</a></div>
      ${albumGrid(albums.items)}
    </div>
    <div class="grid-2">
      <div class="section">
        <div class="section-head"><h2>待刮削</h2><span class="muted tiny">${untagged.total} 首</span></div>
        ${untagged.items.length ? `<div class="table-wrap"><table class="data"><tbody>
          ${untagged.items.map((track) => `
            <tr><td><div class="t-title">${cover(track.cover_url, "t-cover", 36)}
              <div class="t-main"><div class="t-name"><a href="#" data-action="edit-track" data-id="${track.id}">${esc(track.title)}</a></div>
              <div class="t-sub">${esc(track.rel_path || "")}</div></div></div></td>
              <td style="width:90px"><button class="mini" data-action="scrape-track" data-id="${track.id}">刮削</button></td></tr>`).join("")}
        </tbody></table></div>` : empty("所有歌曲都已完成刮削 🎉", "✓")}
      </div>
      <div class="section">
        <div class="section-head"><h2>最近播放</h2><a class="muted tiny" href="#/library">进入音乐库 →</a></div>
        ${stats.recent_plays.length ? `<div class="table-wrap"><table class="data"><tbody>
          ${stats.recent_plays.map((row) => `
            <tr><td><div class="t-title">${cover(row.cover_hash ? `/api/covers/${row.cover_hash}` : "", "t-cover", 36)}
              <div class="t-main"><div class="t-name">${esc(row.title)}</div>
              <div class="t-sub">${esc(row.artist || "")} · ${esc(row.username || "")} · ${fmtDateShort(row.played_at)}</div></div></div></td>
              <td style="width:50px"><button class="icon-btn" data-action="play-track" data-id="${row.id}">▶</button></td></tr>`).join("")}
        </tbody></table></div>` : empty("还没有播放记录", "▶")}
      </div>
    </div>`;
  void ctx;
}

function fmtDateShort(value) {
  if (!value) return "";
  const date = new Date(String(value).replace(" ", "T"));
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/* --------------------------------------------------------------- library */
export async function library(root, _route, ctx) {
  const { filters, page, selection } = state;
  const params = new URLSearchParams({
    limit: String(page.limit),
    offset: String(page.offset),
    sort: filters.sort,
    order: filters.order,
  });
  if (filters.q) params.set("q", filters.q);
  if (filters.album_id) params.set("album_id", filters.album_id);
  if (filters.artist_id) params.set("artist_id", filters.artist_id);
  if (filters.only === "untagged") params.set("tagged", "0");
  if (filters.only === "favorites") params.set("favorites_only", "1");
  // "最近添加" has no dedicated backend flag: it is the added-at sort, newest first.
  if (filters.only === "recent") {
    params.set("sort", "added");
    params.set("order", "desc");
  }

  const data = await api(`/api/tracks?${params}`);
  state.tracks = data.items;
  const allChecked = data.items.length > 0 && data.items.every((track) => selection.has(track.id));
  const selectionCount = selection.size;

  root.innerHTML = `
    ${pageHead("音乐库", `${data.total.toLocaleString()} 首曲目${filters.album_id || filters.artist_id ? " · 已筛选" : ""}`, `
      <button class="btn" data-action="open-scan">扫描媒体库</button>
      <button class="btn primary" data-action="scrape-untagged">批量刮削</button>`)}
    <div class="toolbar">
      <input id="libSearch" type="search" placeholder="在音乐库中搜索…" value="${esc(filters.q)}" style="max-width:280px" />
      <select id="libSort" style="width:auto">
        ${[["title", "标题"], ["artist", "艺术家"], ["album", "专辑"], ["added", "入库时间"], ["year", "年份"], ["duration", "时长"], ["plays", "播放次数"]]
          .map(([value, label]) => `<option value="${value}" ${filters.sort === value ? "selected" : ""}>排序：${label}</option>`).join("")}
        <option value="__desc" ${filters.order === "desc" ? "selected" : ""}>方向：降序</option>
      </select>
      <div class="chips">
        ${[["", "全部"], ["untagged", "未刮削"], ["favorites", "我的收藏"], ["recent", "最近添加"]]
          .map(([value, label]) => `<button class="chip ${filters.only === value ? "on" : ""}" data-action="filter" data-only="${value}">${label}</button>`).join("")}
      </div>
      <div class="spacer"></div>
      <label class="muted tiny"><input type="checkbox" data-action="select-all" ${allChecked ? "checked" : ""} /> 全选本页</label>
    </div>
    ${selectionCount ? `
      <div class="batchbar">
        <strong>已选 ${selectionCount} 首</strong>
        <button class="btn sm" data-action="batch-scrape">刮削</button>
        <button class="btn sm" data-action="batch-edit">批量改标签</button>
        <button class="btn sm" data-action="batch-rename">批量重命名</button>
        <button class="btn sm" data-action="batch-reindex">重新读取</button>
        ${state.user?.role === "admin" ? '<button class="btn sm danger" data-action="batch-delete">删除</button>' : ""}
        <div class="spacer"></div>
        <button class="mini" data-action="clear-selection">取消选择</button>
      </div>` : ""}
    ${trackTable(data.items)}
    ${pager(data.total, data.offset, data.limit)}`;

  // apply selection highlighting after render (server-side template keeps it simple)
  root.querySelectorAll("tbody tr").forEach((row) => {
    if (selection.has(Number(row.dataset.trackId))) row.classList.add("selected");
  });

  const search = document.getElementById("libSearch");
  bindLiveSearch(search, (value) => {
    filters.q = value;
    page.offset = 0;
  }, ctx);
  document.getElementById("libSort")?.addEventListener("change", (event) => {
    if (event.target.value === "__desc") {
      filters.order = filters.order === "desc" ? "asc" : "desc";
    } else {
      filters.sort = event.target.value;
      filters.order = event.target.value === "added" ? "desc" : "asc";
    }
    page.offset = 0;
    ctx.refresh();
  });
}

/* ----------------------------------------------------------------- albums */
export async function albums(root, _route, ctx) {
  const data = await api(`/api/albums?limit=500&sort=${state.filters.albumSort || "name"}`);
  root.innerHTML = `
    ${pageHead("专辑", `${data.total.toLocaleString()} 张专辑`, `
      <button class="btn" data-action="album-sort" data-sort="name">按名称</button>
      <button class="btn" data-action="album-sort" data-sort="artist">按艺术家</button>
      <button class="btn" data-action="album-sort" data-sort="year">按年份</button>`)}
    ${albumGrid(data.items)}`;
  void ctx;
}

export async function albumDetail(root, route, ctx) {
  const album = await api(`/api/albums/${route.id}`);
  const tracks = album.tracks || [];
  root.innerHTML = `
    <div class="hero">
      ${cover(album.cover_url, "art")}
      <div class="meta">
        <div class="muted tiny">专辑</div>
        <h1>${esc(album.name)}</h1>
        <div class="muted">${esc(album.artist || "未知艺术家")} · ${album.year || "年份未知"} · ${album.track_count} 首 · ${fmtRange(album.duration)}</div>
        <div class="actions">
          <button class="btn primary" data-action="play-album" data-id="${album.album_id}">▶ 播放专辑</button>
          <button class="btn" data-action="enqueue-album" data-id="${album.album_id}">加入队列</button>
          <button class="btn" data-action="scrape-album" data-id="${album.album_id}">刮削整张专辑</button>
        </div>
      </div>
    </div>
    ${trackTable(tracks)}`;
  void ctx;
}

/* ---------------------------------------------------------------- artists */
export async function artists(root, _route, ctx) {
  const { filters } = state;
  const params = new URLSearchParams({
    limit: "1000",
    sort: filters.artistSort || "name",
    order: (filters.artistSort || "name") === "name" ? "asc" : "desc",
  });
  if (filters.artistQ) params.set("q", filters.artistQ);
  const data = await api(`/api/artists?${params}`);
  const canEdit = ["admin", "editor"].includes(state.user?.role);
  root.innerHTML = `
    ${pageHead("艺术家", `${data.total.toLocaleString()} 位艺术家 · 按姓名/歌曲数/专辑数浏览`, canEdit
      ? `<button class="btn" data-action="export-artist-images">导出图片到 Navidrome</button>
         <button class="btn" data-action="scrape-all-artists">刮削全部艺术家</button>` : "")}
    <div class="toolbar">
      <input id="artistSearch" type="search" placeholder="搜索艺术家…" value="${esc(filters.artistQ)}" style="max-width:280px" />
      <select id="artistSort" style="width:auto">
        ${[["name", "按姓名"], ["tracks", "按歌曲数"], ["albums", "按专辑数"]]
          .map(([value, label]) => `<option value="${value}" ${(filters.artistSort || "name") === value ? "selected" : ""}>${label}</option>`).join("")}
      </select>
      <span class="muted tiny">共 ${data.total} 位</span>
    </div>
    ${data.items.length ? `<div class="album-grid">
      ${data.items.map((artist) => `
        <div class="album-card artist-card" data-action="open-artist" data-id="${artist.artist_id}">
          <div class="cover-wrap">${cover(artist.image_url || artist.cover_url, "")}</div>
          <div class="t">${esc(artist.name || "未知艺术家")}</div>
          <div class="s">${artist.track_count} 首歌曲 · ${artist.album_count} 张专辑</div>
        </div>`).join("")}
    </div>` : empty(filters.artistQ ? "没有匹配的艺术家" : "还没有艺术家", "☺")}`;

  const search = document.getElementById("artistSearch");
  bindLiveSearch(search, (value) => {
    filters.artistQ = value;
  }, ctx);
  document.getElementById("artistSort")?.addEventListener("change", (event) => {
    filters.artistSort = event.target.value;
    ctx.refresh();
  });
}

export async function artistDetail(root, route, ctx) {
  const artist = await api(`/api/artists/${route.id}`);
  const tracks = artist.tracks || [];
  const albums = artist.albums || [];
  const canEdit = ["admin", "editor"].includes(state.user?.role);
  const aliases = artist.aliases || [];
  root.innerHTML = `
    <div class="hero">
      ${cover(artist.image_url || artist.cover_url, "art round")}
      <div class="meta">
        <div class="muted tiny">艺术家</div>
        <h1>${esc(artist.name || "未知艺术家")}</h1>
        <div class="muted">${tracks.length} 首歌曲 · ${artist.album_count} 张专辑 · ${fmtRange(artist.duration)}</div>
        ${aliases.length ? `<div class="chips" style="margin-top:8px">${aliases.map((alias) => `<span class="chip">${esc(alias)}</span>`).join("")}</div>` : ""}
        <div class="actions">
          <button class="btn primary" data-action="play-artist" data-id="${artist.artist_id}">▶ 播放全部</button>
          <button class="btn" data-action="enqueue-artist" data-id="${artist.artist_id}">加入队列</button>
          ${canEdit ? `<button class="btn" data-action="scrape-artist" data-id="${artist.artist_id}">刮削艺术家资料</button>` : ""}
        </div>
      </div>
    </div>

    ${artist.bio ? `<div class="panel" style="margin-bottom:18px">
      <h3>简介 <span class="muted tiny">${esc(artist.meta_source || "")}${artist.meta_updated_at ? " · " + esc(artist.meta_updated_at.slice(0, 10)) : ""}</span></h3>
      <p class="muted bio">${esc(artist.bio)}</p>
    </div>` : ""}

    <div class="section">
      <div class="section-head"><h2>歌曲</h2><span class="muted tiny">${tracks.length} 首</span></div>
      ${tracks.length ? trackTable(tracks) : empty("这位艺术家还没有可播放的歌曲", "♫")}
    </div>

    <div class="section">
      <div class="section-head"><h2>专辑</h2><span class="muted tiny">${albums.length} 张</span></div>
      ${albums.length ? albumGrid(albums) : empty("还没有专辑", "▣")}
    </div>`;
  void ctx;
}

/* ----------------------------------------------------- artist scraping UI */
export async function openArtistScrape(artistId) {
  const artist = await api(`/api/artists/${artistId}`);
  openPanel("editor", `
    <div class="drawer-head"><h3>刮削艺术家：${esc(artist.name || "")}</h3>
      <button class="icon-btn" data-action="close-editor">✕</button></div>
    <div class="drawer-body">
      <p class="muted tiny">从网易云 / 维基百科 / Deezer 抓取头像、别名与简介；应用后会替换当前资料。</p>
      <div id="artistCandidates" class="muted" style="margin-top:12px">搜索中…</div>
    </div>`);
  const host = document.getElementById("artistCandidates");
  try {
    const data = await api(`/api/artists/${artistId}/candidates`);
    if (!data.items.length) {
      host.innerHTML = '<div class="empty">没有找到匹配的艺术家资料（检查网络或设置里的艺术家来源）</div>';
      return;
    }
    host.innerHTML = data.items.map((candidate) => `
      <div class="candidate">
        ${candidate.image_url ? `<img src="${esc(candidate.image_url)}" alt="" loading="lazy" />` : '<div class="thumb cover-ph" style="width:54px;height:54px">☺</div>'}
        <div class="c-meta">
          <div><strong>${esc(candidate.name)}</strong></div>
          <div class="muted tiny">${esc((candidate.aliases || []).join(" / "))}</div>
          <div class="muted tiny">${esc((candidate.bio || "").slice(0, 90))}</div>
          <div class="muted tiny">来源 ${esc(candidate.source)} · <span class="score">匹配度 ${(Number(candidate.score) * 100).toFixed(0)}%</span>${candidate.album_size ? ` · ${candidate.album_size} 张专辑` : ""}</div>
        </div>
        <button class="btn sm primary" data-action="apply-artist-candidate"
          data-artist="${esc(artistId)}" data-candidate="${esc(JSON.stringify(candidate))}">应用</button>
      </div>`).join("");
  } catch (err) {
    host.innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
}

export async function applyArtistCandidate(button) {
  const candidate = JSON.parse(button.dataset.candidate);
  button.disabled = true;
  button.textContent = "应用中…";
  try {
    const result = await api(`/api/artists/${button.dataset.artist}/scrape`, {
      method: "POST",
      body: { candidate },
    });
    closeEditor();
    toast(result.navidrome?.started
      ? `艺术家资料已更新，已自动触发 Navidrome 图像重算（任务 #${result.navidrome.task_id}）`
      : "艺术家资料已更新");
    window.dispatchEvent(new CustomEvent("refresh-view"));
  } catch (err) {
    toast(err.message, "error");
    button.disabled = false;
    button.textContent = "应用";
  }
}

/* ---------------------------------------------------------------- helpers */
export async function playAlbumById(albumId, append = false) {
  const album = await api(`/api/albums/${albumId}`);
  if (!album.tracks?.length) return toast("专辑中没有歌曲", "warn");
  if (append) enqueue(album.tracks);
  else playQueue(album.tracks, 0);
  go("#/album/" + albumId);
}

export async function playArtistById(artistId) {
  const artist = await api(`/api/artists/${artistId}`);
  const tracks = artist.tracks || [];
  if (!tracks.length) return toast("这位艺术家没有可播放的歌曲", "warn");
  playQueue(tracks, 0);
}

export async function enqueueArtistById(artistId) {
  const artist = await api(`/api/artists/${artistId}`);
  const tracks = artist.tracks || [];
  if (!tracks.length) return toast("这位艺术家没有可播放的歌曲", "warn");
  enqueue(tracks);
}

export function clearSelectionAndRefresh(ctx) {
  state.selection.clear();
  ctx.refresh();
}
