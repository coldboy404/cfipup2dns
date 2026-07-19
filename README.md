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

## 健康检查

镜像内置健康检查接口：

```bash
curl http://127.0.0.1:9527/api/health
```

正常响应：

```json
{"ok": true, "service": "cfipup2dns"}
```

## 与上游项目的关系

本项目基于 `Leo-Mu/montecarlo-ip-searcher`，主要增加：

- WebUI 配置管理
- Cloudflare DNS 自动写入和差异化同步
- 多域名、Zone ID 自动识别
- 定时任务、结果持久化和日志管理
- 容器镜像与自动发布流程

镜像构建阶段会从上游 `main` 分支编译 `mcis`，运行容器不再依赖 Go 工具链，也不需要在启动阶段访问 GitHub 下载源码。

## 安全提示

- WebUI 能修改 DNS 和读取运行状态，默认没有账号认证，请不要直接暴露到公网。
- 推荐仅在内网访问，或放在带认证与 HTTPS 的反向代理后面。
- Cloudflare Token 应遵循最小权限原则。
- 若需要公网部署，可在反向代理层添加访问控制、Basic Auth 或单点登录。

## 作者与致谢

- 作者：**coldboy404**、**gemini**、**chatgpt**
- 特别鸣谢：https://github.com/Leo-Mu/montecarlo-ip-searcher
