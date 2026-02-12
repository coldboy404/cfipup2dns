# cfipup2dns

专为**国内网络**优化的 Cloudflare 优选 IP 自动 DDNS 工具。
自动筛选**下载速度最快 (Mbps)** 的 Top N IP（默认 5）并更新到 DNS。

> 💡 **致谢**：项目灵感来自 [Leo-Mu/montecarlo-ip-searcher](https://github.com/Leo-Mu/montecarlo-ip-searcher)，感谢大佬的开源，本项目为了简化部署流程而作。

‼️写在前面：在国内机器上跑，否则没意义

‼️免责声明：本项目仅为个人测试学习所用，请勿用于任何非法活动，一切后果与作者无关

## 🚀 快速开始

### 1. 安装（使用了国内加速源）
```bash
git clone https://hk.gh-proxy.org/https://github.com/coldboy404/cfipup2dns.git
cd cfipup2dns
bash install.sh
```

### 2. 配置 (也可在前面脚本执行完通过命令行提示输入)
安装后请修改配置文件，填入你的 Cloudflare 信息：
```bash
nano /opt/montecarlo-ip-searcher/config.json
```
* **token**: Cloudflare API Token
* **zone_id**: 域名 Zone ID
* **domain**: 你自己的域名 (如 `cf.example.com`)

### 3. 使用（友情提醒：在优选时关闭机器的代理网络）
每次优选预估5到10分钟
* **手动运行**: `cfip`
* **查看日志**: `tail -f /opt/montecarlo-ip-searcher/cron.log`

*(脚本已自动配置每 2小时 运行一次，并开机自启)*

### 4. 可选参数（环境变量覆盖）
可通过环境变量调整测速策略：
```
TOP_N=5            # 选取多少个最快 IP
TOP_TEST=50        # 参与详细测速的数量
DOWNLOAD_BYTES=5000000
DOWNLOAD_TIMEOUT=8s
CONCURRENCY=50
```
示例：
```bash
TOP_N=3 TOP_TEST=30 cfip
```

## 🗑️ 卸载
```bash
cd cfipup2dns && bash uninstall.sh
```
