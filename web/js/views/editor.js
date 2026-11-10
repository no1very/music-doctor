/* Track editor drawer: tags, cover, lyrics, scraping candidates, history. */

import { api, esc, fmtDate, fmtDuration, openPanel, state, toast } from "../core.js";
import { cover } from "./common.js";

let currentTrack = null;
let pendingCover = null;

export async function openTrackEditor(trackId, tab = "tags") {
  const track = await api(`/api/tracks/${trackId}`);
  currentTrack = track;
  pendingCover = null;
  const versions = await api(`/api/tracks/${trackId}/versions`);
  const admin = state.user?.role === "admin";
  const canEdit = ["admin", "editor"].includes(state.user?.role);

  openPanel("editor", `
    <div class="drawer-head">
      <h3>${esc(track.title || "未命名曲目")}</h3>
      <button class="icon-btn" data-action="close-editor">✕</button>
    </div>
    <div class="drawer-body">
      <div class="cover-edit" style="margin-bottom:14px">
        <div id="coverPreview">${cover(track.cover_url, "", 96)}</div>
        <div>
          <div class="muted tiny">${esc(track.rel_path || track.path)}</div>
          <div class="muted tiny">${esc((track.codec || "").toUpperCase())} · ${track.bitrate || 0} kbps · ${fmtDuration(track.duration)} · ${(track.size / 1048576).toFixed(1)} MB</div>
          <div class="toolbar" style="margin-top:8px">
            ${canEdit ? '<label class="mini" style="cursor:pointer">更换封面<input type="file" id="coverFile" accept="image/*" style="display:none" /></label>' : ""}
            ${canEdit && track.cover_url ? '<button class="mini" data-action="remove-cover">移除封面</button>' : ""}
            <button class="mini" data-action="play-track" data-id="${track.id}">播放</button>
            ${!track.writable ? '<span class="badge warn">只读格式</span>' : ""}
          </div>
        </div>
      </div>

      <div class="tabs">
        ${[["tags", "标签"], ["lyrics", "歌词"], ["scrape", "刮削"], ["history", `历史 (${versions.items.length})`]]
          .map(([key, label]) => `<button class="${tab === key ? "active" : ""}" data-action="editor-tab" data-tab="${key}">${label}</button>`).join("")}
      </div>

      <div data-tab-body="tags" ${tab === "tags" ? "" : "hidden"}>
        <form id="trackForm" data-id="${track.id}">
          <div class="editor-grid">
            <label class="field full">标题<input type="text" name="title" value="${esc(track.title)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">艺术家<input type="text" name="artist" value="${esc(track.artist)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">专辑艺术家<input type="text" name="albumartist" value="${esc(track.albumartist)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">专辑<input type="text" name="album" value="${esc(track.album)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">流派<input type="text" name="genre" value="${esc(track.genre)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">音轨号<input name="track_no" type="number" min="0" value="${track.track_no ?? ""}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">CD 号<input name="disc_no" type="number" min="0" value="${track.disc_no ?? ""}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">年份<input name="year" type="number" min="0" value="${track.year ?? ""}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">BPM<input name="bpm" type="number" min="0" value="${track.bpm ?? ""}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">作曲<input type="text" name="composer" value="${esc(track.composer)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">作词<input type="text" name="lyricist" value="${esc(track.lyricist)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">ISRC<input type="text" name="isrc" value="${esc(track.isrc)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field">注释<input type="text" name="comment" value="${esc(track.comment)}" ${canEdit ? "" : "disabled"} /></label>
            <label class="field full">歌词<textarea name="lyrics" rows="6" ${canEdit ? "" : "disabled"}>${esc(track.lyrics)}</textarea></label>
          </div>
          <div class="toolbar" style="margin-top:14px">
            ${canEdit ? '<button class="btn primary" type="submit">保存到文件</button>' : ""}
            <button class="btn" type="button" data-action="fetch-lyrics">在线获取歌词</button>
            <button class="btn" type="button" data-action="rename-track">按模板重命名</button>
            <div class="spacer"></div>
            ${admin ? '<button class="btn danger" type="button" data-action="delete-track">删除</button>' : ""}
          </div>
        </form>
        <dl class="kv" style="margin-top:16px">
          <dt>专辑 ID</dt><dd><code>${esc(track.album_id)}</code></dd>
          <dt>同步状态</dt><dd>${track.tagged ? "已刮削" : "未刮削"}</dd>
          <dt>播放次数</dt><dd>${track.play_count || 0}</dd>
        </dl>
      </div>

      <div data-tab-body="lyrics" ${tab === "lyrics" ? "" : "hidden"}>
        <div class="toolbar">
          <button class="btn" data-action="fetch-lyrics">从 LRCLIB / 网易云获取</button>
        </div>
        <pre class="log" style="max-height:520px">${esc(track.lyrics || "暂无歌词")}</pre>
      </div>

      <div data-tab-body="scrape" ${tab === "scrape" ? "" : "hidden"}>
        <div class="toolbar">
          <button class="btn primary" data-action="search-candidates">搜索匹配</button>
          <span class="muted tiny">按当前标题/艺术家/专辑匹配，取最高分候选</span>
        </div>
        <div id="candidates" class="muted">尚未搜索</div>
      </div>

      <div data-tab-body="history" ${tab === "history" ? "" : "hidden"}>
        <div id="versionList">
          ${versions.items.length ? versions.items.map(versionRow).join("") : '<div class="muted">暂无修改记录</div>'}
        </div>
      </div>
    </div>`);

  const fileInput = document.getElementById("coverFile");
  fileInput?.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) return toast("封面不能超过 8MB", "warn");
    const reader = new FileReader();
    reader.onload = () => {
      pendingCover = { dataUrl: String(reader.result), mime: file.type || "image/jpeg" };
      document.getElementById("coverPreview").innerHTML = `<img src="${pendingCover.dataUrl}" style="width:96px;height:96px;border-radius:10px;object-fit:cover" />`;
    };
    reader.readAsDataURL(file);
  });

  document.getElementById("trackForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const body = {};
    new FormData(form).forEach((value, key) => {
      body[key] = value;
    });
    if (pendingCover) {
      body.cover_base64 = pendingCover.dataUrl.split(",")[1];
      body.cover_mime = pendingCover.mime;
    }
    if (state.meta.removeCover) body.remove_cover = true;
    try {
      const updated = await api(`/api/tracks/${track.id}`, { method: "PATCH", body });
      toast("标签已写入文件");
      state.meta.removeCover = false;
      pendingCover = null;
      currentTrack = updated;
      window.dispatchEvent(new CustomEvent("refresh-view"));
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.querySelectorAll("[data-action='editor-tab']").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-action='editor-tab']").forEach((el) => el.classList.toggle("active", el === button));
      document.querySelectorAll("[data-tab-body]").forEach((el) => {
        el.hidden = el.dataset.tabBody !== button.dataset.tab;
      });
    });
  });
}

function versionRow(version) {
  const changed = Object.keys(version.after || {}).filter(
    (key) => JSON.stringify((version.before || {})[key]) !== JSON.stringify(version.after[key]),
  );
  return `
    <div class="version-row">
      <span class="badge">${esc(version.action)}</span>
      <span class="muted tiny" style="min-width:120px">${esc(fmtDate(version.created_at))} · ${esc(version.username || "系统")}</span>
      <span class="v-diff" title="${esc(changed.join(", "))}">${esc(changed.slice(0, 4).join(", ") || "—")}</span>
      <button class="mini" data-action="restore-version" data-id="${version.track_id}" data-version="${version.id}">回退</button>
    </div>`;
}

export async function searchCandidates() {
  if (!currentTrack) return;
  const host = document.getElementById("candidates");
  host.innerHTML = '<div class="muted">搜索中…</div>';
  try {
    const data = await api(`/api/tracks/${currentTrack.id}/candidates`);
    if (!data.items.length) {
      host.innerHTML = '<div class="empty">没有找到候选，检查网络或标签来源设置</div>';
      return;
    }
    host.innerHTML = data.items.map((candidate, index) => candidateCard(candidate, index)).join("");
  } catch (err) {
    host.innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
}

function candidateCard(candidate, index) {
  return `
    <div class="candidate" data-candidate-index="${index}">
      ${cover(candidate.cover_url, "", 54)}
      <div class="c-meta">
        <div><strong>${esc(candidate.title || "")}</strong></div>
        <div class="muted tiny">${esc(candidate.artist || "")}${candidate.album ? ` · ${esc(candidate.album)}` : ""}${candidate.year ? ` · ${candidate.year}` : ""}</div>
        <div class="muted tiny">来源 ${esc(candidate.provider)} · <span class="score">匹配度 ${(Number(candidate.score) * 100).toFixed(0)}%</span></div>
      </div>
      <button class="btn sm primary" data-action="apply-candidate" data-index="${index}" data-candidate='${esc(JSON.stringify(candidate))}'>应用</button>
    </div>`;
}

export async function applyCandidate(button) {
  const candidate = JSON.parse(button.dataset.candidate);
  const trackId = currentTrack?.id || Number(button.closest("[data-track-id]")?.dataset.trackId);
  if (!trackId) return;
  button.disabled = true;
  button.textContent = "应用中…";
  try {
    await api(`/api/tracks/${trackId}/scrape`, {
      method: "POST",
      body: { candidate, with_cover: true, with_lyrics: true },
    });
    toast("已应用候选标签");
    await openTrackEditor(trackId, "tags");
    window.dispatchEvent(new CustomEvent("refresh-view"));
  } catch (err) {
    toast(err.message, "error");
    button.disabled = false;
    button.textContent = "应用";
  }
}

export async function fetchLyrics() {
  if (!currentTrack) return;
  try {
    const updated = await api(`/api/tracks/${currentTrack.id}/lyrics`, { method: "POST", body: {} });
    toast("歌词已更新");
    await openTrackEditor(updated.id, "lyrics");
  } catch (err) {
    toast(err.message, "error");
  }
}

export async function renameTrack() {
  if (!currentTrack) return;
  const settings = await api("/api/settings");
  const template = window.prompt("重命名模板", settings.items["naming.template"] || state.settings["naming.template"] || "");
  if (!template) return;
  try {
    const updated = await api(`/api/tracks/${currentTrack.id}/rename`, { method: "POST", body: { template } });
    toast(`已重命名为 ${updated.path.split(/[\\/]/).pop()}`);
    await openTrackEditor(currentTrack.id);
    window.dispatchEvent(new CustomEvent("refresh-view"));
  } catch (err) {
    toast(err.message, "error");
  }
}

export async function removeCover() {
  if (!currentTrack) return;
  state.meta.removeCover = true;
  document.getElementById("coverPreview").innerHTML = '<div class="cover-ph" style="width:96px;height:96px;border-radius:10px">♪</div>';
  toast("保存后移除封面", "info");
}

export async function restoreVersion(trackId, versionId) {
  try {
    await api(`/api/tracks/${trackId}/versions/${versionId}/restore`, { method: "POST", body: {} });
    toast("已回退到该版本");
    await openTrackEditor(trackId, "history");
    window.dispatchEvent(new CustomEvent("refresh-view"));
  } catch (err) {
    toast(err.message, "error");
  }
}
