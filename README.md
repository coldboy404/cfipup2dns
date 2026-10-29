# cfipup2dns

一个面向国内服务器环境的 **Cloudflare 优选 IP 自动 DDNS** 项目，提供 WebUI、定时测速、多域名 DNS 同步和 Docker 一体化部署。

[![Container](https://img.shields.io/badge/GHCR-cfipup2dns-blue?logo=docker)](https://github.com/coldboy404/cfipup2dns/pkgs/container/cfipup2dns)

## 主要功能

- WebUI 图形管理，默认端口 `9527`
- IPv4 / IPv6 手动或定时优选
- 多域名同步、Zone ID 自动识别
- 延迟、丢包率、下载速度、Cloudflare 地区码筛选
- DNS 差异化同步，测速失败时默认保留旧解析
- 运行日志、优选结果持久化和日志自动裁剪
- 优选结果按 IPv4 / IPv6 筛选，支持 CSV / TXT 导出
- 可停止正在运行的任务，手动、定时和 CLI 任务防重叠
- 可选 WebUI 账号认证、跨站请求防护、参数校验
- GHCR 多架构镜像：`linux/amd64`、`linux/arm64`
- 镜像内已预编译 `montecarlo-ip-searcher`，容器启动时无需安装 Go、下载源码或现场编译

## 项目地址

- 本项目：https://github.com/coldboy404/cfipup2dns
- 上游项目：https://github.com/Leo-Mu/montecarlo-ip-searcher

## 界面预览

![截图1](assets/screenshot-1.jpg)
![截图2](assets/screenshot-2.jpg)

## 部署方式

### 方式一：Docker Compose（推荐）

无需克隆完整仓库，只需要创建一个目录和 Compose 文件：

```bash
mkdir -p cfipup2dns && cd cfipup2dns
cat > compose.yml <<'YAML'
services:
  cfipup2dns:
    image: ghcr.io/coldboy404/cfipup2dns:latest
    container_name: cfipup2dns
    restart: unless-stopped
    ports:
      - "9527:9527"
    environment:
      TZ: Asia/Shanghai
      # 如需认证，取消注释并设置强密码；公网访问还必须配置 HTTPS
      # WEB_USERNAME: admin
      # WEB_PASSWORD: "请替换为强密码"
    volumes:
      - ./data:/data
    security_opt:
      - no-new-privileges:true
YAML

docker compose up -d
```

启动后访问：`http://服务器IP:9527`

更新镜像：

```bash
docker compose pull
docker compose up -d
```

查看日志与停止服务：

```bash
docker compose logs -f
docker compose down
```

> 若 GHCR 在你的网络环境中不可达，请先为 Docker 守护进程配置代理或镜像加速；运行优选任务时通常建议关闭会影响真实线路测速的代理。

### 方式二：使用仓库内置 Compose

```bash
git clone https://github.com/coldboy404/cfipup2dns.git
cd cfipup2dns
bash docker/up.sh
```

`docker/up.sh` 默认拉取 GHCR 最新镜像。需要从当前源码本地构建时：

```bash
BUILD_LOCAL=1 bash docker/up.sh
```

### 方式三：Docker 命令

```bash
docker run -d \
  --name cfipup2dns \
  --restart unless-stopped \
  -p 9527:9527 \
  -e TZ=Asia/Shanghai \
  -v "$PWD/data:/data" \
  --security-opt no-new-privileges:true \
  ghcr.io/coldboy404/cfipup2dns:latest
```

## 镜像标签与架构

镜像地址：

```text
ghcr.io/coldboy404/cfipup2dns
```

- `latest`：`main` 分支最新稳定构建
- `sha-xxxxxxx`：指定提交构建，适合固定版本
- `vX.Y.Z`、`X.Y.Z`、`X.Y`：推送语义化版本标签后自动生成
- 支持架构：`linux/amd64`、`linux/arm64`

GitHub Actions 会校验 Python、Shell 和 Compose 配置，并生成镜像 SBOM、构建来源证明（provenance / attestation）。

## 首次配置

打开 WebUI 后填写：

1. Cloudflare API Token
2. 一个或多个待更新域名
3. TTL、小黄云和测速参数
4. 定时任务周期与 IP 模式

Token 建议至少具有：

- **Zone / Zone / Read**：自动查询 Zone ID
- **Zone / DNS / Edit**：读写 DNS 记录

多域名配置一行一个：

```text
cf1.example.com
cf2.example.com
cf3.example.net
```

如需手动指定 Zone ID：

```text
cf1.example.com
cf2.example.com|2d4f6f8axxxxxxxxxxxxxxxxxxxx
```

WebUI 不会在页面源码或 `/api/config` 响应中回传已保存的 Token；输入框留空并保存时会保留原 Token。

## 常用环境变量

Compose 文件只需保留基础配置。高级参数可在 WebUI 中设置，也可通过环境变量提供默认值：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `TZ` | `Asia/Shanghai` | 容器时区 |
| `PORT` | `9527` | 容器内 WebUI 端口；通常只修改宿主机端口映射 |
| `TIMEOUT` | `6s` | 单次探测超时 |
| `ROUNDS` | `6` | 测速轮数 |
| `DOWNLOAD_TOP` | `8` | 下载测速候选数量 |
| `DOWNLOAD_TIMEOUT` | `90s` | 下载测速超时 |
| `DOWNLOAD_MODE` | `sequential` | 下载测速模式 |
| `MIN_DOWNLOAD_MBPS` | `0` | 最低下载速度，`0` 表示不限制 |
| `MAX_LATENCY_MS` | `0` | 最大延迟，`0` 表示不限制 |
| `MIN_LATENCY_MS` | `0` | 最小延迟，`0` 表示不限制 |
| `MAX_LOSS_RATE` | `1` | 最大丢包率，`1` 表示 100% |
| `CF_COLO` | 空 | 地区码过滤，例如 `HKG,NRT,SJC` |
| `KEEP_LAST_ON_FAIL` | `true` | 新结果不可用时保留旧 DNS |
| `SWITCH_MIN_IMPROVEMENT` | `0.20` | 候选总耗时至少改善 20% 才切换 |
| `SWITCH_MIN_INTERVAL` | `21600` | 两次正常切换的最小间隔（秒） |
| `SWITCH_FAILURE_THRESHOLD` | `2` | 现有 IP 连续整轮失败达到此次数才故障切换 |
| `SWITCH_RECHECK_ROUNDS` | `3` | 现有与候选 IP 的同轮 HTTPS 复测次数 |
| `SWITCH_RECHECK_TIMEOUT` | `5` | 单次直连复测超时（秒） |
| `LOG_MAX_BYTES` | `2097152` | 日志触发裁剪的大小 |
| `LOG_KEEP_BYTES` | `524288` | 裁剪后保留的最近日志大小 |

## 数据目录

宿主机的 `./data` 映射到容器 `/data`，其中包括：

- `/data/project/config.json`：Cloudflare 与高级配置
- `/data/project/best_ips_v4.json`：IPv4 优选结果
- `/data/project/best_ips_v6.json`：IPv6 优选结果
- `/data/project/montecarlo-ip-searcher`：镜像内置工具的持久化副本
- `/data/cron/cfip.cron`：定时任务配置
- `/data/logs/`：运行日志

升级容器不会删除这些数据。请自行保护 `data/project/config.json`，其中包含 Cloudflare Token。

## IP 地址库升级

内置 CIDR 文件记录 SHA256 与条目数到 `/data/project/.cidr-manifest.json`。受管理且未被修改的库随镜像升级自动刷新；手动修改的文件，以及首次升级时无法判断来源的旧文件，默认保留，初始化日志会说明状态。

如需将某一类地址库恢复为当前镜像内置版本，请先自行留存自定义内容，删除对应的 `data/project/ipv4cidr.txt` 或 `ipv6cidr.txt` 后重启容器。不会在启动时下载第三方在线库，也不会默默覆盖自定义内容。

## IPv6 出口与数量设置

### 容器 IPv6

宿主机能访问 IPv6，不代表默认 Docker bridge 也能。程序会在 IPv6 扫描前检查本地路由：明确无 IPv6 路由时跳过扫描、报告 `network_unavailable`（原因 `ipv6_no_local_route_kept_old`）并保留现有 DNS；此检查只确认本机路由，不保证公网可达或测速成功。

Linux 主机已具备可用 IPv6 时，可选择 **host 网络**，无需修改 Docker 全局配置。使用仓库内置 Compose、Docker Compose **2.24.4 或更新版**：

```bash
# 将 IP 替换为宿主机实际的内网地址；只生成配置检查，不启动容器
WEB_BIND_HOST=192.168.66.33 docker compose -f docker-compose.yml -f docker-compose.host.yml config --quiet

# 确认后再更新部署；与默认 bridge 方式共用 ./data
WEB_BIND_HOST=192.168.66.33 NETWORK_MODE=host bash docker/up.sh
```

host 网络不使用端口映射，`PORT` 是程序的真实监听端口，必须避免与宿主其他服务冲突。为防止意外公网暴露，host 覆盖文件默认只监听 `127.0.0.1`；需要局域网访问时显式指定 `WEB_BIND_HOST`，并开启 Web 认证。绑定 IPv4 内网地址不影响程序主动连接 IPv6。后续更新仍需使用相同的环境变量/覆盖文件；回到 bridge 用 `NETWORK_MODE=bridge bash docker/up.sh`，但未配置 IPv6 的 bridge 仍无法进行 IPv6 优选。

镜像更新**不会自动修复已存在的 Docker 网络**，也不会自动修改宿主机、路由器或 DNS。host 模式是可选部署方案，不是应用在容器内自动打开 IPv6。

### 三种数量的含义

- **立即执行 / 优选数量**：每个地址族的目标上限，可单独“保存运行设置”；运行时也保存，刷新和重启后保留。与定时任务独立，保存 Cloudflare 配置不会覆盖它。
- **定时任务 / 优选数量**：只控制定时任务，需在定时任务区域保存。
- **下载测速数量**：测速候选目标，不是 DNS 记录数。启用下载测速时，程序自动将有效候选目标提高到至少 `优选数量 × 3`，并同步扩大延迟候选池，为过滤和复验留余量；不足仍可能少于目标，不会凑数写入不合格 IP。`0` 显式关闭下载测速。

`sequential` 和 `all` 都逐 IP 串行下载，避免争抢带宽：前者在候选池内测到指定成功数量后停止，后者只尝试固定数量，不保证成功数量。下载失败或超时过程中读到的部分字节不再视为有效下载成绩；旧格式缺少 `download_ok` 时保留兼容处理。

## 本次修复

- 保存手动运行参数，区分手动、定时及下载测速数量。
- 已有 DNS 从 5 条扩到 10 条时，可补入同轮 HTTPS 全部通过的候选；扩容不受性能替换冷却限制。缩容仅移除本轮确认健康的多余旧记录；不确定或失败的旧记录保留保护，可能暂时超出目标数量。普通替换仍保留防抖和故障保护。
- 扩大测速候选余量，过滤后保留未测速候选供无最低下载门槛时补位；启用最低下载门槛时仍必须有有效下载成绩。
- IPv6 无本地出口时提前报告，不再空跑大量探测后仅提示“无合格 IP”。
- 最终新旧 IP 性能替换仍按同轮 HTTPS 总耗时，不声称按下载吞吐自动切换；本次未更换 mcis 核心。

## 健康检查

镜像内置健康检查接口：

```bash
curl http://127.0.0.1:9527/api/health
```

正常响应：

```json
{"ok": true, "service": "cfipup2dns"}
```

## 仅测速、校验与运行摘要

- WebUI“立即执行”可勾选 **仅测速**，只影响本次手动运行；默认不勾选，定时任务仍按原配置同步 DNS。CLI 可设置 `SCAN_ONLY=true`。无需 Cloudflare Token 或域名即可扫描。
- 仅测速不调用 DNS 更新接口，不覆盖 `best_ips_v*.json`；候选单独写入 `scan_only_v4.json` / `scan_only_v6.json`，在页面折叠区和认证后的 `/api/scan-results` 查看。现有导出仍导出已同步结果，不导出仅测速候选。
- 排序指标必须是有限数字；NaN、Infinity、非数字、负数不参与有效评分。启用延迟、丢包、速度或地区过滤后，缺少所需指标的候选会被拒绝，而不是绕过限制。丢包上限 `1` 为不限制，`0` 为不允许丢包；其他数值阈值 `0` 为不限制。上游未输出所需指标时可能得到空结果。
- mcis JSONL 的机房码位于 `trace.colo`，程序会提取为 `colo`。`connect_ms`、`tls_ms`、`ttfb_ms`、`total_ms` 会分别保存和导出；兼容字段 `latency_ms` 定义为 `total_ms` 优先，缺失时依次回退 `ttfb_ms`、`connect_ms`。mcis 的 `prefix_*` 是网段采样统计，不是每 IP 丢包率；启用丢包过滤时会明确提示并用重复 HTTPS 直连失败率复测候选。
- DNS 切换策略默认只比较**同一轮**对实际 DNS 现有 IP 与候选 IP 的 HTTPS 直连 `total_ms`：证书、SNI 和 Host 均验证。默认最小改善率 `20%`、最小切换间隔 `21600` 秒、现有 IP 连续 `2` 轮全部复测失败才故障切换，每轮复测 `3` 次、单次 `5` 秒。状态原子写入且最多保留 200 个域名/地址族条目；只有 Cloudflare API 回读验证成功后才推进状态。每个域名独立读取和决策，因此允许解析不同；复测不确定时保持原记录，首次无记录可安全初始化。仅测速不会读取、写入或推进切换状态。
- 每个域名写入后，通过 Cloudflare API 再读取记录集合，核对 IP、TTL 和代理状态；验证失败标记失败，继续其他域名和地址族，并报告部分失败。**这是 API 状态验证，不是公网递归 DNS 传播验证**，也不是跨域名事务。已有修改不自动回滚；部分失败时不覆盖该地址族的已同步结果文件，因此页面旧结果可能不代表所有域名当前状态。
- 正常完成的摘要保存到 `run_history.json`（最近 30 次、原子写入、0600），重启后可在页面、`/api/history` 和 `/api/status` 的 `last_summary` 查看。摘要不保存 Token、API 响应正文或原始测速日志。
- 退出码 `0` 仅表示全量 API 验证通过或仅测速完成；`1` 表示失败/部分失败；`2` 表示至少一个地址族无新结果并保留现有 DNS。各地址族/域名的详细状态以摘要为准，空结果不再宣称同步成功。
- 强制停止/断电时本次可能没有完整摘要，页面显示的“最近完整摘要”仍可能属于上一次；取消状态以当前任务状态和日志为准。扫描超时仍可使用已产生的合格候选，但 DNS 切换前仍执行同轮直连复测。
- 当前切换策略选择延迟指标，不比较历史下载速度；下载测速结果仍用于候选排序/过滤。没有自动回滚，也不保证公网递归 DNS 已传播。

## 结果导出与任务控制

WebUI 结果区可选择全部 / IPv4 / IPv6 并下载 CSV 或 TXT，TXT 每行一个 IP；CSV 包含延迟与下载速度（Mbps，不是 MB/s）。

“停止任务”会终止当前测速进程组，阻止任务继续执行；已经提交给 Cloudflare 的 DNS 修改不会自动回滚。测速失败、取消或空结果不会主动删除现有解析。

DNS 差异同步按 IP 匹配原记录，不因测速排名变化而重复写入；仅替换失效候选，并在新记录写入成功后删除多余记录。该过程不是 Cloudflare 端事务，多域名中途失败时可能已有部分域名更新。

本轮功能设计参考 [CFData-WEB](https://github.com/PoemMisty/CFData-WEB) 的结果导出和任务交互思路，未复制其 GPL 源码，也未替换本项目的 `mcis` 测速核心。非标 IP/端口扫描、数据中心分组二次测速等仍属于后续可扩展方向。

## 与上游项目的关系

本项目基于 `Leo-Mu/montecarlo-ip-searcher`，主要增加：

- WebUI 配置管理
- Cloudflare DNS 自动写入和差异化同步
- 多域名、Zone ID 自动识别
- 定时任务、结果持久化和日志管理
- 容器镜像与自动发布流程

镜像构建阶段从固定上游提交 `add8ab94463ec440d17d354c858be7346a52b95e` 编译 `mcis`，并校验源码归档 SHA256。运行容器不依赖 Go 工具链或启动时下载源码；持久化二进制按镜像内版本标记自动升级。自定义上游版本时，必须同时传入构建参数 `MCIS_REF` 与对应 `MCIS_SHA256`。

## 安全提示

- WebUI 能修改 DNS 和读取运行状态。默认不启用认证以兼容现有内网部署，请不要直接暴露到公网。
- 配置 `WEB_USERNAME` 与 `WEB_PASSWORD` 可开启 HTTP Basic Auth，两项必须同时设置；使用 HTTPS 反向代理避免凭据明文传输。仓库 Compose 支持从 `.env` 读取两项，请保护该文件权限。
- 跨域的修改请求会被拒绝；反向代理应保留原始 Host。
- 配置文件与新写入的结果文件使用 `0600` 权限；配置和结果采用原子替换，避免中途断电产生半截 JSON。
- 推荐仅在内网访问，或放在带认证与 HTTPS 的反向代理后面。
- Cloudflare Token 应遵循最小权限原则。
- 若需要公网部署，可在反向代理层添加访问控制、Basic Auth 或单点登录。

## 作者与致谢

- 作者：**coldboy404**、**gemini**、**chatgpt**
- 特别鸣谢：https://github.com/Leo-Mu/montecarlo-ip-searcher
