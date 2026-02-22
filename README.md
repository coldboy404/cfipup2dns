# cfipup2dns

专为国内网络环境优化的 Cloudflare 优选 IP 自动 DDNS 工具。  
基于 `montecarlo-ip-searcher`，自动筛选最快 IP 并更新到 Cloudflare DNS。

> 灵感与核心能力来自 [Leo-Mu/montecarlo-ip-searcher](https://github.com/Leo-Mu/montecarlo-ip-searcher)

---

## 一条命令全搞定（推荐）

在服务器 SSH 里直接执行：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/menu.sh)"
```

> 说明：该命令会拉起交互式菜单，一路选择即可完成安装、配置、运行、查看日志、卸载。

---

## 常规安装

```bash
git clone https://github.com/coldboy404/cfipup2dns.git
cd cfipup2dns
bash install.sh
```

一键更新：

```bash
cd cfipup2dns && git pull && bash install.sh
```

安装后会提供命令：
- `cfip-run`：直接执行优选
- `cfip`：交互式菜单管理
- `cfip-menu`：兼容别名（同菜单）

---

## 菜单功能

执行：

```bash
cfip
```

菜单支持：
1. 快速部署（安装/更新 + 配置 + 首次运行）
2. 安装 / 更新
3. 修改 Cloudflare 配置
4. 立即运行一次优选
5. 查看日志
6. 查看状态
7. 卸载

---

## 配置文件

路径：

```bash
/opt/montecarlo-ip-searcher/config.json
```

字段说明：
- `token`: Cloudflare API Token
- `zone_id`: Zone ID
- `domain`: 要更新的域名（如 `cf.example.com`）
- `ttl`: DNS TTL（默认 60）
- `proxied`: 是否走橙云（默认 false）

---

## 默认行为（已改）

`cfip-run` 默认会执行 **双栈更新**：
- **IPv4：筛选并写入 5 个 A 记录**
- **IPv6：筛选并写入 5 个 AAAA 记录**

等价于：

```bash
IP_MODE=both TOP_N=5 cfip-run
```

---

## 常用参数（环境变量覆盖）

```bash
# 模式（默认 both）
IP_MODE=both   # 4 / 6 / both
TOP_N=5        # 每个模式写入数量（默认每种 5 个）
TOP_TEST=50

# 搜索参数
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

# 透传给 mcis 的其它参数
MCIS_EXTRA_ARGS="-v"
```

示例：

```bash
# 默认：同时更新 IPv4(5) + IPv6(5)
cfip-run

# 仅 IPv4，写 3 个 A 记录
IP_MODE=4 TOP_N=3 cfip-run

# 仅 IPv6，写 3 个 AAAA 记录
IP_MODE=6 TOP_N=3 cfip-run

# 双栈 + 多轮 + 限定机房
IP_MODE=both ROUNDS=6 SKIP_FIRST=1 COLO_ALLOW=HKG,SIN TOP_N=5 cfip-run

# 用自定义大文件测速
DOWNLOAD_URL=https://your-domain.com/large.bin DOWNLOAD_TOP=20 cfip-run
```

查看帮助：

```bash
cfip-run --help
```

---

## 卸载

```bash
cd cfipup2dns && bash uninstall.sh
```

---

## 免责声明

本项目仅供学习和技术测试，请遵守当地法律法规与服务条款。
