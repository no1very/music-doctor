/* Scrape report: volume by day/source, untagged backlog, failed tasks. */

import { api, esc, go } from "../core.js";
import { empty, pageHead, statCard } from "./common.js";

export async function reports(root, route) {
  const days = Number(route.query.days || 14);
  const data = await api(`/api/reports/scrape?days=${days}`);
  const maxSource = Math.max(1, ...data.by_source.map((row) => row.count));
  const maxDay = Math.max(1, ...data.by_day.map((row) => row.count));
  root.innerHTML = `
    ${pageHead("刮削报告", `最近 ${data.days} 天的刮削结果与待处理项`, `
      ${[7, 14, 30, 90].map((value) => `<button class="btn sm ${value === days ? "primary" : ""}" data-action="report-days" data-days="${value}">${value} 天</button>`).join("")}
      <button class="btn" data-action="scrape-untagged">刮削未完成</button>`)}
    <div class="cards">
      ${statCard("刮削写入", data.total.toLocaleString(), `最近 ${data.days} 天`)}
      ${statCard("待刮削", data.untagged.toLocaleString(), "缺少标题/艺术家/专辑")}
      ${statCard("重复组", data.duplicates.toLocaleString(), "去重页可清理")}
      ${statCard("失败任务", data.failed_tasks.length.toLocaleString(), "最近失败记录")}
    </div>

    <div class="grid-2">
      <div class="panel">
        <h3>来源分布</h3>
        ${data.by_source.length ? data.by_source.map((row) => `
          <div style="margin-bottom:10px">
            <div class="toolbar" style="margin-bottom:4px">
              <span style="flex:1">${esc(row.source)}</span>
              <span class="muted tiny">${row.count}</span>
            </div>
            <div class="progress"><i style="width:${(row.count / maxSource) * 100}%"></i></div>
          </div>`).join("") : empty("还没有刮削记录", "◎")}
      </div>
      <div class="panel">
        <h3>每日写入</h3>
        ${data.by_day.length ? `<div class="table-wrap"><table class="data">
          <thead><tr><th>日期</th><th class="num">数量</th><th style="width:50%"></th></tr></thead>
          <tbody>${data.by_day.slice().reverse().map((row) => `
            <tr><td>${esc(row.day)}</td><td class="num">${row.count}</td>
              <td><div class="progress"><i style="width:${(row.count / maxDay) * 100}%"></i></div></td></tr>`).join("")}
          </tbody></table></div>` : empty("暂无数据", "▤")}
      </div>
    </div>

    <div class="section" style="margin-top:18px">
      <div class="section-head"><h2>最近失败任务</h2><a class="muted tiny" href="#/tasks">任务中心 →</a></div>
      ${data.failed_tasks.length ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>任务</th><th>类型</th><th>错误</th><th>时间</th><th></th></tr></thead>
        <tbody>${data.failed_tasks.map((task) => `
          <tr><td>${esc(task.title)}</td><td><span class="badge bad">${esc(task.kind)}</span></td>
            <td class="muted tiny">${esc(task.error || "")}</td>
            <td class="muted tiny">${esc((task.finished_at || "").slice(0, 16))}</td>
            <td><button class="mini" data-action="open-task" data-id="${task.id}">日志</button></td></tr>`).join("")}
        </tbody></table></div>` : '<div class="empty">最近没有失败任务 🎉</div>'}
    </div>`;
  void go;
}
