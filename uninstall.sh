cat > uninstall.sh <<'EOF'
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
echo -e "${RED}   警告：此操作将执行【毁灭性】清理   ${PLAIN}"
echo -e "${RED}   1. 停止所有优选进程 ${PLAIN}"
echo -e "${RED}   2. 删除项目所有文件和配置 ${PLAIN}"
echo -e "${RED}   3. 卸载 Go 环境及环境变量 ${PLAIN}"
echo -e "${RED}   4. 清除相关的定时任务 ${PLAIN}"
echo -e "${RED}=================================================${PLAIN}"
read -p "确认要彻底卸载吗？(y/n): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "已取消。"
    exit 0
fi

# 1. 停止进程
echo -e "${YELLOW}[*] 1. 正在停止进程...${PLAIN}"
pkill -f montecarlo-ip-searcher
pkill -f cfip
# 再次确认，防止遗漏
killall montecarlo-ip-searcher 2>/dev/null
killall cfip 2>/dev/null

# 2. 清理定时任务
echo -e "${YELLOW}[*] 2. 正在清理 Crontab 定时任务...${PLAIN}"
crontab -l > /tmp/cron.bak 2>/dev/null
# 过滤掉 cfip 和 montecarlo 相关的任务
crontab -l 2>/dev/null | grep -v "cfip" | grep -v "montecarlo-ip-searcher" | crontab -
echo "定时任务已净化。"

# 3. 删除文件
echo -e "${YELLOW}[*] 3. 正在删除项目文件...${PLAIN}"
# 删除主程序目录
rm -rf /opt/montecarlo-ip-searcher
# 删除快捷指令
rm -f /usr/local/bin/cfip
rm -f /usr/local/bin/cfip.sh
# 删除可能存在的源码目录 (包括改名及其旧名字)
rm -rf /root/cfipup2dns
rm -rf /root/cfip2ddns
rm -rf /root/my-cfip-project
echo "项目文件已删除。"

# 4. 卸载 Go 环境 (对应 install.sh 的安装)
echo -e "${YELLOW}[*] 4. 正在卸载 Go 语言环境...${PLAIN}"
# 删除 Go 安装目录
rm -rf /usr/local/go
# 删除 Go 工作目录 (pkg, src, bin) 和 缓存
rm -rf /root/go
rm -rf /root/.cache/go-build
rm -rf /root/.config/go

# 5. 清理环境变量 (这是关键，防止残留 PATH)
echo -e "${YELLOW}[*] 5. 清理系统环境变量...${PLAIN}"
# 使用 sed 删除包含 /usr/local/go/bin 的行
sed -i '/\/usr\/local\/go\/bin/d' /etc/profile
sed -i '/\/usr\/local\/go\/bin/d' ~/.bashrc
# 刷新一下当前环境 (虽然脚本退出后可能会失效，但心理上舒服点)
source /etc/profile 2>/dev/null

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   卸载完成！系统已恢复纯净。   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"
echo -e "提示：系统依赖工具 (jq, wget, git) 已保留，以免影响其他软件运行。"
EOF
chmod +x uninstall.sh
