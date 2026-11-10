# 音乐博士 · Music-Doctor

**自托管的音乐标签编辑、刮削、整理与播放系统。**
后端 **零第三方依赖**（仅 Python 标准库），前端 **零构建**（原生 ES Module），一条命令即可运行。

> **重要声明**
> 本项目依据公开教程与截图独立实现，**不包含**任何商业版代码、资源或激活机制，也**不提供**任何授权绕过。
> 仅用于自托管、学习与二次开发。详见 [docs/REA-ANALYSIS.md](docs/REA-ANALYSIS.md)。

---

## 目录

- [功能](#功能)
- [快速开始](#快速开始)
- [Docker 部署](#docker-部署)
- [使用指南](#使用指南)
- [Subsonic 客户端](#subsonic-客户端)
- [配置项](#配置项)
- [API 概览](#api-概览)
- [架构](#架构)
- [标签写入支持](#标签写入支持)
- [测试](#测试)
- [已知限制与路线图](#已知限制与路线图)
- [许可](#许可)

延伸文档：[REA 分析](docs/REA-ANALYSIS.md) · [功能矩阵](docs/FEATURE-MATRIX.md) ·
[维护者指南](docs/MAINTAINING.md) · [集成指南](docs/INTEGRATIONS.md) ·
[服务器部署记录](docs/DEPLOY-SERVER.md)

---

## 功能

对照上游官方文档的功能清单（详见 [docs/FEATURE-MATRIX.md](docs/FEATURE-MATRIX.md)）：

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| 音乐库扫描与索引 | ✅ | 本地目录 + WebDAV/AList；增量扫描、缺失标记、目录监听 |
| 标签读取/写入 | ✅ | MP3(ID3v2) / FLAC 读写；M4A 只读；其余格式仅索引 |
| 封面与歌词 | ✅ | 内嵌 + 外置 `.lrc` / `cover.jpg`，LRCLIB / 网易云歌词 |
| 异步任务中心 | ✅ | 后台线程池、进度、逐行日志、取消、SSE 实时推送 |
| 自动/手动刮削 | ✅ | MusicBrainz、网易云、文件名、JSON 插件；定时监听目录 |
| 元数据批量编辑 | ✅ | 多选批量改字段，直接写入文件 |
| 批量重命名 | ✅ | 模板预览 + 冲突检测 + 批量执行 |
| 元数据版本管理 | ✅ | 每次修改留存前后快照，可一键回退 |
| 智能歌单 | ✅ | 规则引擎（字段/操作符/组合/排序/上限），实时求值 |
| 音乐收藏与播放 | ✅ | 收藏、播放队列、断点续播、Web 播放器、MediaSession |
| 跨设备播放同步 | ✅ | 播放队列/曲目/进度持久化，登录后恢复 |
| 多端同播（一起听） | ✅ | 房间码加入、成员在线、播放状态 SSE 广播同步 |
| 角色与权限 | ✅ | admin / editor / listener 三级权限 |
| 音乐去重与洗版 | ✅ | 标题+艺术家+时长聚类，评分保留最佳版本，其余移入回收站 |
| 插件模块 | ✅ | 声明式 JSON HTTP 标签源（不执行任意代码） |
| 艺术家刮削 | ✅ | 网易云 / 维基 / Deezer：头像、别名、简介；单人 + 批量任务，可导出给 Navidrome |
| MCP / Agent 接入 | ✅ | Streamable HTTP MCP，11 个工具，API 密钥认证 |
| Subsonic / OpenSubsonic | ✅ | 浏览、搜索、串流、封面、歌单、收藏、scrobble、队列同步 |
| 云盘音乐（AList/WebDAV） | ✅ | 远程索引 + 服务端代理串流（只读，不写回网盘） |
| 刮削报告 | ✅ | 按天/来源统计、待刮削积压、失败任务 |
| API 密钥 / 备份 | ✅ | 脚本与 Agent 用密钥；数据库一键下载备份 |
| 变量模板重命名 | ✅ | 与文档一致的 `${...}` 表达式语法 |
| 小爱音箱 | ⚠️ | 不内置米家协议；通过 Home Assistant / xiaomusic 间接接入（见 INTEGRATIONS） |
| 单点登录 SSO | ❌ | 尚未实现（路线图） |
| 激活码 / 授权 | 🚫 | 商业授权机制，明确不实现、不绕过 |

---

## 快速开始

### 环境

- Python **3.11+**（开发验证于 3.13），无需 `pip install`
- 任意现代浏览器

### 运行

```bash
git clone https://github.com/no1very/music-doctor.git
cd music-doctor
python run.py
```

打开 <http://127.0.0.1:8318>，用户名 `admin`，初始密码在 `data/INITIAL_ADMIN.txt`（首次启动生成）。

```bash
# 生成一份演示音乐库（需要 ffmpeg；没有则写入静音帧）
python tools/make_demo_library.py demo-music

# 自定义端口 / 数据目录
python run.py --port 9000 --data-dir ./mydata
```

首次使用：

1. **设置 → 媒体库** 添加音乐目录（例如 `demo-music` 或 `D:/Music`），点「扫描」。
   也可以选 **WebDAV / AList** 添加云盘目录（只读）。
2. **任务中心** 查看扫描进度；扫描完成后曲目进入音乐库。
3. **音乐库** 勾选曲目 → 批量刮削 / 批量改标签 / 批量重命名；点击标题进入编辑器。
4. **设置 → 刮削与标签** 选择标签源（MusicBrainz / 网易云 / 文件名）与最低匹配分。
5. **设置 → 插件与接口** 创建 API 密钥，即可用脚本或 MCP Agent 管理曲库
   （配置见 [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)）。

---

## Docker 部署

```bash
docker build -t open-music-tag .
docker run -d --name open-music-tag \
  -p 8318:8318 \
  -v /path/to/music:/music:ro \
  -v ./data:/data \
  open-music-tag
```

或使用 Compose：

```bash
docker compose up -d
```

`docker-compose.yml` 默认把宿主音乐目录挂到容器内 `/music`，添加媒体库时填写 `/music`。
镜像基于 `python:3.12-slim`，运行时只执行标准库代码，无需联网安装依赖。

已在 Armbian/arm64 主机上通过 Docker Compose 部署验证（容器 `open-music-tag`，端口 8318，
数据目录持久化挂载，音乐目录以可写方式挂载）。目录布局、升级、构建注意事项、备份与排障见
[服务器部署记录](docs/DEPLOY-SERVER.md)。

---

## 使用指南

### 刮削

- **自动**：`扫描后自动刮削` 打开后，扫描任务结束会对未刮削曲目排队刮削；
  目录监听线程每 `library.watch_interval` 秒检查新增文件。
- **手动**：单曲编辑器 → 刮削标签页 → 搜索匹配 → 选候选「应用」；
  或音乐库多选 → 批量刮削。
- **匹配分**：标题 55% + 艺术家 30% + 专辑 10% + 时长 5%（无专辑时重新加权），
  低于 `scrape.min_score` 不会自动写入。

### 变量模板

与上游文档一致：只解析 `${...}`，内部是受限的 Python 表达式（白名单校验，不可执行任意代码）。

```
${discnumber.zfill(2) + "_" if discnumber and tracknumber else ""}${tracknumber.zfill(2) + ". " if tracknumber else ""}${title or "未知标题"} - ${artist or albumartist or "未知歌手"}
```

可用变量：`title` `artist` `first_artist` `first_artist_letter` `album` `albumartist`
`discnumber` `tracknumber` `filename` `file_song_name` `parent_path` `parent_parent_path`
`lyrics` `comment` `year` `genre` `composer` `lyricist` `counter` `null`。

### 智能歌单

规则由「字段 + 操作符 + 值」组成，可全部匹配或任一匹配，按标题/入库时间等排序并限制数量。
示例：`流派 包含 Rock` 且 `年份 介于 1990~2000`，按播放次数降序取 100 首。
智能歌单每次打开时实时求值，新增歌曲自动进入。

### 艺术家页

艺术家列表按姓名/歌曲数/专辑数浏览与搜索；点进艺术家先显示**全部歌曲**（可直接播放/编辑），
下方再列出专辑。歌曲列表里的艺术家名可点击跳转。

### 艺术家刮削

和原版一样，艺术家资料是独立刮削的：

- **单个**：艺术家详情页 → 「刮削艺术家资料」→ 选候选（头像/别名/简介/匹配度）→ 应用。
- **批量**：艺术家列表右上「刮削全部艺术家」→ 后台任务中心执行；
  默认只处理**还没有头像**的艺术家（按歌曲数排序，单次 300 位，可重复运行补齐）。
- **来源与阈值**：设置 → 刮削与标签 → 艺术家刮削（网易云 / 维基百科 / Deezer、最低匹配分）。
- 刮到的头像存在 `covers` 表，艺术家页与 Subsonic `getArtistInfo2`（简介 + 头像 URL）都会使用；
  也可通过 `PATCH /api/artists/{id}` 手动改简介/别名/头像。

#### 同步到 Navidrome

Navidrome 不读本项目的数据库，但它 0.62+ 支持 `ArtistImageFolder`：

1. 设置 → 刮削与标签 → **艺术家图片导出目录** 填 Navidrome 能读到的路径，例如 `/music/artist`；
   之后每次刮削会自动写出 `<艺术家名>.jpg`，也可在艺术家列表点「导出图片到 Navidrome」全量导出。
2. Navidrome 侧设置：
   ```yaml
   ND_ARTISTIMAGEFOLDER: /music/artist
   ND_ARTISTARTPRIORITY: image-folder, artist.*, album/artist.*, external
   ```
   重启后**必须重新解析一次**，否则 Navidrome 会沿用之前缓存的“无图”结果。这一步已经集成进项目：
   导出图片 / 刮削艺术家后会自动排队执行（任务中心显示「Navidrome 图像缓存重算」），
   手动重算与排查用：
   ```bash
   python tools/navidrome.py status                  # 总览：哪些艺术家没图、命中什么来源
   python tools/navidrome.py reprocess               # 重算全部艺术家图片缓存
   python tools/navidrome.py explain ar-<艺术家ID>    # 某位艺术家为什么有/没有图
   python tools/navidrome.py doctor                  # status + 仅当图片目录变化时 reprocess
   ```
   Navidrome 在 NAS 上时加 `--ssh nas`（`~/.ssh/config` 别名或 `user@host`）；
   本项目跑在容器里（没有 docker CLI）时，脚本会说明原因并打印等效命令，不会让刮削/导出失败。
   底层等价于：
   ```bash
   docker exec navidrome /app/navidrome artwork reprocess --kind ar -y --datafolder /data
   docker exec navidrome /app/navidrome artwork explain ar-<艺术家ID> --datafolder /data
   docker exec navidrome /app/navidrome artwork status --datafolder /data
   ```
   用 `artwork explain ar-<艺术家ID>` 可以看到 `image-folder hit /music/artist/刘德华.jpg`。
3. **简介**：Navidrome 的艺术家简介只来自它自己的代理（Last.fm / Spotify / Deezer），
   不会读取本项目的简介。可启用 Navidrome 的 Last.fm（`ND_LASTFM_ENABLED=true` + Key，
   建议 `ND_LASTFM_LANGUAGE=zh`）获得简介；或者在 Navidrome 以外的界面
   （本项目 / Subsonic 客户端）查看刮削结果。

### 音乐去重

`音乐去重` 页按「标题 + 艺术家 + 时长（容差可配）」聚类，并单独列出**完全相同的文件**（快速哈希）。
每组按无损优先、码率、位数、体积评分，标出建议保留项；清理时把文件移入 `data/trash/<时间戳>/`。

### 批量重命名

音乐库多选 → 批量重命名 → 填写模板 → **预览**（显示新旧文件名与冲突）→ 执行。
只改文件名，不移动目录；目标已存在会跳过并报告。

### 外置歌词与封面

`设置 → 刮削与标签` 可开启：

- **外置 .lrc**：写入/更新与音频同名的 `.lrc`；扫描时若内嵌歌词为空会自动读取它；
- **外置 cover.jpg**：把封面写到专辑目录；扫描时若内嵌封面为空会自动导入；
- **写回音频标签**：关闭后只更新数据库索引（远程库始终只读）。

### 刮削报告

`刮削报告` 页按天与来源统计写入量，并列出待刮削积压、重复组与最近失败任务。

### 远程库（WebDAV / AList）

设置 → 媒体库 → 类型选 WebDAV/AList，填 `/dav` 地址与账号。远程曲目按目录结构推断
`艺术家/专辑/标题`，可搜索、刮削、加歌单、播放；**不支持写回网盘**。
播放由服务端代理并转发 Range，客户端无需直连网盘。

### MCP / Agent

服务端在 `POST /mcp` 提供 MCP Streamable HTTP（11 个工具：搜索/读取/改标签/扫描/刮削/
歌单/去重/动态）。用 API 密钥认证，Claude Desktop、Cursor 等客户端可直接接入。
配置与工具清单见 [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)。

### 一起听（多端同播）

`一起听 → 创建房间`，把 6 位房间码发给朋友；成员打开房间后点击「同步到此设备」。
播放/暂停/切歌会通过 SSE 广播，其他成员自动跟随。

---

### 修改密码

- **改自己的**：登录后点侧边栏用户区的 **⚿**，输入当前密码与新密码。
  修改成功后所有设备上的登录会失效，需要用新密码重新登录。
- **管理员重置他人**：设置 → 用户与权限 → 对该用户点「重置密码」。
- **忘记密码（服务器上）**：

  ```bash
  docker exec -it open-music-tag python run.py --reset-password admin
  # 按提示输入两次新密码；非交互可用 --password '新密码'（会留在命令历史里）
  ```

  该命令会同时注销该用户的所有会话。Linux 源码运行时去掉 `docker exec ...` 前缀，
  加 `--data-dir /path/to/data`。

### Subsonic 客户端

服务器地址填 `http://<host>:8318`，用户名/密码用网页账号即可（同时支持 token 认证与 `enc:` 密码）。

已实现：`ping` `getLicense` `getMusicFolders` `getIndexes` `getArtists` `getArtist` `getArtistInfo2`
`getAlbum` `getAlbumList(2)` `getSong` `getRandomSongs` `getGenres` `search2/3` `stream` `download`
`getCoverArt` `scrobble` `star/unstar` `getStarred(2)` `getPlaylists` `getPlaylist` `createPlaylist`
`updatePlaylist` `deletePlaylist` `getPlayQueue` `savePlayQueue` `getUser` `getNowPlaying` `getLyrics`
`getLyricsBySongId` `getScanStatus` `startScan` `getOpenSubsonicExtensions`。

已验证客户端兼容性目标：DSub、Symfonium、Feishin、Sonixd、音流、棉花音乐等。

---

## 配置项

全部通过环境变量覆盖：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MTW_DATA_DIR` | `./data` | 数据目录（SQLite、密钥、日志、回收站、插件） |
| `MTW_HOST` / `MTW_PORT` | `0.0.0.0` / `8318` | 监听地址与端口 |
| `MTW_SECRET_KEY` | 自动生成 | 会话签名与密码加密密钥；默认存于 `data/secret.key` |
| `MTW_ADMIN_PASSWORD` | 随机 | 首次初始化时的管理员密码 |
| `MTW_WORKERS` | `2` | 后台任务并发数 |
| `MTW_SESSION_DAYS` | `30` | 登录有效期 |
| `MTW_READ_ONLY` | `0` | 只读模式：不写回音频文件 |
| `MTW_USER_AGENT` | 内置 | 刮削请求 UA（MusicBrainz 要求可识别 UA） |
| `MTW_MAX_UPLOAD_MB` | `64` | 请求体上限（封面内联上传等） |

站点级配置（`settings` 表）通过界面修改：标签源、最低匹配分、歌词源、命名模板、目录监听间隔等。

---

## API 概览

除 Subsonic 外均为 JSON REST，认证用 `Authorization: Bearer <token>` 或 `omt_session` Cookie。

```
POST /api/auth/login            登录（返回 token 并下发 Cookie）
GET  /api/stats                 仪表盘统计
GET  /api/tracks?q=&sort=&tagged=0
PATCH/api/tracks/{id}           修改标签并写入文件
GET  /api/tracks/{id}/versions  版本历史
POST /api/tracks/{id}/versions/{vid}/restore
POST /api/tracks/batch          批量刮削/编辑/重读/删除/重命名
POST /api/tracks/rename/preview 重命名预览（冲突检测）
GET  /api/tracks/{id}/candidates 刮削候选
POST /api/tracks/{id}/scrape    应用候选
POST /api/libraries/{id}/scan   创建扫描任务（本地或 WebDAV）
GET  /api/tasks/{id}            任务详情（含日志）
GET  /api/events                SSE：任务进度、房间状态、库变更
GET  /api/duplicates            重复分组
GET  /api/reports/scrape        刮削报告
POST /api/playlists             （智能）歌单
GET  /api/apikeys               个人 API 密钥
GET  /api/backup                数据库备份（zip）
POST /mcp                       MCP JSON-RPC（Streamable HTTP）
```

完整路由见 `app/api.py`（装饰器即路由表）。

---

## 架构

```
run.py                 入口：初始化数据库、启动 HTTP 与后台线程
app/
  config.py            环境配置
  db.py                SQLite schema（含 albums/artists 视图）、设置、迁移
  security.py          PBKDF2 密码、HMAC token、角色
  secretbox.py         可逆密码存储（Subsonic token、WebDAV 密码）
  apikeys.py           个人 API 密钥
  server.py            路由、静态资源、Range 串流、远程代理、SSE
  api.py               REST 接口（认证/库/曲目/歌单/任务/去重/报告/设置/备份）
  subsonic.py          Subsonic / OpenSubsonic 兼容层
  mcp.py               MCP Streamable HTTP（JSON-RPC + 工具）
  tags/                自研标签引擎：id3.py / flac.py / mp4.py / probe.py
  scanner.py           目录扫描与索引（本地 + 远程分流）
  webdav.py            WebDAV/AList 索引、PROPFIND 解析、远程流
  library.py           曲目模型、编辑、版本、外部 .lrc/封面、智能查询、去重
  tasks.py             任务中心（线程池 + 持久化进度/日志）
  watcher.py           目录监听 → 自动扫描/刮削
  scrape.py            刮削管线（候选、封面、歌词、批量）
  providers/           标签源：filename / musicbrainz / netease / lrclib / JSON 插件
  variables.py         ${...} 模板（AST 白名单）
  events.py            SSE 事件总线
web/                   无构建前端（index.html + styles.css + js/*）
tools/                 e2e.py、ui_smoke.mjs、make_demo_library.py
tests/                 unittest：标签/模板/安全/MCP/WebDAV/迁移
docs/                  REA 分析、功能对照、维护者指南、集成指南
```

### 线程与一致性

- HTTP 用 `ThreadingHTTPServer`，每个线程持有自己的 SQLite 连接（WAL 模式）。
- 写音频文件采用「临时文件 + 原子替换」，失败不会破坏原文件。
- 任务进度/日志写库并通过 SSE 推送；前端断线自动重连。

---

## 标签写入支持

| 格式 | 读取 | 写入 | 实现 |
| --- | --- | --- | --- |
| MP3 | ✅ ID3v2.2/2.3/2.4 + ID3v1 回退 | ✅ ID3v2.3（自动 UTF-16） | `app/tags/id3.py` |
| FLAC | ✅ STREAMINFO / Vorbis / PICTURE | ✅ 重写注释与封面块 | `app/tags/flac.py` |
| M4A / MP4 | ✅ ilst / mvhd | ❌ 只读（避免破坏 stco 偏移） | `app/tags/mp4.py` |
| OGG/Opus/WAV/APE/DSF | ⚠️ 仅索引与基础探测 | ❌ | — |

写 MP3 时会保留未识别的帧（如 ReplayGain、自定义 TXXX），避免第三方标签丢失。

---

## 测试

```bash
# 单元测试（34 项：标签引擎、模板、安全、MCP、WebDAV、艺术家资料、迁移、外置文件）
python -m unittest discover -s tests -v

# 端到端接口测试（需先启动服务，81 项断言，含内置 WebDAV 假服务器）
python tools/e2e.py --base http://127.0.0.1:8318 --library ./demo-music

# 前端启动 + 视图渲染测试（无需浏览器，22 项起，含 SPA 启动）
node tools/ui_smoke.mjs
```

`tools/e2e.py` 会验证：登录/权限、扫描任务、标签写回与回滚、批量编辑、静态与智能歌单、
去重接口、Range 串流、Subsonic token 认证、SSE、用户权限隔离、模板重命名、
**API 密钥/MCP 工具调用、外置 .lrc、重命名预览、刮削报告、WebDAV 远程库扫描与代理串流、
自助改密与管理员重置密码**。
`tools/ui_smoke.mjs` 会在 Node 的 DOM shim 中真正 import `app.js`、触发 `DOMContentLoaded`
（验证启动与登录/主界面切换），再渲染全部页面与编辑器，捕获运行期错误。

---

## 已知限制与路线图

- **不做商业授权逻辑**：无激活码、无授权校验（这是刻意的边界）。
- **远程库只读**：WebDAV/AList 曲目不写回标签，只维护本地索引；重命名也不支持。
- **M4A 仅读取标签**；写入需要重写 moov 并修补 stco，尚未实现。
- **未实现**：SSO/OIDC、MySQL/Redis 外置存储、原生小爱/米家协议。
  小爱可通过 Home Assistant + xiaomusic 间接接入，见 [集成指南](docs/INTEGRATIONS.md)。
- 刮削源只使用公开 API；上游可能存在的私有聚合源不在本项目内。
- 多用户同时写同一文件未加锁；单人/家庭场景足够，大规模并发建议串行化任务。
- 大曲库（>20 万首）建议：先跑一次全量扫描，再用 `sort=added` 分批刮削；
  SQLite 已建常用索引，必要时加 FTS5（见维护者指南路线图）。
- 播放统计以客户端上报为准（Web 端播放 10 秒或一半时长后上报一次）。
- 前端运行时验证使用 Node 中的 DOM shim（构建环境无法启动 Chromium）；
  真实浏览器中的交互建议按 [维护者指南](docs/MAINTAINING.md) 的清单人工回归。

---

## 许可

本项目以 **MIT** 许可发布（见 [LICENSE](LICENSE)）。
它不包含、不派生 Music Tag Web 的代码；Subsonic API 仅依据其公开规范实现。
“Music Tag Web” 是其作者的商标/产品名，本仓库仅用于说明兼容目标。
