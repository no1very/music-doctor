/* Shared listening rooms: join by code, sync playback state across devices. */

import { api, esc, go, state, toast } from "../core.js";
import { player, playQueue } from "../player.js";
import { empty, pageHead } from "./common.js";

export async function rooms(root, route, ctx) {
  if (route.id) return roomDetail(root, route, ctx);
  const data = await api("/api/rooms");
  root.innerHTML = `
    ${pageHead("一起听", "创建或加入共享听歌房间，播放进度会同步到所有成员", `
      <button class="btn" data-action="new-room">创建房间</button>
      <button class="btn primary" data-action="join-room">加入房间</button>`)}
    <div class="grid-3">
      ${data.items.length ? data.items.map((room) => `
        <div class="panel">
          <div class="toolbar" style="margin-bottom:6px">
            <strong style="flex:1">${esc(room.name)}</strong>
            <span class="badge">${esc(room.code)}</span>
          </div>
          <div class="muted tiny">${room.members} 位成员 · 创建于 ${esc((room.created_at || "").slice(0, 16))}</div>
          <div class="muted tiny" style="margin:8px 0">${
            room.state?.track_id ? `正在播放 #${room.state.track_id}` : "等待播放"
          }</div>
          <div class="toolbar">
            <button class="btn sm" data-action="enter-room" data-code="${esc(room.code)}">进入</button>
            ${(room.owner_id === state.user?.id || state.user?.role === "admin")
              ? `<button class="btn sm danger" data-action="delete-room" data-code="${esc(room.code)}">解散</button>`
              : ""}
          </div>
        </div>`).join("") : empty("还没有房间，创建一个邀请朋友一起听", "⌂")}
    </div>`;
  void ctx;
}

async function roomDetail(root, route, ctx) {
  const code = route.id.toUpperCase();
  let room;
  try {
    room = await api(`/api/rooms/${code}`);
  } catch (err) {
    root.innerHTML = empty(err.message, "⌂");
    return;
  }
  state.room = room;
  await api("/api/rooms/join", { method: "POST", body: { code } });
  const current = room.state?.track_id ? await api(`/api/tracks/${room.state.track_id}`).catch(() => null) : null;
  root.innerHTML = `
    <div class="hero">
      <div class="art cover-ph" style="display:grid;place-items:center;font-size:34px">⌂</div>
      <div class="meta">
        <div class="muted tiny">共享听歌房间</div>
        <h1>${esc(room.name)}</h1>
        <div class="muted">房间码 <code>${esc(room.code)}</code> · ${room.members.length} 位成员 · 主持人 ${esc(room.members[0]?.username || "—")}</div>
        <div class="actions">
          <button class="btn primary" data-action="room-sync">同步到此设备</button>
          <button class="btn" data-action="room-toggle">${room.state?.playing ? "暂停所有人" : "播放所有人"}</button>
          <button class="btn danger" data-action="leave-room">离开房间</button>
        </div>
      </div>
    </div>
    <div class="grid-2">
      <div class="panel">
        <h3>当前播放</h3>
        ${current ? `<div class="t-title">${current.cover_url ? `<img class="t-cover" src="${esc(current.cover_url)}" />` : ""}
          <div class="t-main"><div class="t-name">${esc(current.title)}</div><div class="t-sub">${esc(current.artist)}</div></div></div>
          <div class="muted tiny" style="margin-top:10px">进度 ${Math.round((room.state.position_ms || 0) / 1000)} 秒 · ${room.state.playing ? "播放中" : "已暂停"}</div>`
          : '<div class="muted">房间里还没有播放内容</div>'}
      </div>
      <div class="panel">
        <h3>成员</h3>
        ${room.members.map((member) => `<div class="version-row"><span class="avatar" style="width:24px;height:24px;font-size:11px">${esc((member.username || "?")[0].toUpperCase())}</span>
          <span style="flex:1">${esc(member.username || "未知")}</span><span class="muted tiny">${esc((member.last_seen || "").slice(11, 16))}</span></div>`).join("")}
      </div>
    </div>
    <p class="muted tiny" style="margin-top:16px">播放控制会通过 SSE 广播到所有成员；「同步到此设备」会把房间当前曲目和进度载入本地播放器。</p>`;
  ctx.refresh;
}

export async function createRoom() {
  const name = window.prompt("房间名称", `${state.user?.display_name || state.user?.username || "我"}的房间`);
  if (name === null) return;
  const room = await api("/api/rooms", { method: "POST", body: { name } });
  toast(`房间已创建：${room.code}`);
  go(`#/rooms/${room.code}`);
}

export async function joinRoom() {
  const code = window.prompt("输入房间码");
  if (!code) return;
  try {
    await api("/api/rooms/join", { method: "POST", body: { code: code.trim().toUpperCase() } });
    go(`#/rooms/${code.trim().toUpperCase()}`);
  } catch (err) {
    toast(err.message, "error");
  }
}

export async function roomSync() {
  if (!state.room?.state?.track_id) return toast("房间还没有播放内容", "warn");
  const room = await api(`/api/rooms/${state.room.code}`);
  const track = await api(`/api/tracks/${room.state.track_id}`);
  state.meta.applyingRoom = true;
  playQueue([track], 0, { silent: true });
  player.audio.currentTime = (Number(room.state.position_ms) || 0) / 1000;
  if (!room.state.playing) player.audio.pause();
  setTimeout(() => (state.meta.applyingRoom = false), 600);
  toast("已同步房间播放状态");
}

export async function roomToggle() {
  const room = await api(`/api/rooms/${state.room.code}`);
  const playing = !room.state?.playing;
  await api(`/api/rooms/${state.room.code}/state`, {
    method: "PUT",
    body: { playing, track_id: room.state?.track_id || null, position_ms: room.state?.position_ms || 0 },
  });
  if (playing) player.audio.play().catch(() => {});
  else player.audio.pause();
}

export async function leaveRoom() {
  if (!state.room?.code) return;
  await api(`/api/rooms/${state.room.code}/leave`, { method: "POST" });
  state.room = null;
  toast("已离开房间");
  go("#/rooms");
}

export function onRoomState(event) {
  if (!state.room || event.code !== state.room.code) return;
  state.room.state = event.state;
  const view = document.getElementById("view");
  if (location.hash.startsWith("#/rooms/") && view) {
    window.dispatchEvent(new CustomEvent("refresh-view"));
  }
  if (state.meta.applyingRoom || !event.state?.track_id) return;
  const currentId = player.current?.id;
  if (event.state.track_id !== currentId) {
    roomSync();
  } else if (event.state.playing && player.audio.paused) {
    player.audio.play().catch(() => {});
  } else if (!event.state.playing && !player.audio.paused) {
    player.audio.pause();
  }
}
