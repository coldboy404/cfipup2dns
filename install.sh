#!/bin/bash

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
PLAIN='\033[0m'

# 1. 基础环境清理与准备
echo -e "${GREEN}[*] 清理旧文件...${PLAIN}"
rm -rf /opt/montecarlo-ip-searcher
rm -rf /usr/local/go
# 确保当前会话的 PATH 包含 Go，防止找不到命令
export PATH=$PATH:/usr/local/go/bin

# 2. 重新下载 Go (如果不存在)
if ! command -v go &> /dev/null; then
    echo -e "${GREEN}[*] 安装 Go 1.23...${PLAIN}"
    # 使用 quiet 模式但显示进度条
    wget -q --show-progress https://go.dev/dl/go1.23.4.linux-amd64.tar.gz -O /tmp/go.tar.gz
    tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz
fi

# ==========================================
# 【关键修改】配置 Go 国内代理，解决超时问题
# ==========================================
echo -e "${GREEN}[*] 配置 Go 国内代理 (goproxy.cn)...${PLAIN}"
export PATH=$PATH:/usr/local/go/bin
go env -w GOPROXY=https://goproxy.cn,direct
# ==========================================

# 3. 拉取源码
echo -e "${GREEN}[*] 拉取源码...${PLAIN}"
PROJECT_DIR="/opt/montecarlo-ip-searcher"
# 注意：如果你是想拉取自己的仓库，记得把下面这个地址改成你的
git clone https://github.com/Leo-Mu/montecarlo-ip-searcher.git $PROJECT_DIR

if [ ! -d "$PROJECT_DIR" ]; then
    echo -e "${RED}[!] 源码拉取失败，请检查网络。${PLAIN}"
    exit 1
fi

cd $PROJECT_DIR

# ---------------------------------------------------------
# 核心修复 1: 强制修复 go.mod 中离谱的 "go 1.25.5" 错误
# ---------------------------------------------------------
echo -e "${GREEN}[*] 修复作者的 go.mod 版本错误...${PLAIN}"
# 先 tidy 一次让它生成 go.mod (如果缺失)
go mod tidy 2>/dev/null || true 
go mod edit -go=1.23
go mod edit -toolchain=none

# ---------------------------------------------------------
# 核心修复 2: 自动寻找 main.go 所在的真实目录
# ---------------------------------------------------------
echo -e "${GREEN}[*] 正在自动寻找编译入口...${PLAIN}"
# 再次运行 tidy 下载依赖 (这次有代理了，会很快)
go mod tidy

# 寻找含有 "package main" 的文件路径
MAIN_FILE=$(find . -name "main.go" -print0 | xargs -0 grep -l "package main" | head -n 1)

if [ -z "$MAIN_FILE" ]; then
    echo -e "${RED}[!] 找不到 main.go，无法编译！列出目录结构供排查：${PLAIN}"
    find . -maxdepth 3
    exit 1
fi

# 获取 main.go 所在的目录
BUILD_DIR=$(dirname "$MAIN_FILE")
echo -e "${GREEN}[+] 找到编译入口: $BUILD_DIR${PLAIN}"

# 开始编译
echo -e "${GREEN}[*] 正在编译...${PLAIN}"
go build -o montecarlo-ip-searcher "$BUILD_DIR"

if [ ! -f "./montecarlo-ip-searcher" ]; then
    echo -e "${RED}[!] 编译失败！${PLAIN}"
    exit 1
fi
chmod +x montecarlo-ip-searcher

# 5. 配置文件 (Config)
echo -e "${GREEN}---------------------------------------------${PLAIN}"
echo -e "${GREEN}   编译成功！请配置 Cloudflare   ${PLAIN}"
echo -e "${GREEN}---------------------------------------------${PLAIN}"
read -p "API Token: " CF_KEY
read -p "Zone ID: " CF_ZONE
read -p "域名: " CF_DOMAIN

cat > config.json <<EOF
{
  "max_thread": 100,
  "max_duration": 60,
  "min_speed": 10,
  "source_url": "https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/master/source.txt",
  "cloudflare": {
    "enabled": true,
    "token": "$CF_KEY",
    "zone_id": "$CF_ZONE",
    "domain": "$CF_DOMAIN",
    "record_type": "A",
    "proxied": false
  }
}
EOF

# 6. 生成运行脚本
cat > run.sh <<EOF
#!/bin/bash
cd $PROJECT_DIR
./montecarlo-ip-searcher | tee -a run.log
EOF
chmod +x run.sh

# 7. 生成快捷命令
cat > /usr/local/bin/cfip <<EOF
#!/bin/bash
PROJECT_DIR="/opt/montecarlo-ip-searcher"
cd \$PROJECT_DIR
./montecarlo-ip-searcher
EOF
chmod +x /usr/local/bin/cfip

# 8. 定时任务
CRON="0 4 * * * /bin/bash $PROJECT_DIR/run.sh >> $PROJECT_DIR/run.log 2>&1"
(crontab -l 2>/dev/null | grep -v "montecarlo-ip-searcher"; echo "$CRON") | crontab -

echo -e "${GREEN}=============================================${PLAIN}"
echo -e "${GREEN}   终于成功了！输入 cfip 开始运行。   ${PLAIN}"
echo -e "${GREEN}=============================================${PLAIN}"
