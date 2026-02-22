# cfipup2dns

专为国内网络环境优化的 Cloudflare 优选 IP 自动 DDNS 工具。  
基于 `montecarlo-ip-searcher`，自动筛选最快 IP 并更新到 Cloudflare DNS。

> 灵感与核心能力来自 [Leo-Mu/montecarlo-ip-searcher](https://github.com/Leo-Mu/montecarlo-ip-searcher)

---

## 快速开始

```bash
git clone https://github.com/coldboy404/cfipup2dns.git
cd cfipup2dns
bash install.sh
```

一键更新：

```bash
cd cfipup2dns && git pull && bash install.sh
```

安装后配置：

```bash
nano /opt/montecarlo-ip-searcher/config.json
```

字段说明：
- `token`: Cloudflare API Token
- `zone_id`: Zone ID
- `domain`: 要更新的域名（如 `cf.example.com`）
- `ttl`: DNS TTL（默认 60）
- `proxied`: 是否走橙云（默认 false）

---

## 使用

手动运行：

```bash
cfip
```

日志：

```bash
tail -f /opt/montecarlo-ip-searcher/cron.log
```

安装脚本会自动添加：
- 每 2 小时执行一次
- 开机自启执行一次

---

## 新增能力（对齐上游近期更新）

- 多轮测速稳定化：`ROUNDS` + `SKIP_FIRST`
- 机房过滤：`COLO_ALLOW` / `COLO_EXCLUDE`
- 自定义测速文件：`DOWNLOAD_URL`
- IPv4 / IPv6 模式切换：`IP_MODE=4|6`（自动更新 A / AAAA）
- 支持 Cloudflare `ttl` / `proxied`

---

## 常用参数（环境变量覆盖）

```bash
# 基础
TOP_N=5
TOP_TEST=50
CONCURRENCY=50
BUDGET=3000
TIMEOUT=3s
HEADS=8

# 下载测速
DOWNLOAD_TOP=50
DOWNLOAD_BYTES=5000000
DOWNLOAD_TIMEOUT=8s
DOWNLOAD_URL=https://example.com/large.bin

# 稳定性
ROUNDS=6
SKIP_FIRST=1

# 机房过滤
COLO_ALLOW=HKG,SJC
COLO_EXCLUDE=LAX,DFW

# 模式
IP_MODE=4   # 4 或 6

# 透传给 mcis 的其它参数
MCIS_EXTRA_ARGS="-v"
```

示例：

```bash
# IPv6 + 多轮 + 限定机房
IP_MODE=6 ROUNDS=6 SKIP_FIRST=1 COLO_ALLOW=HKG,SIN TOP_N=3 cfip

# 用自定义大文件测速
DOWNLOAD_URL=https://your-domain.com/large.bin DOWNLOAD_TOP=20 cfip
```

查看帮助：

```bash
cfip --help
```

---

## 卸载

```bash
cd cfipup2dns && bash uninstall.sh
```

---

## 免责声明

本项目仅供学习和技术测试，请遵守当地法律法规与服务条款。
