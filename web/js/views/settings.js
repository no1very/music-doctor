/* Settings: libraries, scraping, naming, users, plugins, about. */

import { api, esc, fmtSize, state, toast } from "../core.js";
import { pageHead } from "./common.js";

const TABS = [
  ["libraries", "媒体库"],
  ["scrape", "刮削与标签"],
  ["naming", "文件命名"],
  ["users", "用户与权限"],
  ["plugins", "插件与接口"],
  ["about", "关于"],
];

export async function settings(root, route, ctx) {
  const tab = route.query.tab || "libraries";
  const [settingsData, libraries, providersData] = await Promise.all([
    api("/api/settings"),
    api("/api/libraries"),
    api("/api/providers"),
  ]);
  const settings = settingsData.items;
  state.settings = settings;
  const users = state.user?.role === "admin" ? (await api("/api/users")).items : [];
  const keys = tab === "plugins" ? (await api("/api/apikeys")).items : [];

  root.innerHTML = `
    ${pageHead("设置", "媒体库路径、刮削来源、命名模板与账号权限", "")}
    <div class="tabs">
      ${TABS.map(([key, label]) => `<button class="${tab === key ? "active" : ""}" data-action="settings-tab" data-tab="${key}">${label}</button>`).join("")}
    </div>
    <div id="settingsBody">
      ${tab === "libraries" ? librariesTab(libraries.items, settings) : ""}
      ${tab === "scrape" ? scrapeTab(settings, providersData.items) : ""}
      ${tab === "naming" ? namingTab(settings) : ""}
      ${tab === "users" ? usersTab(users) : ""}
      ${tab === "plugins" ? pluginsTab(providersData.items, keys) : ""}
      ${tab === "about" ? aboutTab(settings) : ""}
    </div>`;

  wire();
  void ctx;
}

/* ---------------------------------------------------------------- libraries */
function librariesTab(libraries, settings) {
  const admin = state.user?.role === "admin";
  return `
    <div class="section">
      <div class="section-head"><h2>媒体库目录</h2><span class="muted tiny">扫描会在后台任务中心执行</span></div>
      ${libraries.length ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>名称</th><th>类型</th><th>位置</th><th class="num">曲目</th><th>最近扫描</th><th></th></tr></thead>
        <tbody>${libraries.map((lib) => `
          <tr><td><strong>${esc(lib.name)}</strong></td>
            <td><span class="badge ${lib.kind === "webdav" ? "flac" : ""}">${lib.kind === "webdav" ? "WebDAV/AList" : "本地"}</span></td>
            <td class="muted tiny" title="${esc(lib.path)}">${esc(lib.path)}</td>
            <td class="num">${lib.track_count}</td>
            <td class="muted tiny">${esc(lib.last_scan_at || "从未")}</td>
            <td style="white-space:nowrap">
              <button class="mini" data-action="scan-library" data-id="${lib.id}">扫描</button>
              <button class="mini" data-action="scan-library" data-id="${lib.id}" data-force="1">强制全量</button>
              ${admin ? `<button class="mini" data-action="delete-library" data-id="${lib.id}">移除</button>` : ""}
            </td></tr>`).join("")}
        </tbody></table></div>` : '<div class="empty">尚未添加媒体库</div>'}
    </div>
    ${admin ? `<div class="grid-2">
      <div class="panel">
        <h3>添加媒体库</h3>
        <form id="libraryForm" class="editor-grid">
          <label class="field full">类型
            <select name="kind" id="libKind">
              <option value="local">本地目录</option>
              <option value="webdav">WebDAV / AList（远程）</option>
            </select>
          </label>
          <label class="field">名称<input type="text" name="name" placeholder="例如：主音乐库" /></label>
          <label class="field" data-local-only>目录路径<input type="text" name="path" placeholder="D:\\Music 或 /music" /></label>
          <label class="field full" data-remote-only hidden>WebDAV 地址<input type="text" name="url" placeholder="https://alist.example.com/dav/music" /></label>
          <label class="field" data-remote-only hidden>用户名<input type="text" name="username" autocomplete="off" /></label>
          <label class="field" data-remote-only hidden>密码<input name="password" type="password" autocomplete="new-password" /></label>
          <div class="full"><button class="btn primary" type="submit">添加并扫描</button></div>
        </form>
        <p class="muted tiny" style="margin-top:10px">远程库只读（不支持写回网盘），可播放、刮削、加入歌单；AList 需启用 WebDAV 并填写其 /dav 路径。</p>
      </div>
      <div class="panel">
        <h3>写回与外部文件</h3>
        <p class="muted tiny">这些设置位于「刮削与标签」页：</p>
        <dl class="kv">
          <dt>写回标签</dt><dd>${settings["scrape.write_tags"] === "1" ? "开启" : "关闭（仅更新索引）"}</dd>
          <dt>外置歌词</dt><dd>${settings["scrape.write_lrc"] === "1" ? "写 .lrc 同名文件" : "仅内嵌"}</dd>
          <dt>封面文件</dt><dd>${settings["scrape.write_cover_file"] === "1" ? "写 cover.jpg" : "仅内嵌"}</dd>
        </dl>
      </div>
    </div>` : ""}`;
}

/* ----------------------------------------------------------------- scraping */
function scrapeTab(settings, providers) {
  const enabled = JSON.parse(settings["scrape.providers"] || '["filename"]');
  const artistEnabled = JSON.parse(settings["artist.providers"] || '["netease","wikipedia","deezer"]');
  return `
    <div class="grid-2">
      <form class="panel" id="scrapeForm">
        <h3>标签来源</h3>
        <p class="muted tiny">按顺序尝试，取评分最高的候选。仅勾选可访问的来源。</p>
        <div class="chips" style="margin:10px 0 16px">
          ${providers.map((provider) => `
            <label class="chip ${enabled.includes(provider.name) ? "on" : ""}" style="cursor:pointer">
              <input type="checkbox" name="providers" value="${esc(provider.name)}" ${enabled.includes(provider.name) ? "checked" : ""} style="display:none" />
              ${esc(provider.label)}${provider.needs_network ? " 🌐" : ""}
            </label>`).join("")}
        </div>
        <div class="editor-grid">
          <label class="field">最低匹配分<input name="min_score" type="number" step="0.01" min="0" max="1" value="${esc(settings["scrape.min_score"] || "0.72")}" /></label>
          <label class="field">歌词来源<select name="lyrics_provider">
            ${[["lrclib", "LRCLIB"], ["netease", "网易云"], ["", "关闭"]].map(([value, label]) =>
              `<option value="${value}" ${(settings["lyrics.provider"] || "lrclib") === value ? "selected" : ""}>${label}</option>`).join("")}
          </select></label>
        </div>
        <div class="chips" style="margin-top:14px">
          <label class="chip"><input type="checkbox" name="write_cover" ${settings["scrape.write_cover"] === "1" ? "checked" : ""} /> 写入封面</label>
          <label class="chip"><input type="checkbox" name="write_lyrics" ${settings["scrape.write_lyrics"] === "1" ? "checked" : ""} /> 写入歌词</label>
          <label class="chip"><input type="checkbox" name="auto_on_scan" ${settings["scrape.auto_on_scan"] === "1" ? "checked" : ""} /> 扫描后自动刮削</label>
        </div>
        <div class="chips" style="margin-top:8px">
          <label class="chip"><input type="checkbox" name="write_tags" ${settings["scrape.write_tags"] !== "0" ? "checked" : ""} /> 写回音频标签</label>
          <label class="chip"><input type="checkbox" name="write_lrc" ${settings["scrape.write_lrc"] === "1" ? "checked" : ""} /> 外置 .lrc 歌词</label>
          <label class="chip"><input type="checkbox" name="write_cover_file" ${settings["scrape.write_cover_file"] === "1" ? "checked" : ""} /> 外置 cover.jpg</label>
        </div>

        <h3 style="margin-top:18px">艺术家刮削</h3>
        <p class="muted tiny">抓取艺人头像、别名与简介；艺术家页可单独或批量刮削。</p>
        <div class="chips" style="margin-top:8px">
          ${[["netease", "网易云"], ["wikipedia", "维基百科"], ["deezer", "Deezer"]].map(([value, label]) => `
            <label class="chip ${artistEnabled.includes(value) ? "on" : ""}" style="cursor:pointer">
              <input type="checkbox" name="artistProviders" value="${value}" ${artistEnabled.includes(value) ? "checked" : ""} style="display:none" />
              ${label}
            </label>`).join("")}
        </div>
        <label class="field" style="max-width:200px;margin-top:10px">艺术家最低匹配分
          <input name="artist_min_score" type="number" step="0.05" min="0" max="1" value="${esc(settings["artist.min_score"] || "0.5")}" />
        </label>
        <label class="field" style="margin-top:10px">艺术家图片导出目录（Navidrome ArtistImageFolder）
          <input type="text" name="artist_image_folder" value="${esc(settings["artist.image_folder"] || "")}" placeholder="例如 /music/artist" />
        </label>
        <p class="muted tiny">填写后，每次刮削会自动把头像写成 <code>&lt;艺术家名&gt;.jpg</code>；
          Navidrome 需设置 <code>ND_ARTISTIMAGEFOLDER</code> 指向同一目录，并把
          <code>image-folder</code> 加入 <code>ND_ARTISTARTPRIORITY</code>。</p>

        <button class="btn primary" type="submit" style="margin-top:16px">保存刮削设置</button>
      </form>
      <div class="panel">
        <h3>标签写入</h3>
        <dl class="kv">
          <dt>MP3</dt><dd>ID3v2.3 读写，保留未知帧</dd>
          <dt>FLAC</dt><dd>Vorbis Comment + PICTURE 读写</dd>
          <dt>M4A / MP4</dt><dd>只读（避免破坏 stco 偏移）</dd>
          <dt>其他格式</dt><dd>仅入库索引，不写标签</dd>
        </dl>
        <p class="muted tiny" style="margin-top:12px">写入采用临时文件 + 原子替换，失败会保留原文件。</p>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------ naming */
function namingTab(settings) {
  const template = settings["naming.template"] || "${tracknumber}. ${title} - ${artist}";
  return `
    <form class="panel" id="namingForm" style="max-width:760px">
      <h3>文件重命名模板</h3>
      <p class="muted tiny">支持 <code>${"${变量}"}</code> 表达式，如
        <code>${"${tracknumber.zfill(2)}"}</code>、<code>${"${artist or '未知歌手'}"}</code>。</p>
      <label class="field" style="margin-top:12px">模板
        <textarea name="template" rows="3">${esc(template)}</textarea>
      </label>
      <div class="editor-grid" style="margin-top:12px">
        <label class="field">示例预览
          <input type="text" id="namingPreview" readonly value="" placeholder="选择歌曲后预览" />
        </label>
        <label class="field">自动重命名
          <select name="enabled">
            <option value="0" ${settings["naming.enabled"] === "1" ? "" : "selected"}>关闭（仅在手动触发时）</option>
            <option value="1" ${settings["naming.enabled"] === "1" ? "selected" : ""}>刮削/编辑后自动重命名</option>
          </select>
        </label>
      </div>
      <div class="chips" style="margin-top:12px">
        ${["${tracknumber.zfill(2)}. ${title}", "${artist} - ${album} - ${tracknumber} ${title}", "${discnumber.zfill(2)}_${tracknumber.zfill(2)} ${title}"]
          .map((tpl) => `<button type="button" class="chip" data-action="use-template" data-template="${esc(tpl)}">${esc(tpl)}</button>`).join("")}
      </div>
      <div class="toolbar" style="margin-top:16px">
        <button class="btn primary" type="submit">保存命名设置</button>
      </div>
      <p class="muted tiny">模板只影响文件名，不会移动目录。重命名前会检查目标文件是否已存在。</p>
    </form>`;
}

/* ------------------------------------------------------------------- users */
function usersTab(users) {
  return `
    <div class="section">
      <div class="section-head"><h2>用户</h2><span class="muted tiny">admin 可管理全部；editor 可编辑/刮削；listener 只能播放与收藏</span></div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>用户名</th><th>显示名</th><th>角色</th><th>状态</th><th>创建时间</th><th></th></tr></thead>
        <tbody>${users.map((user) => `
          <tr><td><strong>${esc(user.username)}</strong></td>
            <td>${esc(user.display_name)}</td>
            <td><select data-action="set-role" data-id="${user.id}" style="width:auto">
              ${["admin", "editor", "listener"].map((role) => `<option value="${role}" ${user.role === role ? "selected" : ""}>${role}</option>`).join("")}
            </select></td>
            <td>${user.active ? '<span class="badge flac">启用</span>' : '<span class="badge bad">停用</span>'}</td>
            <td class="muted tiny">${esc(user.created_at)}</td>
            <td style="white-space:nowrap">
              <button class="mini" data-action="reset-user-password" data-id="${user.id}" data-name="${esc(user.username)}">重置密码</button>
              <button class="mini" data-action="toggle-user" data-id="${user.id}" data-active="${user.active ? 1 : 0}">${user.active ? "停用" : "启用"}</button>
              <button class="mini" data-action="delete-user" data-id="${user.id}">删除</button>
            </td></tr>`).join("")}
        </tbody></table></div>
    </div>
    <div class="panel" style="max-width:560px">
      <h3>新建用户</h3>
      <form id="userForm" class="editor-grid">
        <label class="field">用户名<input type="text" name="username" required minlength="2" /></label>
        <label class="field">密码<input name="password" type="password" required minlength="6" /></label>
        <label class="field">显示名<input type="text" name="display_name" /></label>
        <label class="field">角色<select name="role">
          <option value="listener">listener（听众）</option>
          <option value="editor">editor（编辑）</option>
          <option value="admin">admin（管理员）</option>
        </select></label>
        <div class="full"><button class="btn primary" type="submit">创建用户</button></div>
      </form>
    </div>`;
}

/* ----------------------------------------------------------------- plugins */
function pluginsTab(providers, keys) {
  const sample = JSON.stringify(
    {
      name: "myapi",
      label: "我的标签源",
      search: {
        url: "https://api.example.com/search?q={artist} {title}",
        headers: { Accept: "application/json" },
        items_path: "data.items",
        fields: {
          title: "name",
          artist: "artists.0.name",
          album: "album.name",
          year: "album.publishTime",
          track_no: "no",
          duration: "duration",
          cover: "album.picUrl",
          id: "id",
        },
      },
    },
    null,
    2,
  );
  return `
    <div class="grid-2">
      <div class="panel">
        <h3>已注册来源</h3>
        <table class="data"><tbody>${providers.map((provider) => `
          <tr><td>${esc(provider.label)}</td>
            <td class="muted tiny">${esc(provider.name)}</td>
            <td>${provider.needs_network ? "需要网络" : "本地"}</td>
            <td>${provider.custom ? '<span class="badge">插件</span>' : '<span class="badge">内置</span>'}</td>
            <td>${provider.enabled ? "已启用" : "未启用"}</td>
            <td><button class="mini" data-action="test-provider" data-name="${esc(provider.name)}">测试</button></td></tr>`).join("")}
        </tbody></table>
      </div>
      <form class="panel" id="pluginForm">
        <h3>安装 JSON 插件</h3>
        <p class="muted tiny">用声明式 HTTP 映射扩展标签源，避免执行任意代码。</p>
        <label class="field" style="margin-top:10px">插件定义
          <textarea name="spec" rows="16" spellcheck="false">${esc(sample)}</textarea>
        </label>
        <button class="btn primary" type="submit" style="margin-top:10px">安装插件</button>
      </form>

      <div class="panel">
        <h3>API 密钥</h3>
        <p class="muted tiny">供脚本、MCP Agent 与智能家居调用；密钥只显示一次，权限等同当前账号。</p>
        <div class="table-wrap" style="margin-top:10px">
          <table class="data"><thead><tr><th>名称</th><th>前缀</th><th>最近使用</th><th></th></tr></thead>
          <tbody>${keys.length ? keys.map((key) => `
            <tr><td>${esc(key.name)}</td><td class="muted tiny"><code>${esc(key.prefix)}…</code></td>
              <td class="muted tiny">${esc(key.last_used_at || "从未")}</td>
              <td><button class="mini" data-action="delete-apikey" data-id="${key.id}">删除</button></td></tr>`).join("")
            : '<tr><td colspan="4" class="muted">还没有 API 密钥</td></tr>'}
          </tbody></table>
        </div>
        <form id="apiKeyForm" class="toolbar" style="margin-top:10px">
          <input type="text" name="name" placeholder="密钥名称，例如 home-assistant" style="flex:1" />
          <button class="btn primary" type="submit">创建</button>
        </form>
        <div id="apiKeyResult" class="log" style="display:none;margin-top:10px"></div>
      </div>

      <div class="panel">
        <h3>MCP / Agent 接入</h3>
        <p class="muted tiny">支持 MCP Streamable HTTP 的客户端填写：</p>
        <dl class="kv">
          <dt>地址</dt><dd><code>${esc(location.origin)}/mcp</code></dd>
          <dt>认证</dt><dd><code>Authorization: Bearer &lt;API 密钥&gt;</code></dd>
          <dt>工具</dt><dd>搜索/读取/改标签/扫描/刮削/歌单/去重/动态</dd>
        </dl>
        <pre class="log" style="margin-top:10px;max-height:160px">{
  "mcpServers": {
    "music-doctor": {
      "type": "http",
      "url": "${esc(location.origin)}/mcp",
      "headers": { "Authorization": "Bearer omt_xxx" }
    }
  }
}</pre>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------- about */
function aboutTab(settings) {
  return `
    <div class="grid-2">
      <div class="panel">
        <h3>服务信息</h3>
        <dl class="kv">
          <dt>版本</dt><dd>1.0.0</dd>
          <dt>站点名称</dt><dd><input type="text" id="siteNameInput" value="${esc(settings["site.name"] || "Music-Doctor")}" /></dd>
          <dt>Subsonic</dt><dd>${settings["subsonic.enabled"] === "1" ? "已启用" : "已关闭"}（<code>/rest/ping.view</code>）</dd>
          <dt>MCP</dt><dd><code>${esc(location.origin)}/mcp</code></dd>
          <dt>数据库</dt><dd><code>data/library.db</code></dd>
        </dl>
        <div class="toolbar" style="margin-top:12px">
          <button class="btn" data-action="save-site">保存站点名称</button>
          <a class="btn" href="/api/backup" download>下载数据库备份</a>
        </div>
        <p class="muted tiny" style="margin-top:8px">备份包含 library.db 与设置；恢复时停止服务后用备份替换 <code>data/library.db</code>。</p>
      </div>
      <div class="panel">
        <h3>客户端接入</h3>
        <p class="muted tiny">任意 Subsonic / OpenSubsonic 客户端填写：</p>
        <dl class="kv">
          <dt>服务器</dt><dd><code>${esc(location.origin)}</code></dd>
          <dt>用户名</dt><dd>你的账号</dd>
          <dt>密码</dt><dd>账号密码（支持 token 认证）</dd>
        </dl>
        <p class="muted tiny" style="margin-top:12px">
          已实现：ping、getArtists/getAlbum、search3、stream、getCoverArt、scrobble、
          playlist、star、playQueue 等。
        </p>
      </div>
      <div class="panel" style="grid-column:1/-1">
        <h3>数据与许可</h3>
        <p class="muted tiny">
          本程序是自托管的音乐标签管理工具：界面与功能依据公开文档独立实现，
          不包含任何商业版代码，也不提供激活/授权机制的绕过。删除的文件会移入 <code>data/trash</code>。
          当前数据库大小：${fmtSize(0)}。
        </p>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------- wiring */
function wire() {
  const kindSelect = document.getElementById("libKind");
  if (kindSelect) {
    const toggle = () => {
      const remote = kindSelect.value === "webdav";
      document.querySelectorAll("[data-local-only]").forEach((el) => {
        el.hidden = remote;
        const input = el.querySelector("input");
        if (input) input.required = !remote;
      });
      document.querySelectorAll("[data-remote-only]").forEach((el) => {
        el.hidden = !remote;
        const input = el.querySelector("input");
        if (input) input.required = remote && input.name === "url";
      });
    };
    kindSelect.addEventListener("change", toggle);
    toggle();
  }

  document.getElementById("libraryForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    const kind = data.get("kind") || "local";
    const body = { kind, name: data.get("name") };
    if (kind === "webdav") {
      body.url = data.get("url");
      body.username = data.get("username");
      body.password = data.get("password");
    } else {
      body.path = data.get("path");
    }
    try {
      await api("/api/libraries", { method: "POST", body });
      toast("媒体库已添加，正在扫描");
      window.dispatchEvent(new CustomEvent("refresh-view"));
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("scrapeForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const providers = [...form.querySelectorAll('input[name="providers"]:checked')].map((input) => input.value);
    const artistProviders = [...form.querySelectorAll('input[name="artistProviders"]:checked')].map((input) => input.value);
    try {
      await api("/api/settings", {
        method: "PUT",
        body: {
          "scrape.providers": providers,
          "scrape.min_score": form.min_score.value,
          "lyrics.provider": form.lyrics_provider.value,
          "scrape.write_cover": form.write_cover.checked ? "1" : "0",
          "scrape.write_lyrics": form.write_lyrics.checked ? "1" : "0",
          "scrape.auto_on_scan": form.auto_on_scan.checked ? "1" : "0",
          "scrape.write_tags": form.write_tags.checked ? "1" : "0",
          "scrape.write_lrc": form.write_lrc.checked ? "1" : "0",
          "scrape.write_cover_file": form.write_cover_file.checked ? "1" : "0",
          "artist.providers": artistProviders,
          "artist.min_score": form.artist_min_score.value,
          "artist.image_folder": form.artist_image_folder.value.trim(),
        },
      });
      toast("刮削设置已保存");
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("namingForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api("/api/settings", {
        method: "PUT",
        body: { "naming.template": form.template.value, "naming.enabled": form.enabled.value },
      });
      toast("命名模板已保存");
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("userForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    try {
      await api("/api/users", {
        method: "POST",
        body: {
          username: data.get("username"),
          password: data.get("password"),
          display_name: data.get("display_name"),
          role: data.get("role"),
        },
      });
      toast("用户已创建");
      window.dispatchEvent(new CustomEvent("refresh-view"));
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("pluginForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    let spec;
    try {
      spec = JSON.parse(event.target.spec.value);
    } catch (err) {
      return toast(`JSON 解析失败：${err.message}`, "error");
    }
    try {
      await api("/api/providers/plugins", { method: "POST", body: spec });
      toast("插件已安装");
      window.dispatchEvent(new CustomEvent("refresh-view"));
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("apiKeyForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = new FormData(event.target).get("name") || "api key";
    try {
      const data = await api("/api/apikeys", { method: "POST", body: { name } });
      const box = document.getElementById("apiKeyResult");
      box.style.display = "block";
      box.textContent = [
        "已创建（只显示一次，请立即保存）：",
        data.key,
        "",
        "用法：Authorization: Bearer " + data.key,
        "MCP：POST " + location.origin + "/mcp",
      ].join("\n");
      toast("API 密钥已创建");
    } catch (err) {
      toast(err.message, "error");
    }
  });
}
