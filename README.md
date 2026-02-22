# cfipup2dns

专为国内网络环境优化的 Cloudflare 优选 IP 自动 DDNS 工具。  
基于 `montecarlo-ip-searcher`，自动筛选最快 IP 并更新到 Cloudflare DNS。

---

## 一键部署（最简单）

在服务器 SSH 里直接执行：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/menu.sh)"
```

如果访问 GitHub 慢：

```bash
bash -c "$(curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/menu.sh)"
```

> 会直接进入交互菜单，按数字选择即可。

---

## 命令说明

安装后提供：

- `cfip-run`：直接执行优选
- `cfip`：交互菜单管理
- `cfip-menu`：菜单别名（兼容）

---

## 默认行为

`cfip-run` 默认执行双栈：

- IPv4：写入 5 条 A
- IPv6：写入 5 条 AAAA

等价于：

```bash
IP_MODE=both TOP_N=5 cfip-run
```

---

## 菜单功能

```bash
cfip
```

支持：
1. 快速部署（安装/更新 + 配置 + 首次运行）
2. 安装 / 更新
3. 修改 Cloudflare 配置
4. 立即运行一次优选
5. 查看日志
6. 查看状态
7. 卸载

---

## 常用参数（环境变量）

```bash
IP_MODE=both   # 4 / 6 / both
TOP_N=5        # 每个模式写入数量
TOP_TEST=50
CONCURRENCY=50
BUDGET=3000
TIMEOUT=3s
HEADS=8

DOWNLOAD_TOP=50
DOWNLOAD_BYTES=5000000
DOWNLOAD_TIMEOUT=8s
DOWNLOAD_URL=https://example.com/large.bin

ROUNDS=6
SKIP_FIRST=1

COLO_ALLOW=HKG,SJC
COLO_EXCLUDE=LAX,DFW

MCIS_EXTRA_ARGS="-v"
```

示例：

```bash
# 默认双栈 5+5
cfip-run

# 仅 IPv4，写3条
IP_MODE=4 TOP_N=3 cfip-run

# 仅 IPv6，写3条
IP_MODE=6 TOP_N=3 cfip-run
```

---

## 配置文件

```bash
/opt/montecarlo-ip-searcher/config.json
```

字段：
- `token`
- `zone_id`
- `domain`
- `ttl`（默认 60）
- `proxied`（默认 false）
