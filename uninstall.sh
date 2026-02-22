#!/bin/bash
set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
PLAIN='\033[0m'

if [[ $EUID -ne 0 ]]; then
  echo -e "${RED}错误：请使用 root 权限运行此脚本。${PLAIN}"
  exit 1
fi

echo -e "${RED}=================================================${PLAIN}"
echo -e "${RED}   警告：此操作将执行【毁灭性】清理   ${PLAIN}"
echo -e "${RED}   1. 停止优选相关进程 ${PLAIN}"
echo -e "${RED}   2. 删除项目文件和配置 ${PLAIN}"
echo -e "${RED}   3. 清除相关定时任务 ${PLAIN}"
echo -e "${RED}=================================================${PLAIN}"
read -rp "确认要彻底卸载吗？(y/n): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "已取消。"
  exit 0
fi

echo -e "${YELLOW}[*] 1. 正在停止进程...${PLAIN}"
# 仅杀扫描器本体，避免误杀当前菜单/卸载 shell。
pkill -x montecarlo-ip-searcher 2>/dev/null || true

echo -e "${YELLOW}[*] 2. 正在清理 Crontab 定时任务...${PLAIN}"
if crontab -l >/tmp/cron.bak 2>/dev/null; then
  grep -v "cfip-run" /tmp/cron.bak | grep -v "montecarlo-ip-searcher" >/tmp/cron.new || true
  crontab /tmp/cron.new
  rm -f /tmp/cron.bak /tmp/cron.new
  echo "定时任务已清理。"
else
  echo "未检测到 crontab，跳过。"
fi

echo -e "${YELLOW}[*] 3. 正在删除项目文件...${PLAIN}"
rm -rf /opt/montecarlo-ip-searcher
rm -f /usr/local/bin/cfip /usr/local/bin/cfip-run /usr/local/bin/cfip-menu
rm -f /tmp/cfipup2dns-install.sh /tmp/cfipup2dns-uninstall.sh
rm -rf /tmp/cfipup2dns-assets /tmp/cfipup2dns-menu

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   卸载完成！${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"
