# navidrome-bridge

给 open-music-tag 刮出来的艺术家头像「补最后一段路」的小容器：把文件写对之后，让 Navidrome 真的重算并显示出来。

## 它解决什么问题

open-music-tag 刮削完会把头像写成 `ArtistImageFolder` 里的 `<艺术家名>.jpg`，但 Navidrome：

- 不会自己重试缺失的头像；
- 改配置也不会更新已存的解析结果；
- 已经在退避队列里的艺术家，`artwork reprocess` 只会回一句 `left unchanged`。

所以「文件写对了但页面还是灰星」是常态。这个容器每轮做：

```
摘掉「有图却卡在重试队列」的行 → artwork reprocess --source absent --kind ar
```

## 用法

```bash
# 常驻（默认每 120 秒一轮）
docker compose up -d --build

# 看一下状态
docker compose exec navidrome-bridge python3 /app/bridge.py status

# 手动补一轮头像（网易云）
docker compose exec navidrome-bridge python3 /app/bridge.py backfill-images

# 手动补一轮简介（百度百科 → Last.fm 兜底）
docker compose exec navidrome-bridge python3 /app/bridge.py backfill-bio
```

## 部署时记得做的事

容器接管重算之后，宿主机那条 `*/2 * * * * /usr/local/bin/omt-nav-sync.sh` 就该停掉，否则两边都在 reprocess：

```bash
crontab -l > /root/crontab.bak-$(date +%Y%m%d)
crontab -l | sed 's|^\*/2 \* \* \* \* /usr/local/bin/omt-nav-sync.sh|# &|' | crontab -
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
`NAV_CONTAINER` | `navidrome` | Navidrome 容器名 |
`ARTIST_DIR` | `/music/artist` | `ArtistImageFolder` 在容器内的路径 |
`NAV_DB` | `/data/navidrome.db` | Navidrome 数据库路径 |
`NAV_DATAFOLDER` | `/data` | 传给 `navidrome artwork` 的 `--datafolder` |
`INTERVAL` | `120` | 循环间隔秒 |
`ENABLE_UNSTICK` | `1` | 摘卡死队列行 |
`ENABLE_REPROCESS` | `1` | 调 navidrome 重算 |
`ENABLE_IMAGE` | `0` | 每轮自动补头像（会访问网易云） |
`ENABLE_BIO` | `0` | 每轮自动补简介（会访问百科/Last.fm） |
`NETEASE_DELAY` | `1.0` | 补图请求间隔秒 |
`BIO_DELAY` | `0.6` | 补简介请求间隔秒 |
`LASTFM_APIKEY` | 内置 | 需要换 key 时覆盖 |

## 挂载要求

| 宿主路径 | 容器内 | 用途 |
| --- | --- | --- |
`/diskc/Music` | `/music` | 读写艺术家图片 |
`/DATA/AppData/navidrome/data` | `/data` | 读库、补简介时写库 |
`/var/run/docker.sock` | 同路径 | 走 Docker Engine API：`exec` 跑 `artwork reprocess`，`stop`/`start` 用于写库前停容器（镜像里不需要 docker CLI） |

## 注意

- **`docker.sock` 是高权限**：容器内等于能操作宿主机上所有容器。不放心的话可以把 `ENABLE_REPROCESS` / `ENABLE_BIO` 设为 `0`，只保留「写文件」的能力，重算继续交给宿主机的 cron。
- 补简介会**停一下 Navidrome**（约 5～10 秒）再写库，因为它在内存里缓存了 artist 行，边跑边改可能被覆盖。只写 `biography` 为空的行，不覆盖已有简介。
- 补图只新增文件，不覆盖同名已有图。
