# 服务器部署记录（your-server-ip）

本文件记录**这台具体服务器**的部署布局，便于以后升级、排障和迁移。不含任何密码。

---

## 1. 主机与依赖

| 项 | 值 |
| --- | --- |
| 主机 | `your-host` · `your-server-ip` |
| 系统 | Armbian 26.2.1 noble / Ubuntu 24.04 · aarch64 |
| Docker | 29.4.0，Compose v5.1.3，Docker Root `/diskc/docker` |
| 镜像加速 | `/etc/docker/daemon.json` → `https://docker.1ms.run` |
| 磁盘 | `/` 6G（剩 1G）；`/diskc` 916G（剩 12G）；音乐在 `/diskc/Music`（24G，扫描到 **725 个音频文件**，全部文件 1722 个含 `.lrc`/封面） |
| 防火墙 | firewalld/ufw 均未启用（仅局域网可访问） |

> 注意：根分区只剩约 1G，**所有数据与镜像都放在 `/diskc`**，不要往 `/` 写大文件。
>
> 构建注意：本机的 buildx 默认 builder 是 `multi`（docker-container），它会绕过
> `/etc/docker/daemon.json` 的镜像加速并卡住。**构建前先 `docker buildx use default`**，
> 或在命令里显式 `--builder default`；基础镜像 `python:3.12-slim` 已拉到本机。

## 2. 目录布局

```
/diskc/open-music-tag/
├── docker-compose.yml      部署定义（构建上下文 ./src）
├── src/                    本仓库源码（app/、web/、run.py、Dockerfile…）
└── data/                   持久化数据：library.db、secret.key、日志、回收站、插件
    ├── library.db
    ├── secret.key          ← 务必备份，丢失会导致 Subsonic token 失效
    ├── INITIAL_ADMIN.txt   ← 首次启动的管理员密码，登录后删除
    └── logs/access.log

/diskc/Music/               音乐库（只读挂载到容器 /music）
```

容器：

| 项 | 值 |
| --- | --- |
| 名称 | `open-music-tag` |
| 镜像 | `open-music-tag:1.0.0`（基于 `python:3.12-slim`，arm64） |
| 端口 | `8318:8318` |
| 数据卷 | `./data:/data`（当前约 180MB，其中封面占 177MB） |
| 音乐卷 | `/diskc/Music:/music`（**rw**，标签编辑需要写回；只想播放可改回 `:ro`） |
| 重启策略 | `unless-stopped` |

索引结果：**725 首 / 645 张专辑 / 434 位艺术家**，0 个失败。
其中 4 个文件的扩展名与实际容器不符（`.flac` 实为 MP3），已按魔数识别为 MP3，
在界面上标记为只读，避免用错误的写入器破坏文件。

旧的 `music-tag-web`（`xhongc/music_tag_web:latest`，8001 端口）**保持原样未动**，
两套可以并行跑，便于对照与切换。

## 3. 常用命令

```bash
cd /diskc/open-music-tag

docker compose ps                     # 状态
docker compose logs -f --tail=100     # 日志
docker compose restart                # 重启
docker compose up -d --build          # 改代码后重建并启动
docker compose down                   # 停止（不删除 data/）
docker exec -it open-music-tag sh     # 进容器
docker exec -it open-music-tag python run.py --reset-password admin   # 重置密码
curl -s http://127.0.0.1:8318/api/health
```

升级步骤：

```bash
# 1) 备份
curl -fsS -H "Authorization: Bearer <API密钥>" \
  http://127.0.0.1:8318/api/backup -o /diskc/open-music-tag/backup.zip

# 2) 覆盖 src/（上传新代码），然后
cd /diskc/open-music-tag && docker compose up -d --build
```

数据库迁移在启动时自动执行（只加列 + 重建视图），`data/` 不需要动。
**降级不支持**，升级前务必备份 `data/library.db` 与 `data/secret.key`。

## 4. 首次登录与媒体库

1. 浏览器打开 <http://your-server-ip:8318>
2. 用户名 `admin`，密码在 `/diskc/open-music-tag/data/INITIAL_ADMIN.txt`
3. 登录后立即改密码：点侧边栏用户区的 **⚿**，输入当前密码和新密码；
   忘记密码可在服务器上重置（会注销该用户所有会话）：
   ```bash
   docker exec -it open-music-tag python run.py --reset-password admin
   ```
   改完删除初始密码文件：
   ```bash
   rm -f /diskc/open-music-tag/data/INITIAL_ADMIN.txt
   ```
4. 媒体库已添加为 **Music**（路径 `/music`，只读）。扫描在「任务中心」；
   也可用 API 触发：
   ```bash
   curl -X POST -H "Authorization: Bearer <token>" \
     http://your-server-ip:8318/api/libraries/1/scan
   ```
5. Subsonic 客户端填 `http://your-server-ip:8318`，账号同上。
6. MCP 地址：`http://your-server-ip:8318/mcp`（先用 API 密钥认证）。
7. 艺术家资料：进入「艺术家」→ 点开某位 → 「刮削艺术家资料」，或在列表右上
   「刮削全部艺术家」（后台任务，默认只补没有头像的，单次 300 位）。
   数据源默认 网易云 / 维基百科 / Deezer，可在 设置 → 刮削与标签 调整。
   该功能需要容器能访问 `music.163.com`（这台机器已验证可达）。

## 5. 备份与恢复

- **备份**：设置 → 关于 → 下载数据库备份，或上面的 `/api/backup`。
  生成的是 `library.db` + `settings.json` 的 zip，运行中也可安全备份。
- **恢复**：
  ```bash
  cd /diskc/open-music-tag
  docker compose down
  cp data/library.db data/library.db.bak 2>/dev/null || true
  rm -f data/library.db-wal data/library.db-shm
  # 解压备份里的 library.db 覆盖 data/library.db
  docker compose up -d
  ```
- **整机迁移**：复制 `/diskc/open-music-tag/{src,data,docker-compose.yml}`，
  在新机器上 `docker compose up -d --build`，再把音乐目录挂到同一路径即可。

## 6. 这台机器的注意事项

- 音乐目录是**扁平结构**（`标题 - 艺术家.flac` + 同名 `.lrc`）。文件内嵌标签已完整，
  扫描会优先使用内嵌标签；`.lrc` 会作为外置歌词回填到数据库。
- 如果以后加入未打标签的新文件，默认按「艺术家 - 标题」解析文件名；
  这批文件是「标题 - 艺术家」，建议先在设置里确认标签或手动刮削修正。
- 12G 剩余空间：镜像约 300MB，数据库随歌词增长（内嵌歌词较大），
  1722 首预计几十 MB，空间足够；但不要在 `/diskc` 上再堆大镜像。
- 端口 `8318` 未占用；若以后加反向代理，注意 `/api/events` 需要关闭缓冲。
- **不要删除** `data/secret.key`；更换它会让所有登录失效、Subsonic token 认证失败。
- 本机没有启用防火墙，建议只在局域网暴露 8318；如需公网访问请套 HTTPS 反代，
  并参考 `docs/INTEGRATIONS.md` 的 Nginx 示例。

## 6.1 页面纯黑（已修复，留档）

部署当晚出现过一次：浏览器打开只有黑底、没有任何文字。
原因：`web/js/app.js` 启动时调用了 `showLogin()`，但上一轮清理 import 时把它删掉了。
未登录时 `/api/auth/me` 返回 401 → 进入 catch → `showLogin` 未定义 → 启动中断；
而 `#app` 与 `#login` 都带 `hidden`，于是只剩背景色。

修复内容：

1. 补回 `showLogin` 的导入；
2. 新增 `#boot` 启动遮罩：启动完成前显示“正在加载”，任何启动异常会显示“初始化失败”和错误信息；
3. `app.js` 的启动流程加上外层 try/catch，并监听 `error` / `unhandledrejection`；
4. 静态 `html/js/css` 改为 `Cache-Control: no-store`，避免部署后浏览器继续用旧脚本；
5. `tools/ui_smoke.mjs` 增加 **SPA boot 测试**：真正 import `app.js`、触发
   `DOMContentLoaded`、断言登录界面出现，这类“黑屏”问题以后会被测试拦住。

若再次遇到：

```bash
# 1. 强制刷新（Ctrl+F5 / Cmd+Shift+R）；或先开一个无痕窗口
# 2. 浏览器开发者工具 Console 看第一条报错
# 3. 确认静态资源可访问
curl -sI http://127.0.0.1:8318/js/app.js | head -5
# 4. 看后端
docker compose logs --tail=50
```

## 8. Navidrome 艺术家头像/简介同步

Navidrome 有自己的数据库和代理，**不读本项目的数据库**；它 0.64.2 支持 `ArtistImageFolder`
本地头像目录，可以把本项目的刮削结果桥接过去。

已做的配置：

- 本项目设置 `artist.image_folder = /music/artist`，刮削时自动写出 `<艺术家名>.jpg`；
  艺术家列表还有「导出图片到 Navidrome」按钮（本次已导出 2 位已刮削艺术家）。
- Navidrome 用 CasaOS 管理，compose 文件：
  `/var/lib/casaos/apps/gifted_christiaan/docker-compose.yml`
  （改动前已备份为 `docker-compose.yml.bak-before-omt`）。新增/修改：
  ```yaml
  ND_ARTISTIMAGEFOLDER: /music/artist
  ND_ARTISTARTPRIORITY: image-folder, artist.*, album/artist.*, external
  ND_LASTFM_ENABLED: "true"
  ```
- 头像：`image-folder` 优先级最高，按**艺术家名（忽略大小写）**匹配 `/music/artist` 下的图片。
  新导出/更新图片后，Navidrome 的缓存键包含文件 mtime，会重新取图；必要时 Ctrl+F5。
- 简介：Navidrome 只从自己的代理取简介。已启用 Last.fm（容器内实测 API 200，
  刘德华/Beyond 均有中文简介）；它不会读本项目刮到的简介。
- 重新应用配置：
  ```bash
  cd /var/lib/casaos/apps/gifted_christiaan
  docker compose up -d navidrome
  # 关键：配置指纹变了，必须重新解析一次艺术家封面，否则沿用旧的“无图”缓存
  docker exec navidrome /app/navidrome artwork reprocess --kind ar -y --datafolder /data
  ```
- 验证（这三条命令已集成到项目，`python tools/navidrome.py status|reprocess|explain ar-<id>`
  会自动拼好下面这些参数；Navidrome 在 NAS 上时加 `--ssh nas`）：
  ```bash
  docker exec navidrome /app/navidrome artwork explain ar-<艺术家ID> --datafolder /data
  # Stored -> Source: folder / Source path: /music/artist/刘德华.jpg
  docker exec navidrome /app/navidrome artwork status --datafolder /data
  # Sources: artist folder 254（本次）；其余 250 位没有同名图片，队列仍在后台尝试外部代理
  ```
- 自动重算：导出图片/刮削艺术家后，本项目会往任务中心排一个「Navidrome 图像缓存重算」
  （`app/navidrome.py:after_export`）。本项目跑在容器里且没有 docker CLI 时不会硬失败，
  只会在任务日志/接口返回里说明原因，此时用 `python tools/navidrome.py reprocess --ssh <NAS>` 补一次。
- 本机（NAS）落地方案：容器内没有 docker CLI，所以自动重算交给宿主机 cron 兜底——
  ```cron
  */5 * * * * cd /diskc/open-music-tag/src && MTW_DATA_DIR=/diskc/open-music-tag/data MTW_NAVIDROME_LOCAL_IMAGE_FOLDER=/diskc/Music/artist /usr/bin/python3 tools/navidrome.py doctor --skip-status >> /tmp/navidrome-artwork.log 2>&1
  ```
  它只在 `/diskc/Music/artist` 的「文件数 + 最新 mtime」变化时才真正 `reprocess`，
  状态记在 `data/navidrome-artwork.json`。
- 回滚：用 `.bak-before-omt` 覆盖 compose 后再 `docker compose up -d navidrome`。
- 本次核对：Navidrome 有 505 位艺术家，`/music/artist` 有 265 张图片，
  按名称（忽略大小写）能匹配 **255 位**；刘德华、凤凰传奇、BEYOND 均在匹配列表中。
  未匹配的艺术家需要先在本项目刮削并导出（文件名必须等于 Navidrome 的艺术家名）。
- 简介：已启用 Last.fm（`ND_LASTFM_ENABLED=true`，`ND_LASTFM_LANGUAGE=zh`），
  并把这些艺术家的 `external_info_updated_at` 清空，Navidrome 会在打开艺术家页时重新拉取简介。

## 9. 回滚/卸载

```bash
cd /diskc/open-music-tag
docker compose down
docker rmi open-music-tag:1.0.0
# 确认不再需要后：
# rm -rf /diskc/open-music-tag
```

卸载不会影响 `/diskc/Music` 与旧的 `music-tag-web` 容器。
