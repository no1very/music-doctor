/* Shared view fragments. */

import { esc, fmtDuration, fmtSize } from "../core.js";

export function cover(url, className = "", size = "") {
  if (url) return `<img class="${className}" src="${esc(url)}" alt="" loading="lazy" ${size ? `width="${size}" height="${size}"` : ""} />`;
  return `<div class="${className} cover-ph" ${size ? `style="width:${size}px;height:${size}px"` : ""}>♪</div>`;
}

export function pageHead(title, sub, actions = "") {
  return `
    <div class="page-head">
      <div><h1>${esc(title)}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div>
      <div class="page-actions">${actions}</div>
    </div>`;
}

export function empty(text, icon = "♪") {
  return `<div class="empty"><div class="big">${icon}</div>${esc(text)}</div>`;
}

export function pager(total, offset, limit) {
  if (!total) return "";
  const from = total ? offset + 1 : 0;
  const to = Math.min(offset + limit, total);
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return `
    <div class="pager">
      <span>${from}–${to} / ${total} · 第 ${page}/${pages} 页</span>
      <button class="mini" data-action="page" data-offset="0" ${page === 1 ? "disabled" : ""}>«</button>
      <button class="mini" data-action="page" data-offset="${Math.max(0, offset - limit)}" ${page === 1 ? "disabled" : ""}>上一页</button>
      <button class="mini" data-action="page" data-offset="${offset + limit}" ${to >= total ? "disabled" : ""}>下一页</button>
    </div>`;
}

export function qualityBadge(track) {
  const codec = (track.codec || track.ext || "").replace(".", "").toUpperCase();
  const cls = codec === "FLAC" ? "flac" : codec === "MP3" ? "mp3" : "";
  return `<span class="badge ${cls}">${esc(codec)}</span>`;
}

export function trackRow(track, index, { selected = false, showAlbum = true, compact = false } = {}) {
  return `
    <tr data-track-id="${track.id}" class="${selected ? "selected" : ""}">
      <td style="width:28px"><input type="checkbox" data-action="select-track" data-id="${track.id}" ${selected ? "checked" : ""} /></td>
      <td>
        <div class="t-title">
          <button class="icon-btn" data-action="play-track" data-id="${track.id}" title="播放" style="width:26px;height:26px">▶</button>
          ${cover(track.cover_url, "t-cover", 36)}
          <div class="t-main">
            <div class="t-name"><a href="#/track/${track.id}" data-action="edit-track" data-id="${track.id}">${esc(track.title || "未知标题")}</a></div>
            <div class="t-sub"><a href="#/artist/${track.artist_id}" data-action="open-artist" data-id="${track.artist_id}">${esc(track.artist || "未知艺术家")}</a>${showAlbum && track.album ? ` · ${esc(track.album)}` : ""}</div>
          </div>
        </div>
      </td>
      ${compact ? "" : `<td class="num">${track.track_no ? String(track.track_no).padStart(2, "0") : "—"}</td>`}
      <td class="num">${fmtDuration(track.duration)}</td>
      ${compact ? "" : `<td class="num">${track.bitrate ? `${track.bitrate}k` : "—"}</td>`}
      <td>${qualityBadge(track)}</td>
      <td class="num" style="white-space:nowrap">
        <span class="muted tiny">${track.play_count ? `▶ ${track.play_count}` : ""}</span>
        <div class="row-actions" style="display:inline-flex">
          <button class="icon-btn" data-action="toggle-favorite" data-id="${track.id}" title="收藏">${track.favorite ? "♥" : "♡"}</button>
          <button class="icon-btn" data-action="enqueue" data-id="${track.id}" title="加入队列">＋</button>
          <button class="icon-btn" data-action="edit-track" data-id="${track.id}" title="编辑">✎</button>
        </div>
      </td>
    </tr>`;
}

export function trackTable(tracks, options = {}) {
  if (!tracks.length) return empty("这里还没有歌曲", "♫");
  return `
    <div class="table-wrap">
      <table class="data">
        <thead><tr>
          <th></th><th>标题</th>
          ${options.compact ? "" : '<th class="num">#</th>'}
          <th class="num">时长</th>
          ${options.compact ? "" : '<th class="num">码率</th>'}
          <th>格式</th><th></th>
        </tr></thead>
        <tbody>${tracks.map((track, index) => trackRow(track, index, options)).join("")}</tbody>
      </table>
    </div>`;
}

export function statCard(label, value, hint = "") {
  return `<div class="stat"><div class="label">${esc(label)}</div>
    <div class="value">${esc(value)}</div>${hint ? `<div class="hint">${esc(hint)}</div>` : ""}</div>`;
}

export function albumCard(album) {
  return `
    <div class="album-card" data-action="open-album" data-id="${album.album_id}">
      <div class="cover-wrap">
        ${cover(album.cover_url, "")}
        <button class="play-fab" data-action="play-album" data-id="${album.album_id}" title="播放专辑">▶</button>
      </div>
      <div class="t">${esc(album.name || "未知专辑")}</div>
      <div class="s">${esc(album.artist || "未知艺术家")}${album.year ? ` · ${album.year}` : ""}</div>
    </div>`;
}

export function albumGrid(albums) {
  if (!albums.length) return empty("没有匹配的专辑", "▣");
  return `<div class="album-grid">${albums.map(albumCard).join("")}</div>`;
}

export function fmtRange(seconds) {
  const hours = Math.floor((Number(seconds) || 0) / 3600);
  if (hours >= 1) return `${hours} 小时 ${Math.round(((Number(seconds) || 0) % 3600) / 60)} 分`;
  return `${Math.round((Number(seconds) || 0) / 60)} 分钟`;
}

export function sizeText(bytes) {
  return fmtSize(bytes);
}
