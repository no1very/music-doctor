# 集成指南

本页把 Music-Doctor 接到常见客户端、脚本、智能家居与 MCP Agent 上。
所有示例都使用你自己的服务器地址、账号与 API 密钥。

---

## 1. Subsonic / OpenSubsonic 客户端

- 服务器：`http://<host>:8318`（HTTPS 反代则填域名）
- 用户名 / 密码：网页账号
- 认证方式：**token** 或 **密码** 都可以；若客户端支持 OpenSubsonic，会看到
  `getOpenSubsonicExtensions` 声明的 4 个扩展

已验证/目标客户端：DSub、Symfonium、Feishin、Sonixd、音流、棉花音乐、箭头音乐、Web App。
常见客户端只支持 `/rest/*.view` 形式，本项目同时接受不带 `.view` 的路径。

```bash
# 连通性自检（把 u/p 换成你的账号）
curl "http://127.0.0.1:8318/rest/ping.view?u=admin&p=yourpass&v=1.16.1&c=curl&f=json"
```

---

## 2. API 密钥与脚本

设置 → 插件与接口 → API 密钥 → 创建。密钥形如 `omt_xxxxxxxx`，**只显示一次**，权限等于当前账号。

```bash
KEY=omt_xxx
BASE=http://127.0.0.1:8318

# 统计
curl -H "Authorization: Bearer $KEY" "$BASE/api/stats"

# 搜索
curl -H "Authorization: Bearer $KEY" "$BASE/api/tracks?q=Airbag&limit=5"

# 改标签（会写文件并记录版本）
curl -X PATCH -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"genre":"Alternative Rock"}' "$BASE/api/tracks/12"

# 触发扫描
curl -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{}' "$BASE/api/libraries/1/scan"
```

`X-API-Key: $KEY` 与 `Authorization: Bearer $KEY` 等价。

---

## 3. MCP（让 AI Agent 管理曲库）

服务端实现了 MCP Streamable HTTP：`POST /mcp`，支持 `initialize`、`tools/list`、
`tools/call`、`resources/list|read`、`ping`。

可用工具：

| 工具 | 作用 | 权限 |
| --- | --- | --- |
| `library_stats` | 曲库概览 | listener |
| `search_tracks` | 按标题/艺术家/专辑/流派搜索 | listener |
| `get_track` | 单曲完整元数据 + 版本数 | listener |
| `update_track` | 改标签并写文件 | editor |
| `scan_library` | 触发扫描任务 | editor |
| `scrape_track` | 返回候选或直接应用最佳匹配 | editor |
| `list_playlists` / `create_playlist` | 歌单（含智能规则） | listener |
| `list_duplicates` | 重复分组与建议保留项 | listener |
| `recent_activity` | 最近播放与任务 | listener |

Claude Desktop / Cursor / 其他客户端配置示例：

```json
{
  "mcpServers": {
    "open-music-tag": {
      "type": "http",
      "url": "http://127.0.0.1:8318/mcp",
      "headers": { "Authorization": "Bearer omt_xxxxxxxx" }
    }
  }
}
```

手动验证：

```bash
curl -X POST http://127.0.0.1:8318/mcp \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Agent 提示词示例：

> 用 open-music-tag 的 MCP 工具检查所有未刮削的曲目，先搜索候选并把匹配度低于 0.8 的列出来，
> 不要直接写入；我确认后再改。

---

## 4. 云盘 / AList（WebDAV）

AList 打开「WebDAV」后，地址形如 `http://<alist>:5244/dav`：

1. 设置 → 媒体库 → 类型选 **WebDAV / AList**；
2. 地址填 `http://<alist>:5244/dav/音乐`（结尾斜杠可有可无）；
3. 填 AList 用户名/密码（不是站点管理员密码，而是 AList 账号）；
4. 保存后扫描。

行为说明：

- 曲目按文件名与目录结构（`艺术家/专辑/曲目`）推断初始元数据；
- 远程文件**只读**：不写回标签，但可以刮削、改索引、加歌单、收藏、播放；
- 播放与下载由服务端代理并转发 Range，客户端无需直接访问网盘；
- 目录监听对远程库同样生效（定期 PROPFIND）。

---

## 5. 智能音箱 / 小爱同学

**没有**内置小米账号协议（那需要模拟米家私有接口，既不稳定也不适合长期维护）。
推荐两条可持续的路径：

### 5.1 Home Assistant + xiaomusic

1. 在 HA 里装 [xiaomusic](https://github.com/hanxi/xiaomusic)，把音乐目录指向 NAS 上的同一份文件；
2. 用 HA 自动化调用 xiaomusic 的播放服务，或用 `rest_command` 调用本项目的 API：

```yaml
rest_command:
  omt_scan:
    url: "http://nas:8318/api/libraries/1/scan"
    method: POST
    headers:
      Authorization: "Bearer omt_xxxxxxxx"
      Content-Type: application/json
    payload: "{}"
```

3. 语音口令 → 场景 → 先 `omt_scan`，再让 xiaomusic 刷新曲库并播放。

### 5.2 纯 HTTP 轮询

```bash
# 最近播放（可让音箱的自动化读取后决定播什么）
curl -H "Authorization: Bearer $KEY" "http://nas:8318/api/history?limit=5"
```

如果之后要原生对接，建议新增 `app/providers` 之外的 `app/casters/` 抽象，
把"播放指令"抽象成适配器，而不是把某个厂商协议写进核心。

---

## 6. 反向代理（Nginx）

需要正确处理 SSE、Range 与大响应：

```nginx
location / {
    proxy_pass http://127.0.0.1:8318;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # SSE（/api/events）
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;

    # 音频 Range 与上传
    proxy_set_header Range $http_range;
    proxy_set_header If-Range $http_if_range;
    client_max_body_size 64m;
}
```

HTTPS 下记得让 `Secure` Cookie 生效：本项目的 Cookie 是 `HttpOnly; SameSite=Lax`，
由浏览器按 HTTPS 自动处理；若要跨站嵌入再考虑调整 SameSite。

---

## 7. 数据交换

- **备份**：`GET /api/backup`（admin）返回 zip（`library.db` + `settings.json`）。
- **导出歌单**：目前通过 API 读取 `GET /api/playlists/{id}` 自行生成 M3U；
  可按 `#EXTM3U` + `#EXTINF:<秒>,<艺术家> - <标题>` 的格式落地。
- **导入**：批量编辑/重命名都走 REST，脚本化即可。

---

## 8. Navidrome 艺术家头像

Navidrome 不读本项目的数据库，需要走它的 `ArtistImageFolder`：

1. 本项目：设置 → 刮削与标签 → 艺术家图片导出目录填 `/music/artist`（或任何 Navidrome
   容器能读到的目录），刮削时自动写 `<艺术家名>.jpg`；也可在艺术家列表点「导出图片到 Navidrome」。
2. Navidrome 环境变量：
   ```yaml
   ND_ARTISTIMAGEFOLDER: /music/artist
   ND_ARTISTARTPRIORITY: image-folder, artist.*, album/artist.*, external
   ```
3. 重启 Navidrome；图片按艺术家名（忽略大小写）匹配，同名 `.jpg/.png/.webp` 都行。
   **关键一步**：改完 `ArtistArtPriority` 后要重新解析一次，否则 Navidrome 沿用旧的“无图”结果。
   这一组命令已集成到项目（`app/navidrome.py` + `tools/navidrome.py`），导出/刮削后自动排队：
   ```bash
   python tools/navidrome.py status                  # artwork status：总览
   python tools/navidrome.py reprocess               # artwork reprocess --kind ar -y
   python tools/navidrome.py explain ar-<艺术家ID>    # artwork explain
   python tools/navidrome.py doctor                  # 只在图片目录变化时重算
   ```
   Navidrome 在 NAS 上时加 `--ssh nas`；等价原始命令：
   ```bash
   docker exec navidrome /app/navidrome artwork reprocess --kind ar -y --datafolder /data
   docker exec navidrome /app/navidrome artwork explain ar-<艺术家ID> --datafolder /data
   ```
   关闭自动重算：设置环境变量 `MTW_NAVIDROME_AUTOREPROCESS=0`。
4. 简介：Navidrome 只用自己的代理（Last.fm / Spotify / Deezer），不会读本项目的简介。
   可开启 `ND_LASTFM_ENABLED=true` 获得 Last.fm 简介；Deezer 在当前网络会超时。

## 9. 移动端后台播放

Web 播放器支持 MediaSession（锁屏信息、耳机按键、后台播放）。
若需要真正的离线缓存/后台播放，建议用现成 Subsonic 客户端（第 1 节），
而不是在浏览器里硬做——这也是上游生态的通行做法。
