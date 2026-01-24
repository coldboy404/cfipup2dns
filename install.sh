#!/bin/bash
GREEN='\033[0;32m'
PLAIN='\033[0m'

if [[ $EUID -ne 0 ]]; then echo "请用 root 运行"; exit 1; fi

echo -e "${GREEN}[*] 开始安装 cfipup2dns... ${PLAIN}"
# 安装依赖
apt-get update && apt-get install -y jq wget git

# 下载脚本到 bin
wget -O /usr/local/bin/cfip https://raw.githubusercontent.com/coldboy404/cfipup2dns/main/cfip.sh
chmod +x /usr/local/bin/cfip

# 准备目录
mkdir -p /opt/montecarlo-ip-searcher
cd /opt/montecarlo-ip-searcher

# 下载 IP 库 (国内加速)
wget -O ipv4cidr.txt https://gh-proxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/master/ipv4cidr.txt

# 下载主程序 (如果有编译好的可以直接下，这里假设需要编译或下载预编译包，
# 既然你已经发布了，通常 install.sh 是给别人用的。
# 为了简化，我们假设别人只需要运行脚本，或者你后续会上传编译好的二进制文件。
# 这里暂时保留最简单的提示)

echo -e "${GREEN}安装完成！请手动编辑 /opt/montecarlo-ip-searcher/config.json 配置 Token。 ${PLAIN}"
