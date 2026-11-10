/* Playback engine: queue, shuffle/repeat, seek, media session, cross-device sync. */

import { api, debounce, state, esc, fmtDuration, toast } from "./core.js";

export const player = {
  audio: null,
  queue: [],
  index: -1,
  shuffle: false,
  repeat: "off",
  current: null,
  saving: false,
};

const coverFallback =
  "data:image/svg+xml," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#1b2336"/><text x="60" y="72" font-size="42" text-anchor="middle" fill="#61708f">♪</text></svg>',
  );

/* --------------------------------------------------------------- helpers */
function el(id) {
  return document.getElementById(id);
}

function shuffleOrder(startIndex) {
  const rest = player.queue.map((_, i) => i).filter((i) => i !== startIndex);
  for (let i = rest.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [rest[i], rest[j]] = [rest[j], rest[i]];
  }
  return [startIndex, ...rest];
}

export function playQueueIndex(index) {
  if (index < 0 || index >= player.queue.length) return;
  player.index = index;
  player._pos = Math.max(0, (player._order || []).indexOf(index));
  const track = player.queue[index];
  if (!track) return;
  player.current = track;
  resetReport();
  player.audio.src = track.stream_url;
  player.audio.play().catch(() => {});
  updateNowPlaying();
  renderQueue();
  persistQueue();
  bindMediaSession();
  announceRoom();
}

/* Removing any queue entry has to keep three things aligned: queue indexes,
   the shuffle order (_order) and the cursor inside it (_pos). Adjusting only
   queue.index made "next" jump to a stale entry and left the server copy of
   the queue untouched. */
export function removeFromQueue(index) {
  if (!Number.isInteger(index) || index < 0 || index >= player.queue.length) return;
  const wasCurrent = index === player.index;
  player.queue.splice(index, 1);
  if (!player.queue.length) {
    clearQueue();
    return;
  }
  if (player._order && player._order.length) {
    player._order = player._order.filter((i) => i !== index).map((i) => (i > index ? i - 1 : i));
  } else {
    player._order = player.queue.map((_, i) => i);
  }
  if (wasCurrent) {
    player._pos = Math.max(0, Math.min(player._pos ?? 0, player._order.length - 1));
    player.index = player._order[player._pos];
    if (player.index === undefined) {
      player._pos = 0;
      player._order = player.queue.map((_, i) => i);
      player.index = 0;
    }
    loadCurrent();
    return;
  }
  player.index = player.index > index ? player.index - 1 : player.index;
  player._pos = Math.max(0, player._order.indexOf(player.index));
  renderQueue();
  persistQueue();
  updateNowPlaying();
}

export function clearQueue() {
  player.queue = [];
  player.index = -1;
  player._pos = 0;
  player._order = [];
  player.current = null;
  player.audio.pause();
  player.audio.removeAttribute("src");
  updateNowPlaying();
  renderQueue();
  persistQueue();
}
/* ------------------------------------------------------------------ queue */
export function playQueue(tracks, startIndex = 0, { silent = false } = {}) {
  if (!tracks || !tracks.length) return;
  player.queue = tracks.slice();
  player._order = player.shuffle ? shuffleOrder(startIndex) : player.queue.map((_, i) => i);
  player._pos = player.shuffle ? 0 : startIndex;
  player.index = player._order[player._pos];
  loadCurrent();
  if (!silent) toast(`开始播放：${player.current?.title || ""}`);
}

export function playTrack(track) {
  playQueue([track], 0, { silent: true });
}

export function enqueue(tracks) {
  const list = Array.isArray(tracks) ? tracks : [tracks];
  const newIndexes = list.map((_, i) => player.queue.length + i);
  player.queue.push(...list);
  if (player.index < 0 && player.queue.length) {
    player.index = 0;
    player._order = player.queue.map((_, i) => i);
    player._pos = 0;
    loadCurrent();
  } else {
    // Appended tracks must enter the shuffle order too, otherwise "next" stops
    // at the old end of the queue.
    player._order = player.shuffle && player._order?.length
      ? [...player._order, ...newIndexes]
      : player.queue.map((_, i) => i);
    player._pos = Math.max(0, player._order.indexOf(player.index));
  }
  renderQueue();
  persistQueue();
  toast(`已加入队列（${list.length} 首）`);
}

function loadCurrent() {
  const track = player.queue[player.index];
  if (!track) return;
  player.current = track;
  resetReport();
  const audio = player.audio;
  audio.src = track.stream_url;
  audio.play().catch(() => {});
  updateNowPlaying();
  renderQueue();
  persistQueue();
  bindMediaSession();
  announceRoom();
}

function updateNowPlaying() {
  const track = player.current;
  el("npTitle").textContent = track ? track.title || "未知标题" : "未在播放";
  el("npArtist").textContent = track ? [track.artist, track.album].filter(Boolean).join(" · ") : "";
  el("npCover").src = track?.cover_url || coverFallback;
  el("playBtn").textContent = player.audio.paused ? "▶" : "❚❚";
  el("qualityBadge").textContent = track
    ? `${(track.codec || "").toUpperCase()}${track.bitrate ? ` · ${track.bitrate}k` : ""}`
    : "";
  el("shuffleBtn").classList.toggle("active", player.shuffle);
  el("repeatBtn").classList.toggle("active", player.repeat !== "off");
  el("repeatBtn").textContent = player.repeat === "one" ? "↻¹" : "↻";
  el("npFav").classList.toggle("active", Boolean(track?.favorite));
  el("totalTime").textContent = fmtDuration(track?.duration || 0);
  if (track?.id) {
    document.title = `${track.title} · ${track.artist || ""}`;
  }
}

export function renderQueue() {
  const list = el("queueList");
  if (!list) return;
  el("queueCount").textContent = player.queue.length ? `${player.queue.length} 首` : "";
  if (!player.queue.length) {
    list.innerHTML = '<div class="empty"><div class="big">☷</div>队列是空的</div>';
    return;
  }
  list.innerHTML = player.queue
    .map(
      (track, index) => `
      <div class="queue-item ${index === player.index ? "current" : ""}" data-action="queue-play" data-index="${index}">
        <span class="idx">${index === player.index ? "▶" : index + 1}</span>
        <span class="qtitle">${esc(track.title || "未知标题")}<span class="muted"> · ${esc(track.artist || "")}</span></span>
        <span class="muted tiny">${fmtDuration(track.duration)}</span>
        <button class="mini" data-action="queue-remove" data-index="${index}" title="移除">✕</button>
      </div>`,
    )
    .join("");
}

/* -------------------------------------------------------------- transport */
export function togglePlay() {
  if (!player.current) {
    if (player.queue.length) {
      player.index = 0;
      loadCurrent();
    }
    return;
  }
  if (player.audio.paused) player.audio.play().catch(() => {});
  else player.audio.pause();
}

export function next({ auto = false } = {}) {
  if (!player.queue.length) return;
  if (player.repeat === "one" && auto) {
    player.audio.currentTime = 0;
    player.audio.play().catch(() => {});
    return;
  }
  player._pos = (player._pos ?? 0) + 1;
  if (player._pos >= (player._order?.length || 0)) {
    if (player.repeat === "off") {
      player.audio.pause();
      player._pos = (player._order?.length || 1) - 1;
      updateNowPlaying();
      return;
    }
    player._pos = 0;
  }
  player.index = player._order[player._pos];
  loadCurrent();
}

export function prev() {
  if (!player.queue.length) return;
  if (player.audio.currentTime > 4) {
    player.audio.currentTime = 0;
    return;
  }
  player._pos = (player._pos ?? 0) - 1;
  if (player._pos < 0) player._pos = 0;
  player.index = player._order[player._pos];
  loadCurrent();
}

export function seekToPercent(percent) {
  const duration = player.audio.duration || player.current?.duration || 0;
  if (duration) player.audio.currentTime = (Number(percent) / 1000) * duration;
}

export function setVolume(value) {
  player.audio.volume = Math.max(0, Math.min(1, Number(value) / 100));
  localStorage.setItem("omt_volume", String(value));
}

export function toggleMute() {
  player.audio.muted = !player.audio.muted;
  el("volBtn").textContent = player.audio.muted ? "🔇" : "🔊";
}

export function toggleShuffle() {
  player.shuffle = !player.shuffle;
  const keep = player.index;
  player._order = player.shuffle ? shuffleOrder(keep) : player.queue.map((_, i) => i);
  player._pos = Math.max(0, player._order.indexOf(keep));
  updateNowPlaying();
}

export function cycleRepeat() {
  player.repeat = player.repeat === "off" ? "all" : player.repeat === "all" ? "one" : "off";
  updateNowPlaying();
  toast(`循环模式：${{ off: "关闭", all: "列表循环", one: "单曲循环" }[player.repeat]}`, "info");
}

/* --------------------------------------------------------------- persist */
const persistQueue = debounce(async () => {
  if (!state.token) return;
  try {
    await api("/api/queue", {
      method: "PUT",
      body: {
        items: player.queue.map((track) => track.id),
        current: player._pos ?? 0,
        position_ms: Math.round((player.audio.currentTime || 0) * 1000),
      },
    });
  } catch {
    /* offline is fine */
  }
}, 700);

export async function restoreQueue() {
  try {
    const saved = await api("/api/queue");
    if (!saved.items?.length) return;
    const ids = saved.items.join(",");
    const data = await api(`/api/tracks?ids=${ids}&limit=1500&missing=0`);
    const byId = new Map(data.items.map((track) => [track.id, track]));
    const list = saved.items.map((id) => byId.get(id)).filter(Boolean);
    if (!list.length) return;
    player.queue = list;
    player._order = list.map((_, i) => i);
    player._pos = Math.min(Number(saved.current) || 0, list.length - 1);
    player.index = player._pos;
    player.current = list[player.index];
    player.audio.src = player.current.stream_url;
    player.audio.currentTime = (Number(saved.position_ms) || 0) / 1000;
    updateNowPlaying();
    renderQueue();
  } catch {
    /* ignore */
  }
}

/* --------------------------------------------------------- media session */
function bindMediaSession() {
  if (!("mediaSession" in navigator) || !player.current) return;
  const track = player.current;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: track.title || "",
    artist: track.artist || "",
    album: track.album || "",
    artwork: track.cover_url ? [{ src: track.cover_url, sizes: "512x512", type: "image/jpeg" }] : [],
  });
  navigator.mediaSession.setActionHandler("play", () => player.audio.play());
  navigator.mediaSession.setActionHandler("pause", () => player.audio.pause());
  navigator.mediaSession.setActionHandler("nexttrack", () => next());
  navigator.mediaSession.setActionHandler("previoustrack", () => prev());
}

/* ------------------------------------------------------------- listening */
async function reportPlay(completed) {
  const track = player.current;
  if (!track?.id) return;
  try {
    await api(`/api/tracks/${track.id}/play`, {
      method: "POST",
      body: { client: "web", ms_played: Math.round(player.audio.currentTime * 1000), completed },
    });
  } catch {
    /* ignore */
  }
}

let reported = false;
/* Playback reporting is once per track; a new track must re-arm it, otherwise
   skipping songs never records the new one in the play history. */
function resetReport() {
  reported = false;
}
function announceRoom() {
  if (!state.room?.code) return;
  api(`/api/rooms/${state.room.code}/state`, {
    method: "PUT",
    body: {
      playing: !player.audio.paused,
      track_id: player.current?.id || null,
      position_ms: Math.round((player.audio.currentTime || 0) * 1000),
    },
  }).catch(() => {});
}

/* ------------------------------------------------------------------ init */
export function initPlayer() {
  player.audio = el("audio");
  const audio = player.audio;

  audio.addEventListener("play", () => {
    el("playBtn").textContent = "❚❚";
    announceRoom();
  });
  audio.addEventListener("pause", () => {
    el("playBtn").textContent = "▶";
    announceRoom();
  });
  audio.addEventListener("timeupdate", () => {
    const duration = audio.duration || player.current?.duration || 0;
    el("curTime").textContent = fmtDuration(audio.currentTime);
    el("totalTime").textContent = fmtDuration(duration);
    if (!player.seeking) el("seek").value = duration ? String((audio.currentTime / duration) * 1000) : "0";
    if (!reported && duration && audio.currentTime > Math.min(10, duration * 0.5)) {
      reported = true;
      reportPlay(false);
    }
  });
  audio.addEventListener("ended", () => {
    reportPlay(true);
    reported = false;
    next({ auto: true });
  });
  audio.addEventListener("loadedmetadata", () => {
    el("totalTime").textContent = fmtDuration(audio.duration || player.current?.duration || 0);
  });

  const seek = el("seek");
  seek.addEventListener("input", () => {
    player.seeking = true;
  });
  seek.addEventListener("change", () => {
    seekToPercent(seek.value);
    player.seeking = false;
  });

  const volume = el("volume");
  const saved = localStorage.getItem("omt_volume");
  if (saved) volume.value = saved;
  setVolume(volume.value);
  volume.addEventListener("input", () => setVolume(volume.value));

  updateNowPlaying();
  renderQueue();
}
