# 维护者指南

面向接下来由你自己维护这个项目。目标是：**一年后回来，仍能读懂、改得动、升级不丢数据。**

---

## 1. 设计约束（请先读）

| 决定 | 原因 | 代价 |
| --- | --- | --- |
| 只用 Python 标准库 | 任何 NAS / 容器都能跑，`python run.py` 即启动 | 没有 FastAPI/Django 的生态，HTTP/ORM 要自己写 |
| SQLite 单文件 | 零运维、易备份 | 不能多实例水平扩展；大库（>20 万首）需注意索引 |
| 原生 ES Module 前端 | 没有 npm/构建链，改完刷新即生效 | 没有 Vue/React 的组件生态 |
| 标签引擎自研 | 不依赖 mutagen，格式行为完全可控 | 新格式需要自己实现解析 |
| 声明式 JSON 插件 | 不执行用户代码，避免 RCE | 复杂 API 需要中间层 |

这些不是"待改进"，而是刻意的取舍。改动前先确认新方案不会破坏"单文件 + 零依赖"的部署体验。

---

## 2. 目录与请求生命周期

```
run.py                 初始化 DB → serve() → 主线程等待信号
app/server.py          ThreadingHTTPServer：解析请求 → 认证 → 路由 → Response
  ├─ /api/*            app/api.py 中的 @route 函数
  ├─ /rest/*           app/subsonic.py
  ├─ /mcp              app/mcp.py（JSON-RPC）
  └─ 其它              web/ 静态文件
app/tasks.py           ThreadPoolExecutor + SQLite 任务状态 + SSE 事件
app/watcher.py         每 30 秒检查是否需要自动扫描
```

请求进入 `AppHandler._handle` 后依次做：

1. 解析 path/query/cookie/body（限制 `MTW_MAX_UPLOAD_MB`）；
2. 认证：`X-API-Key` → `Bearer omt_...` → 会话 token → Cookie；
3. 特殊前缀分流（`/rest/`、`/mcp`）；
4. 匹配 `router`（`{param}` 会转成命名正则）；
5. 调用处理函数，捕获 `ApiError/文件/权限` 等异常转 JSON；
6. 输出：普通 JSON、文件（Range）、远程代理、SSE。

新增接口只需要：

```python
@route("POST", "/api/example/{item_id}")
def example(req: Request, item_id: str):
    req.require("editor")          # listener/editor/admin
    data = req.json()
    return {"ok": True}            # dict 自动变 JSON
```

---

## 3. 数据库与迁移

- 所有表在 `app/db.py` 的 `SCHEMA` 中（`CREATE TABLE IF NOT EXISTS`）。
- `v_albums` / `v_artists` / `v_duplicates` 是视图，**每次启动重建**（`init_db` 先 DROP）。
- 迁移规则：**只做加列**，在 `_migrate()` 里判断 `PRAGMA table_info` 后 `ALTER TABLE`，
  并递增 `SCHEMA_VERSION`。不要改已有列的类型或语义。
- 删除列/表会让老库无法回退；如果必须做，先写升级脚本并文档化。

新增字段的标准步骤：

1. `SCHEMA` 里给新库加上列；
2. `_migrate()` 里给旧库 `ALTER TABLE ... ADD COLUMN`；
3. `SCHEMA_VERSION += 1`；
4. 更新 `library.py` 的读写与序列化；
5. 跑 `python -m unittest discover -s tests` 与 e2e。

---

## 4. 扩展点

### 4.1 新增刮削源（代码）

实现 `app/providers/__init__.py` 的协议即可：

```python
class MyProvider:
    name = "my"
    label = "My source"
    needs_network = True

    def search(self, query: dict) -> list[Candidate]:
        ...
```

然后在文件底部 `registry.register(MyProvider())`。`query` 含
`title/artist/album/albumartist/duration/track_no/path`；返回的 `Candidate` 会被统一评分。
需要歌词的源再加一个 `lyrics(query) -> str` 方法。

### 4.2 新增刮削源（不改代码）

设置 → 插件与接口 → 安装 JSON 插件：

```json
{
  "name": "myapi",
  "label": "我的标签源",
  "search": {
    "url": "https://api.example.com/search?q={artist} {title}",
    "headers": {"Accept": "application/json"},
    "items_path": "data.items",
    "fields": {"title": "name", "artist": "artists.0.name", "album": "album.name",
               "year": "album.publishTime", "cover": "album.picUrl", "id": "id"}
  }
}
```

`items_path` 支持 `a.b[0].c` 形式的路径。

### 4.3 新增前端页面

1. `web/js/views/yourview.js` 导出 `async function yourview(root, route, ctx)`；
2. 在 `web/js/views/index.js` 的 `ROUTES` 注册；
3. 侧边栏 `web/index.html` 加导航项（`data-route` 用于高亮）；
4. 需要刷新时调用 `ctx.refresh()`；全局操作在 `app.js` 的 `data-action` switch 里加分支。

前端没有状态管理库：`state` 在 `core.js`，跨页面只放"筛选条件/选中项/播放器"这类必要状态。

### 4.4 后台任务

```python
task_id = tasks.start(
    "my-task", "标题", {"payload": ...},
    lambda ctx: do_work(ctx), user_id,
)
```

`ctx.log(msg, level)`、`ctx.progress(done, total)`、`ctx.check_cancel()`。
任务状态写库，前端通过 `GET /api/tasks/{id}` 与 SSE `task.*` 消费。

### 4.5 Navidrome 图像维护（已内置）

改过 Navidrome 的 `ArtistArtPriority` / `ArtistImageFolder` 后，它仍按**每位艺术家缓存**的
“有无图片”结果出图；配置文件重启也不失效，必须重算一次缓存：

```bash
navidrome artwork reprocess --kind ar -y --datafolder /data   # 重算全部
navidrome artwork explain ar-<艺术家ID> --datafolder /data     # 某位为什么有/没有图
navidrome artwork status --datafolder /data                    # 总览
```

这三条**不要散落在脚本和文档里各写一遍**，统一走：

- `app/navidrome.py`：拼命令行、选传输方式（本机 `docker exec` / `--ssh`）、执行、记录。
- `tools/navidrome.py`：`status` / `reprocess` / `explain ar-<id>` / `doctor` / `hooks`。
- 自动触发：`POST /api/artists/export`、单个艺术家刮削、批量刮削任务在写图成功后调用
  `navidrome.after_export()`，排队一个「Navidrome 图像缓存重算」后台任务（同时只允许一个，
  重复请求合并）。`doctor` 用 `data/navidrome-artwork.json` 里的 `image_signature`（文件数 + 最新 mtime）
  判断要不要重算。

环境变量（都在 `navidrome.settings()` 里读，容器与 CLI 一致）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MTW_NAVIDROME_CONTAINER` | 自动探测 | 容器名，先找同名的，再按镜像/名字含 `navidrome` |
| `MTW_NAVIDROME_DATA_FOLDER` | `/data` | 容器内 `--datafolder` |
| `MTW_NAVIDROME_SSH` | 空 | 填 `nas` / `user@host` 则通过 SSH 执行（Navidrome 在 NAS 上时用这个） |
| `MTW_NAVIDROME_DOCKER` | `docker` | docker 可执行文件路径 |
| `MTW_NAVIDROME_TIMEOUT` | `900` | 单次命令超时（秒）；重算 500+ 艺术家会慢 |
| `MTW_NAVIDROME_AUTOREPROCESS` | `1` | `0` 关闭写图后的自动重算 |
| `MTW_NAVIDROME_LOCAL_IMAGE_FOLDER` | 空 | 宿主机上同一图片目录的路径（容器内看到的是 `/music/artist`，宿主机可能是 `/diskc/Music/artist`）；宿主机 cron 跑 `doctor` 时必须设，否则算不出签名 |

失败语义：`after_export()` 永不抛出。容器内没有 docker/ssh 时只返回 `reason`（接口里是
`result.navidrome`，前端 toast 会提示），导出/刮削本身不受影响。

---

## 5. 备份、恢复与升级

- **备份**：设置 → 关于 → 下载数据库备份（`library.db` + `settings.json` 的 zip）。
  备份使用 SQLite online backup API，不需要停服。
- **恢复**：停止进程 → 用备份替换 `data/library.db`（同时删除 `-wal/-shm`）→ 启动。
- **升级**：替换 `app/`、`web/`、`run.py` 后重启；`init_db` 会自动迁移并重建视图。
  **降级不受支持**：老版本不认识新列，但通常能启动（多余列被忽略）；仍建议先备份。
- `data/secret.key` 必须保留：它用于会话签名与 Subsonic 密码加密，丢失会导致所有人重新登录、
  且 Subsonic token 认证失效（重新设置密码即可恢复）。

---

## 6. 测试与发布

```bash
python -m unittest discover -s tests -v     # 标签引擎/模板/安全/MCP/WebDAV/迁移/Navidrome 命令拼装
python run.py &                             # 或后台启动
python tools/e2e.py --library demo-music    # 81 项接口断言（含 WebDAV 假服务器）
node tools/ui_smoke.mjs                     # SPA 启动 + 22 项视图渲染（无浏览器）
python tools/navidrome.py hooks             # 只打印 artwork 命令，便于确认参数
```

发布前：

1. 改 `app/config.py` 的 `version` 与 `package.json`；
2. `python tools/make_demo_library.py demo-music` 重新生成演示库；
3. 重新打 zip（排除 `data/ tmp/ tests/.tmp/ __pycache__/`）；
4. 若改了 schema，在 `docs/` 记一条迁移说明。

---

## 7. 常见问题排查

| 症状 | 检查 |
| --- | --- |
| 启动即退出 | 端口占用（`MTW_PORT`）；`data/` 不可写 |
| 扫描很慢 | 大目录首次扫描正常；看任务日志；WebDAV 走网盘带宽 |
| 刮削一直 0 结果 | 出站网络/代理；`MTW_USER_AGENT` 是否可识别；MusicBrainz 限速 1 req/s |
| 封面抓不到 | Cover Art Archive 偶发超时/404，候选可能没有 front 图 |
| 写标签失败 | 文件只读、格式不支持（M4A）、或 `MTW_READ_ONLY=1` |
| WebDAV 401 | 用户名/密码；AList 需开启 WebDAV 并使用 `/dav` 路径 |
| Subsonic 客户端 token 登录失败 | 账号是否通过网页改过密码；`data/secret.key` 是否丢失 |
| 页面 401 循环 | 反向代理丢 Cookie/Authorization；检查 `X-Forwarded-*` 与 HTTPS |
| 页面纯黑 / 只有“正在加载” | 脚本启动失败或浏览器缓存了旧 JS：强制刷新（Ctrl+F5）；打开控制台看第一条报错；启动异常现在会显示“初始化失败”而不是黑屏 |
| 部署后仍是旧界面 | 静态资源已改为 `no-store`；首次升级需强制刷新一次，之后自动生效 |
| Navidrome 艺术家头像不出现 | 改过 `ArtistArtPriority`/`ArtistImageFolder` 后必须重算缓存：`python tools/navidrome.py doctor`（或 `reprocess`）；用 `explain ar-<id>` 确认命中 `image-folder`；命令拼装见 4.5 |
| SSE 不实时 | 代理缓冲：`proxy_buffering off;`（Nginx） |
| 播放 416 | Range 由代理改写；确保 `Accept-Ranges` 透传 |

---

## 8. 安全清单

- [ ] 首次登录后改掉 `admin` 密码（侧边栏 ⚿，或 `python run.py --reset-password admin`），
      并删除 `data/INITIAL_ADMIN.txt`
- [ ] 公网部署必须套 HTTPS（反代），不要直接暴露 8318
- [ ] API 密钥按用途分发，泄露后立即在设置里删除
- [ ] `data/` 目录权限收紧（含 `secret.key`、数据库、回收站）
- [ ] 不要把 `MTW_ADMIN_PASSWORD` 写进镜像或 compose 的版本库
- [ ] 只在可信网络暴露 Subsonic 接口；它使用 Basic/token 认证而非 Cookie

---

## 9. 路线图（建议顺序）

1. M4A/MP4 安全写入（重写 moov + 修补 stco，需充分测试）
2. OIDC 单点登录（授权码 + PKCE）
3. 定时任务（每日固定时间扫描/刮削）
4. 声学指纹去重（可选：Chromaprint 外部调用）
5. 智能音箱：通过 Home Assistant / xiaomusic 的场景化集成（见 `docs/INTEGRATIONS.md`）
6. 大库优化：FTS5 全文搜索、封面懒加载、分页游标

改路线图前先看 `docs/FEATURE-MATRIX.md` 的"未实现"清单，避免重复劳动。
