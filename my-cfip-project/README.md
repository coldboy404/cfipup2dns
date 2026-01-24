# cfipup2dns

专为**国内网络**优化的 Cloudflare 优选 IP 自动 DDNS 工具。
自动筛选**下载速度最快 (Mbps)** 的 Top 5 IP 并更新到 DNS。

## 🚀 快速开始

### 1. 安装
```bash
git clone https://github.com/coldboy404/cfipup2dns.git
cd cfipup2dns
bash install.sh
```

### 2. 配置 (必须!)
安装后请修改配置文件，填入你的 Cloudflare 信息：
```bash
nano /opt/montecarlo-ip-searcher/config.json
```
* **token**: Cloudflare API Token
* **zone_id**: 域名 Zone ID
* **domain**: 优选域名 (如 `best.example.com`)

### 3. 使用
* **手动运行**: `cfip`
* **查看日志**: `tail -f /opt/montecarlo-ip-searcher/cron.log`

*(脚本已自动配置每 30 分钟运行一次，并开机自启)*

## 🗑️ 卸载
```bash
cd cfipup2dns && bash uninstall.sh
```
