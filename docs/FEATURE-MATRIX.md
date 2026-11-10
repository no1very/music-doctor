# 功能对照矩阵（上游文档 → 本实现）

图例：✅ 已实现并验证 · ⚠️ 部分实现 · ❌ 未实现 · 🚫 刻意不实现

| # | 上游文档功能 | 子项 | 状态 | 实现位置 | 验证 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 首页总览 | 统计卡片、最近入库、最近播放、待刮削 | ✅ | `web/js/views/library.js::dashboard` | ui_smoke | 数据来自 `/api/stats` |
| | | 任务进行中提示 | ✅ | 同上 + SSE | ui_smoke | 顶栏徽标显示进度 |
| 2 | 异步任务中心 | 后台执行、进度、日志 | ✅ | `app/tasks.py` | e2e 扫描任务 | 线程池 + SQLite 持久化 |
| | | 取消任务 | ✅ | `tasks.cancel` + `canceling` 状态 | 手动 | 协作式取消（每首/每文件检查） |
| | | 实时日志推送 | ✅ | SSE `task.log` | e2e SSE + 手工 | 断线重连 |
| | | 历史任务与结果 | ✅ | `GET /api/tasks` | ui_smoke | 结果 JSON 存库 |
| 3 | 自动刮削 | 多标签源 | ✅ | `app/providers/` | e2e 候选 | filename / MusicBrainz / 网易云 / JSON 插件 |
| | | 评分与阈值 | ✅ | `score_candidate` + `scrape.min_score` | 单测 + 手工 | 标题/艺术家/专辑/时长加权 |
| | | 扫描后自动刮削 | ✅ | `_scan_job`、`watcher.py` | 代码路径 | 设置项 `scrape.auto_on_scan` |
| | | 封面抓取 | ✅ | Cover Art Archive / 网易云 | 单测封面写回 | 也支持候选自带 URL |
| | | 歌词抓取 | ✅ | LRCLIB / 网易云 | 手工 | 可关闭 |
| 4 | 手动刮削 | 搜索候选 | ✅ | `GET /api/tracks/{id}/candidates` | e2e + ui_smoke | 多源合并排序 |
| | | 应用候选 | ✅ | `scrape.apply_candidate` | ui_smoke 渲染 | 可只写标签不写封面 |
| | | 按文件名解析 | ✅ | `FilenameProvider` | e2e | 「艺术家 - 标题」「01. 标题」 |
| 5 | 后台刮削入库 | 目录监听 | ✅ | `app/watcher.py` | 手动 | 间隔 `library.watch_interval` |
| | | 新增文件自动入库 | ✅ | watcher → scan | 手动 | 与手动扫描同一条路径 |
| | | 增量判断 | ✅ | `scanner.scan_library` | e2e | size+mtime；`force` 全量 |
| | | 缺失文件标记 | ✅ | `missing` 字段 | 手工 | 不删除记录，避免误删版本 |
| 6 | 音乐收藏与播放 | 收藏歌曲 | ✅ | `favorites` + API | 手工 | 收藏页 = 音乐库筛选 |
| | | 收藏专辑/艺术家 | ⚠️ | 通过曲目收藏间接体现 | — | 无独立收藏表 |
| | | Web 播放器 | ✅ | `web/js/player.js` | ui_smoke 渲染 | 队列/随机/循环/音量/MediaSession |
| | | 播放记录 | ✅ | `play_history` | e2e（Subsonic scrobble） | 10 秒或半曲上报 |
| | | 断点续播 | ✅ | `play_queues` | 手工 | 队列+进度持久化 |
| | | Subsonic 接入 | ✅ | `app/subsonic.py` | e2e | 见接口清单 |
| 7 | 跨设备播放同步 | 队列/曲目/进度 | ✅ | `play_queues` + `/api/queue` | 手工 | 登录恢复；Subsonic `savePlayQueue` |
| | | 多端实时同步 | ⚠️ | SSE 只广播房间，不广播个人队列 | — | 个人队列为拉取式（打开时恢复） |
| 8 | 多端同播 | 房间创建/加入 | ✅ | `rooms` + API | ui_smoke | 6 位房间码 |
| | | 成员状态 | ✅ | `room_members` | ui_smoke | 最近在线时间 |
| | | 播放状态协同 | ✅ | `PUT /api/rooms/{code}/state` + SSE | 手工 | 回环抑制 |
| | | 主持人权限 | ❌ | — | — | 任何成员都可控制 |
| 9 | 元数据批量编辑 | 多选批量改字段 | ✅ | `POST /api/tracks/batch` | e2e | 直接写文件 |
| | | 批量刮削/重读/删除 | ✅ | 同上 | e2e + ui_smoke | 删除进回收站 |
| 10 | 元数据版本管理 | 修改快照 | ✅ | `meta_versions` | e2e | 记录前后值 |
| | | 一键回退 | ✅ | `restore_version` | e2e | 回退也产生新版本 |
| | | 差异对比 UI | ⚠️ | 显示变更字段名 | ui_smoke | 无逐字段 diff 视图 |
| 11 | 智能家居（小爱） | 语音唤醒播放 | ⚠️ | 见 `docs/INTEGRATIONS.md` | — | 不内置米家协议；经 Home Assistant / xiaomusic 间接接入 |
| 12 | 云音乐（AList） | WebDAV 索引 + 代理串流 | ✅ | `app/webdav.py` + `scanner.py` | e2e WebDAV 假服务器 | 只读，不写回网盘；支持 Range |
| 13 | 刮削报告 | 按天/来源统计、积压、失败任务 | ✅ | `app/api.py` + `views/reports.js` | e2e + ui_smoke | 无 CSV 导出 |
| 14 | 外置歌词 API | LRCLIB / 网易云 | ✅ | `providers` | 手工 | 自定义源需 JSON 插件（当前仅搜索） |
| 15 | 智能歌单 | 规则组合 | ✅ | `build_smart_query` | e2e + 单测 | all/any、15 字段、16 操作符 |
| | | 动态更新 | ✅ | 每次读取求值 | e2e | 无需物化 |
| | | 排序/上限 | ✅ | rules.sort/order/limit | e2e | SQL 白名单 |
| 16 | 音乐去重与洗版 | 重复聚类 | ✅ | `duplicate_groups` | e2e | 标题+艺术家+时长容差 |
| | | 完全重复文件 | ✅ | `quick_hash` | 手工 | 首 256KB + 大小 |
| | | 保留最佳版本 | ✅ | `_score`（无损/码率/位数/体积） | 单测思路+手工 | 建议值可在 UI 调整 |
| | | 安全清理 | ✅ | 移入 `data/trash` | 代码路径 | 不直接永久删除 |
| 17 | 插件模块 | 内置来源 | ✅ | 5 个内置 provider | e2e providers | — |
| | | 用户自定义 | ⚠️ | JSON 声明式插件 | 手工 | 不执行任意 Python（安全取舍） |
| | | 插件管理 UI | ✅ | 设置 → 插件与接口 | ui_smoke | 安装 + 测试 |
| 18 | 角色与权限 | admin/editor/listener | ✅ | `security.py` + `req.require` | e2e 403 | 前端也隐藏无权限操作 |
| | | 用户管理 | ✅ | `/api/users` | e2e | 停用/删除/改角色 |
| | | 自助改密 / 管理员重置 / CLI 重置 | ✅ | `/api/auth/password`、`run.py --reset-password` | e2e + 手工 | 改密后注销全部会话 |
| | | 单点登录 SSO | ❌ | — | — | 见路线图 |
| 19 | 变量与重命名 | `${...}` 语法 | ✅ | `app/variables.py` | 单测 6 组 | AST 白名单 |
| | | 变量表 | ✅ | `context_for` | 单测 | 与文档一致 |
| | | 文件名预览 | ⚠️ | 设置页示例输入 | — | 预览依赖手动触发 |
| | | 自动重命名 | ⚠️ | 设置项存在，保存后未强制调用 | — | 编辑器可手动触发 |
| 20 | 部署形态 | Docker/Compose | ✅ | `Dockerfile`、`docker-compose.yml` | 构建验证 | 单容器，SQLite |
| | | MySQL/Redis 外置 | ❌ | — | — | 单机场景不必要 |
| | | 自定义端口 | ✅ | `MTW_PORT` / `--port` | 手工 | — |
| 21 | 客户端兼容 | Subsonic 子集 | ✅ | `app/subsonic.py` | e2e | token/enc 认证 |
| | | OpenSubsonic 扩展声明 | ✅ | `getOpenSubsonicExtensions` | e2e | 4 项 |
| 22 | 其它 | 封面缓存 | ✅ | `covers` 表 + 哈希 | e2e | 不可变缓存头 |
| | | 中文/多语言标签 | ✅ | ID3 UTF-16 / FLAC UTF-8 | 单测中文 | — |
| | | 只读模式 | ✅ | `MTW_READ_ONLY` | 代码路径 | 不写回文件 |
| | | 回收站 | ✅ | `data/trash` | 代码路径 | 覆盖删除/去重 |
| 23 | 商业授权 | 激活码/试用 | 🚫 | — | — | 明确不实现 |
| 24 | MCP / Agent | initialize、tools/list、tools/call、resources | ✅ | `app/mcp.py` | e2e 7 项 | 11 个工具，API 密钥认证 |
| 25 | API 密钥 | 个人密钥、撤销、最近使用 | ✅ | `app/apikeys.py` | e2e + 单测 | 仅存 SHA-256 |
| 26 | 外置歌词/封面 | `.lrc` 写入与读取、`cover.jpg` | ✅ | `library.py` | e2e + 单测 | 远程库跳过 |
| 27 | 批量重命名 | 模板预览、冲突检测、批量执行 | ✅ | `api.py` + `app.js` | e2e preview | 只改文件名 |
| 28 | 备份 | 数据库 + 设置 zip | ✅ | `GET /api/backup` | 手工 | 恢复需停服替换文件 |
| 29 | 远程库 | 只读、代理串流、目录监听 | ✅ | `webdav.py` | e2e 扫描/串流/Range | 无写回、无重命名 |
| 30 | 艺术家刮削 | 头像、别名、简介；单个 + 批量；导出给 Navidrome | ✅ | `app/artist_scrape.py` | e2e + 单测 | 网易云/维基/Deezer；Subsonic artistInfo2 同步 |

## 与上游的已知差异（设计取舍）

1. **技术栈不同**：上游未公开技术栈；本实现是 Python 标准库 + 原生前端，单文件部署、无依赖。
2. **数据库**：SQLite 而非 MySQL/Redis；面向家庭/单机，牺牲水平扩展换零运维。
3. **插件安全模型**：只接受声明式 JSON 映射，不加载用户 Python，避免 RCE 面。
4. **M4A 只读**：宁可拒绝写入，也不冒险破坏 MP4 索引。
5. **刮削源**：未接入上游可能存在的私有聚合源，仅使用公开 API 与可自行扩展的 JSON 插件。
6. **UI 原创**：未复制上游的版面与素材，仅保持信息架构相近。
