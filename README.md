# cfipup2dns

一个面向国内服务器环境的 **Cloudflare 优选 IP 自动 DDNS** 项目，现已支持：

- ✅ Docker 一体化部署
- ✅ WebUI 图形管理（默认端口 `9527`）
- ✅ 定时任务管理（cron）
- ✅ 手动一键执行优选
- ✅ 日志在线查看
- ✅ 国内加速源默认启用（APT 镜像 + GitHub 代理）

---

## 项目主页

- GitHub: https://github.com/coldboy404/cfipup2dns

---

## 快速开始（Docker Compose）

> 适合国内机器，默认已经配置了加速源。

```bash
git clone https://gh-proxy.org/https://github.com/coldboy404/cfipup2dns.git
cd cfipup2dns
docker compose up -d --build
```

启动后访问：

- `http://你的服务器IP:9527`

---

## 国内安装 Docker（加速源）

> 适用于 Debian / Ubuntu。安装完成后可直接执行本项目的 `docker compose`。

### 1) 安装 Docker CE（使用国内 APT 源）

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://mirrors.ustc.edu.cn/docker-ce/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.ustc.edu.cn/docker-ce/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

### 2) 可选：配置 Docker Hub 镜像加速

```bash
sudo mkdir -p /etc/docker
cat <<'EOF' | sudo tee /etc/docker/daemon.json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://docker.1panel.live"
  ]
}
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
```

### 3) 验证

```bash
docker --version
docker compose version
```

---

## 目录结构

```text
cfipup2dns/
├─ cfip.sh
├─ config.example.json
├─ Dockerfile
├─ docker-compose.yml
├─ docker/
│  ├─ entrypoint.sh
│  └─ init-mcis.sh
└─ web/
   ├─ app.py
   ├─ templates/index.html
   └─ static/
```

---

## WebUI 功能

### 1) Cloudflare 配置
可直接编辑并保存 `config.json`，主要字段：

- `cloudflare.token`
- `cloudflare.zone_id`
- `cloudflare.domain`
- `cloudflare.ttl`
- `cloudflare.proxied`

### 2) 立即运行
支持手动触发：

- `IP_MODE=4`（仅 IPv4）
- `IP_MODE=6`（仅 IPv6）
- `IP_MODE=both`（双栈）

并可设置 `TOP_N`。

### 3) 定时任务
可在页面直接改 crontab，并即时生效。

### 4) 日志查看
在线查看最近运行日志，便于排查。

---

## 默认端口

- 容器内：`9527`
- 映射端口：`9527:9527`

如果要改端口，修改 `docker-compose.yml`：

```yaml
ports:
  - "9527:9527"
environment:
  PORT: 9527
```

---

## 国内加速说明

默认使用以下加速策略：

- Debian APT 镜像：`mirrors.ustc.edu.cn`
- GitHub 下载代理：`https://gh-proxy.com/`

可通过环境变量自定义：

```yaml
environment:
  GH_PROXY: https://gh-proxy.com/
  MCIS_TAG: v0.2.3
```

---

## 数据持久化

`docker-compose.yml` 默认挂载：

- `./data:/data`

其中包括：

- 配置文件：`/data/project/config.json`
- cron 配置：`/data/cron/cfip.cron`
- 日志目录：`/data/logs/`

---

## 常用命令

```bash
# 查看日志
docker compose logs -f

# 重建并启动
docker compose up -d --build

# 停止
docker compose down
```

---

## 作者与说明

- 作者：**coldboy404**
- 说明：本项目用于 Cloudflare DNS 优选 IP 自动更新，重点优化国内服务器部署体验。
