/* Playlists: static collections plus rule-driven smart playlists. */

import { api, esc, go, openPanel, toast } from "../core.js";
import { playQueue, enqueue } from "../player.js";
import { empty, pageHead, trackTable } from "./common.js";

export const RULE_FIELDS = [
  ["title", "标题"], ["artist", "艺术家"], ["album", "专辑"], ["albumartist", "专辑艺术家"],
  ["genre", "流派"], ["year", "年份"], ["play_count", "播放次数"], ["rating", "评分"],
  ["duration", "时长(秒)"], ["bitrate", "码率"], ["codec", "编码"], ["added", "入库时间"],
  ["last_played", "最近播放"], ["composer", "作曲"], ["format", "格式"],
];

export const RULE_OPS = [
  ["contains", "包含"], ["not_contains", "不包含"], ["equals", "等于"], ["not_equals", "不等于"],
  ["starts_with", "开头是"], ["ends_with", "结尾是"], ["gt", "大于"], ["gte", "大于等于"],
  ["lt", "小于"], ["lte", "小于等于"], ["between", "介于"], ["in", "属于"], ["is_set", "有值"],
  ["is_not_set", "无值"], ["before", "早于"], ["after", "晚于"],
];

export const SORTS = [
  ["title", "标题"], ["artist", "艺术家"], ["album", "专辑"], ["added", "入库时间"],
  ["year", "年份"], ["duration", "时长"], ["plays", "播放次数"], ["random", "随机"],
];

export async function playlists(root, _route, ctx) {
  const data = await api("/api/playlists");
  root.innerHTML = `
    ${pageHead("歌单", `${data.items.length} 个歌单 · 支持静态收藏与规则驱动的智能歌单`, `
      <button class="btn" data-action="new-smart-playlist">新建智能歌单</button>
      <button class="btn primary" data-action="new-playlist">新建歌单</button>`)}
    ${data.items.length ? `<div class="table-wrap"><table class="data">
      <thead><tr><th>名称</th><th>类型</th><th class="num">曲目</th><th>可见性</th><th>更新时间</th><th></th></tr></thead>
      <tbody>${data.items.map((item) => `
        <tr>
          <td><a href="#/playlist/${item.id}"><strong>${esc(item.name)}</strong></a>
            ${item.description ? `<div class="t-sub">${esc(item.description)}</div>` : ""}</td>
          <td><span class="badge ${item.kind === "smart" ? "flac" : ""}">${item.kind === "smart" ? "智能" : "静态"}</span></td>
          <td class="num">${item.kind === "smart" ? "动态" : item.count}</td>
          <td>${item.public ? "公开" : "私有"}</td>
          <td class="num">${esc((item.updated_at || "").slice(0, 16))}</td>
          <td style="white-space:nowrap">
            <button class="icon-btn" data-action="play-playlist" data-id="${item.id}" title="播放">▶</button>
            <button class="icon-btn" data-action="edit-playlist" data-id="${item.id}" title="编辑">✎</button>
            <button class="icon-btn" data-action="delete-playlist" data-id="${item.id}" title="删除">🗑</button>
          </td>
        </tr>`).join("")}</tbody>
    </table></div>` : empty("还没有歌单，创建一个吧", "☰")}`;
  void ctx;
}

export async function playlistDetail(root, route, ctx) {
  const playlist = await api(`/api/playlists/${route.id}`);
  const tracks = playlist.tracks || [];
  root.innerHTML = `
    <div class="hero">
      <div class="art cover-ph" style="display:grid;place-items:center;font-size:34px">${playlist.kind === "smart" ? "⚡" : "☰"}</div>
      <div class="meta">
        <div class="muted tiny">${playlist.kind === "smart" ? "智能歌单 · 动态更新" : "静态歌单"}</div>
        <h1>${esc(playlist.name)}</h1>
        <div class="muted">${tracks.length} 首 · ${playlist.public ? "公开" : "私有"}${playlist.description ? ` · ${esc(playlist.description)}` : ""}</div>
        <div class="actions">
          <button class="btn primary" data-action="play-playlist" data-id="${playlist.id}">▶ 播放</button>
          <button class="btn" data-action="enqueue-playlist" data-id="${playlist.id}">加入队列</button>
          <button class="btn" data-action="edit-playlist" data-id="${playlist.id}">编辑</button>
        </div>
      </div>
    </div>
    ${playlist.kind === "smart" ? renderRuleSummary(playlist.rules) : ""}
    ${trackTable(tracks)}`;
  void ctx;
}

function renderRuleSummary(rules) {
  const rows = rules?.rules || [];
  if (!rows.length) return "";
  const ops = Object.fromEntries(RULE_OPS);
  const fields = Object.fromEntries(RULE_FIELDS);
  return `<div class="panel" style="margin-bottom:16px">
    <h3>规则 <span class="muted tiny">${rules.match === "any" ? "任一匹配" : "全部匹配"} · 最多 ${rules.limit || 500} 首</span></h3>
    <div class="chips">${rows.map((rule) => `<span class="chip">${esc(fields[rule.field] || rule.field)} ${esc(ops[rule.op] || rule.op)} ${esc(
      Array.isArray(rule.value) ? rule.value.join(" ~ ") : rule.value ?? "",
    )}</span>`).join("")}</div>
  </div>`;
}

/* ------------------------------------------------------------- editor form */
export function openPlaylistEditor(playlist = null) {
  const kind = playlist?.kind || "static";
  const rules = playlist?.rules || { match: "all", rules: [], sort: "title", order: "asc", limit: 500 };
  openPanel("editor", `
    <div class="drawer-head"><h3>${playlist ? "编辑歌单" : "新建歌单"}</h3>
      <button class="icon-btn" data-action="close-editor">✕</button></div>
    <div class="drawer-body">
      <form id="playlistForm" data-id="${playlist?.id || ""}">
        <div class="editor-grid">
          <label class="field full">名称<input type="text" name="name" required value="${esc(playlist?.name || "")}" /></label>
          <label class="field full">描述<input type="text" name="description" value="${esc(playlist?.description || "")}" /></label>
          <label class="field">类型
            <select name="kind" id="plKind">
              <option value="static" ${kind === "static" ? "selected" : ""}>静态（手动添加）</option>
              <option value="smart" ${kind === "smart" ? "selected" : ""}>智能（按规则)</option>
            </select>
          </label>
          <label class="field">可见性
            <select name="public"><option value="0">私有</option><option value="1" ${playlist?.public ? "selected" : ""}>公开</option></select>
          </label>
        </div>
        <div id="ruleBox" style="margin-top:16px" class="${kind === "smart" ? "" : "hidden"}">
          <div class="toolbar" style="margin-bottom:8px">
            <label class="muted tiny">匹配
              <select name="match" style="width:auto">
                <option value="all" ${rules.match !== "any" ? "selected" : ""}>全部条件</option>
                <option value="any" ${rules.match === "any" ? "selected" : ""}>任一条件</option>
              </select>
            </label>
            <label class="muted tiny">排序
              <select name="ruleSort" style="width:auto">
                ${SORTS.map(([value, label]) => `<option value="${value}" ${rules.sort === value ? "selected" : ""}>${label}</option>`).join("")}
              </select>
            </label>
            <label class="muted tiny">方向
              <select name="ruleOrder" style="width:auto">
                <option value="asc" ${rules.order !== "desc" ? "selected" : ""}>升序</option>
                <option value="desc" ${rules.order === "desc" ? "selected" : ""}>降序</option>
              </select>
            </label>
            <label class="muted tiny">上限
              <input name="ruleLimit" type="number" min="1" max="2000" value="${rules.limit || 500}" style="width:80px" />
            </label>
            <div class="spacer"></div>
            <button type="button" class="mini" data-action="rule-add">+ 添加条件</button>
          </div>
          <div id="ruleRows">${(rules.rules?.length ? rules.rules : [null]).map(ruleRow).join("")}</div>
        </div>
        <div class="toolbar" style="margin-top:20px">
          <button class="btn primary" type="submit">保存</button>
          <button class="btn" type="button" data-action="close-editor">取消</button>
          ${playlist ? `<div class="spacer"></div><button class="btn danger" type="button" data-action="delete-playlist" data-id="${playlist.id}">删除歌单</button>` : ""}
        </div>
      </form>
    </div>`);
  document.getElementById("plKind").addEventListener("change", (event) => {
    document.getElementById("ruleBox").classList.toggle("hidden", event.target.value !== "smart");
  });
  document.getElementById("playlistForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await savePlaylist(event.target, playlist);
  });
}

export function ruleRow(rule) {
  const value = rule?.value ?? "";
  const op = rule?.op || "contains";
  const noValue = op === "is_set" || op === "is_not_set";
  return `
    <div class="rule-row" style="display:grid;grid-template-columns:1.1fr 1fr 1.4fr 34px;gap:8px;margin-bottom:8px">
      <select name="ruleField">
        ${RULE_FIELDS.map(([v, l]) => `<option value="${v}" ${rule?.field === v ? "selected" : ""}>${l}</option>`).join("")}
      </select>
      <select name="ruleOp">
        ${RULE_OPS.map(([v, l]) => `<option value="${v}" ${op === v ? "selected" : ""}>${l}</option>`).join("")}
      </select>
      <input type="text" name="ruleValue" placeholder="值" value="${esc(Array.isArray(value) ? value.join(",") : value)}" ${noValue ? "disabled" : ""} />
      <button type="button" class="icon-btn" data-action="rule-remove" title="删除条件">✕</button>
    </div>`;
}

async function savePlaylist(form, playlist) {
  const data = new FormData(form);
  const kind = data.get("kind");
  const rules = {
    match: data.get("match") || "all",
    sort: data.get("ruleSort") || "title",
    order: data.get("ruleOrder") || "asc",
    limit: Number(data.get("ruleLimit") || 500),
    rules: [],
  };
  form.querySelectorAll(".rule-row").forEach((row) => {
    const field = row.querySelector('[name="ruleField"]').value;
    let op = row.querySelector('[name="ruleOp"]').value;
    let value = row.querySelector('[name="ruleValue"]').value.trim();
    if (!value && op !== "is_set" && op !== "is_not_set") return;
    if (op === "between") value = value.split(/[,~]/).map((part) => part.trim()).slice(0, 2);
    if (op === "in") value = value.split(",").map((part) => part.trim()).filter(Boolean);
    rules.rules.push({ field, op, value });
  });
  const body = {
    name: data.get("name"),
    description: data.get("description"),
    public: data.get("public") === "1",
    kind,
    rules: kind === "smart" ? rules : {},
  };
  try {
    if (playlist) await api(`/api/playlists/${playlist.id}`, { method: "PATCH", body });
    else {
      const created = await api("/api/playlists", { method: "POST", body });
      toast("歌单已创建");
      go(`#/playlist/${created.id}`);
    }
    document.getElementById("editor").hidden = true;
    window.dispatchEvent(new CustomEvent("refresh-view"));
  } catch (err) {
    toast(err.message, "error");
  }
}

export async function playPlaylist(id, append = false) {
  const playlist = await api(`/api/playlists/${id}`);
  if (!playlist.tracks?.length) {
    toast("歌单是空的", "warn");
    return;
  }
  if (append) enqueue(playlist.tracks);
  else playQueue(playlist.tracks, 0);
}
