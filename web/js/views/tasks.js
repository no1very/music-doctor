/* Task centre and duplicate detection views. */

import { api, esc, fmtDuration, fmtSize, go, openPanel, state, toast } from "../core.js";
import { empty, pageHead } from "./common.js";

const STATUS_TEXT = {
  queued: "排队中",
  running: "进行中",
  canceling: "正在取消",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
};

export async function tasks(root, _route, ctx) {
  const data = await api("/api/tasks?limit=80");
  root.innerHTML = `
    ${pageHead("任务中心", "扫描、刮削、批量编辑等耗时操作在后台执行", `
      <button class="btn" data-action="open-scan">扫描媒体库</button>
      <button class="btn" data-action="scrape-untagged">刮削未完成</button>`)}
    <div class="panel">
      ${data.items.length ? data.items.map(taskRow).join("") : empty("暂无任务", "⏱")}
    </div>`;
  void ctx;
}

function taskRow(task) {
  const pct = task.status === "done" ? 100 : Math.min(100, Number(task.progress) || 0);
  const indeterminate = ["queued", "running"].includes(task.status) && !task.total;
  const detail = task.total
    ? `${task.done || 0} / ${task.total}`
    : task.result && Object.keys(task.result).length
      ? Object.entries(task.result).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(" · ")
      : "";
  return `
    <div class="task-row">
      <div class="task-head">
        <span class="status-dot ${task.status}"></span>
        <strong style="flex:1">${esc(task.title || task.kind)}
          <span class="badge">${esc(task.kind)}</span>
          <span class="muted tiny">${esc(STATUS_TEXT[task.status] || task.status)}</span>
        </strong>
        <span class="muted tiny">${esc((task.started_at || task.created_at || "").slice(5, 16))}</span>
        <button class="mini" data-action="open-task" data-id="${task.id}">详情</button>
        ${["running", "queued"].includes(task.status) ? `<button class="mini" data-action="cancel-task" data-id="${task.id}">取消</button>` : ""}
      </div>
      <div class="progress ${indeterminate ? "indeterminate" : ""}"><i style="width:${pct}%"></i></div>
      <div class="muted tiny" style="margin-top:6px">${esc(detail)} ${task.error ? `· <span class="error">${esc(task.error)}</span>` : ""}</div>
    </div>`;
}

export async function openTask(taskId) {
  const task = await api(`/api/tasks/${taskId}`);
  const pct = task.status === "done" ? 100 : Math.min(100, Number(task.progress) || 0);
  openPanel("editor", `
    <div class="drawer-head"><h3>${esc(task.title || task.kind)}</h3>
      <button class="icon-btn" data-action="close-editor">✕</button></div>
    <div class="drawer-body">
      <div class="kv" style="margin-bottom:14px">
        <dt>状态</dt><dd><span class="status-dot ${task.status}" style="display:inline-block"></span> ${esc(STATUS_TEXT[task.status] || task.status)}</dd>
        <dt>进度</dt><dd>${pct.toFixed(0)}% ${task.total ? `(${task.done}/${task.total})` : ""}</dd>
        <dt>创建</dt><dd>${esc(task.created_at)}</dd>
        <dt>开始</dt><dd>${esc(task.started_at || "—")}</dd>
        <dt>结束</dt><dd>${esc(task.finished_at || "—")}</dd>
        ${task.error ? `<dt>错误</dt><dd class="error">${esc(task.error)}</dd>` : ""}
      </div>
      <div class="progress ${["queued", "running"].includes(task.status) && !task.total ? "indeterminate" : ""}" style="margin-bottom:14px"><i style="width:${pct}%"></i></div>
      <h3>日志</h3>
      <div class="log" id="taskLog">${task.logs.map((line) => logLine(line)).join("") || "<span class='muted'>暂无输出</span>"}</div>
      <div class="toolbar" style="margin-top:12px">
        ${["running", "queued"].includes(task.status) ? `<button class="btn danger" data-action="cancel-task" data-id="${task.id}">取消任务</button>` : ""}
        <button class="btn" data-action="open-tasks">返回任务中心</button>
      </div>
    </div>`);
  state.meta.openTaskId = Number(taskId);
}

function logLine(line) {
  const level = line.level === "warn" || line.level === "error" ? line.level : "";
  return `<div class="${level}">[${esc((line.ts || "").slice(11, 19))}] ${esc(line.message)}</div>`;
}

export function appendTaskLog(event) {
  const host = document.getElementById("taskLog");
  if (!host || state.meta.openTaskId !== event.task_id) return;
  const div = document.createElement("div");
  div.className = event.level === "warn" || event.level === "error" ? event.level : "";
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  div.textContent = `[${time}] ${event.message}`;
  host.appendChild(div);
  host.scrollTop = host.scrollHeight;
}

/* ---------------------------------------------------------------- duplicates */
export async function duplicates(root, _route, ctx) {
  const data = await api("/api/duplicates");
  root.innerHTML = `
    ${pageHead("音乐去重", `${data.total} 组疑似重复 · 按标题、艺术家与时长聚类，评分优先保留高码率/无损版本`, `
      <button class="btn" data-action="dup-scan">重新检测</button>`)}
    ${data.items.length ? data.items.map(dupGroup).join("") : empty("没有发现重复文件", "✓")}
    <p class="muted tiny">删除的文件会移动到 <code>data/trash/</code>，不会立即永久删除。</p>`;
  void ctx;
}

function dupGroup(group) {
  const canResolve = state.user?.role === "admin";
  return `
    <div class="panel" style="margin-bottom:14px" data-dup-key="${esc(group.key)}">
      <div class="toolbar">
        <strong>${esc(group.title)}</strong><span class="muted">${esc(group.artist || "")}</span>
        <span class="badge warn">${group.copies} 份</span>
        <div class="spacer"></div>
        ${canResolve
          ? `<button class="btn sm" data-action="dup-auto" data-key="${esc(group.key)}">保留最佳，清理其余</button>`
          : '<span class="muted tiny">仅管理员可清理重复文件</span>'}
      </div>
      <div class="table-wrap">
        <table class="data"><thead><tr><th></th><th>文件</th><th class="num">时长</th><th class="num">码率</th><th class="num">大小</th><th>格式</th><th>路径</th></tr></thead>
        <tbody>${group.tracks.map((track, index) => `
          <tr>
            <td><input type="radio" name="keep-${esc(group.key)}" value="${track.id}" ${index === 0 ? "checked" : ""} /></td>
            <td>${esc(track.title)}</td>
            <td class="num">${fmtDuration(track.duration)}</td>
            <td class="num">${track.bitrate || 0}k</td>
            <td class="num">${fmtSize(track.size)}</td>
            <td><span class="badge">${esc((track.codec || "").toUpperCase())}</span></td>
            <td class="muted tiny" title="${esc(track.path)}">${esc(track.rel_path)}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
    </div>`;
}

export async function resolveDuplicateGroup(key, deleteFiles = true) {
  const group = [...document.querySelectorAll("[data-dup-key]")].find((el) => el.dataset.dupKey === key);
  if (!group) return;
  const checked = group.querySelector('input[type="radio"]:checked');
  if (!checked) return toast("请选择要保留的版本", "warn");
  const keepId = Number(checked.value);
  const ids = [...group.querySelectorAll('input[type="radio"]')]
    .map((input) => Number(input.value))
    .filter((id) => id !== keepId);
  if (!ids.length) return;
  await api("/api/duplicates/resolve", { method: "POST", body: { ids, delete_files: deleteFiles } });
  toast(`已清理 ${ids.length} 个重复文件`);
  window.dispatchEvent(new CustomEvent("refresh-view"));
}

export async function resolveAllBest() {
  const data = await api("/api/duplicates");
  const remove = [];
  data.items.forEach((group) => {
    group.tracks.forEach((track) => {
      if (track.id !== group.keep_id) remove.push(track.id);
    });
  });
  if (!remove.length) return toast("没有需要清理的重复文件");
  await api("/api/duplicates/resolve", { method: "POST", body: { ids: remove, delete_files: true } });
  toast(`已清理 ${remove.length} 个重复文件`);
  go("#/duplicates");
}
