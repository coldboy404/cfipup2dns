#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
PLAIN='\033[0m'

# 检查 Root 权限
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}错误：请使用 root 权限运行此脚本。${PLAIN}"
    exit 1
fi

echo -e "${RED}=================================================${PLAIN}"
echo -e "${RED}   警告：此操作将彻底卸载 cfipup2dns 及 Go 环境   ${PLAIN}"
echo -e "${RED}=================================================${PLAIN}"
read -p "确认要卸载吗？(y/n): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "已取消。"
    exit 0
fi

echo -e "${YELLOW}[*] 1. 正在停止相关进程...${PLAIN}"
pkill -f montecarlo-ip-searcher
pkill -f cfip

echo -e "${YELLOW}[*] 2. 正在清理定时任务 (Crontab)...${PLAIN}"
# 备份现有的 crontab
crontab -l > /tmp/cron.bak 2>/dev/null
# 过滤掉包含 cfip 和 montecarlo 的行，然后重新写入
crontab -l 2>/dev/null | grep -v "cfip" | grep -v "montecarlo-ip-searcher" | crontab -
echo "定时任务已清理。"

echo -e "${YELLOW}[*] 3. 正在删除项目文件...${PLAIN}"
rm -rf /opt/montecarlo-ip-searcher
rm -f /usr/local/bin/cfip
rm -f /usr/local/bin/cfip.sh
echo "项目文件已删除。"

echo -e "${YELLOW}[*] 4. 正在卸载 Go 语言环境...${PLAIN}"
rm -rf /usr/local/go
rm -rf /root/go
# 清理环境变量（如果是写在 profile 里的）
sed -i '/\/usr\/local\/go\/bin/d' /etc/profile
sed -i '/\/usr\/local\/go\/bin/d' ~/.bashrc
source /etc/profile 2>/dev/null
echo "Go 环境已清理。"

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   卸载完成！系统已恢复纯净。   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"
echo -e "提示：系统依赖工具 jq, wget, git 依然保留，以免影响其他软件。"
