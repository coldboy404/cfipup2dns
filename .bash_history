ifconfig
ping baidu.com
ping www.baidu.com
python3 --version
apt-get -y update
apt-get -y update
nano install_cf_ip.sh
nano install_cf_ip.sh
nano install_cf_ip.sh
chmod +x install_cf_ip.sh
sudo ./install_cf_ip.sh
./install_cf_ip.sh
./install_cf_ip.sh
chmod +x install_cf_ip.sh
./install_cf_ip.sh
#!/bin/bash
# 1. 尝试校准时间 (解决 SSL 报错)
echo -e "\033[0;33m[*] 正在校准系统时间...\033[0m"
apt-get install -y ntpdate
ntpdate pool.ntp.org
# 2. 清理旧的损坏目录 (彻底删除)
echo -e "\033[0;33m[*] 删除损坏的项目目录...\033[0m"
rm -rf /opt/montecarlo-ip-searcher
# 3. 重新克隆源码
echo -e "\033[0;33m[*] 重新克隆源码...\033[0m"
git clone https://github.com/Leo-Mu/montecarlo-ip-searcher.git /opt/montecarlo-ip-searcher
cd /opt/montecarlo-ip-searcher
# 4. 强制修复 go.mod 版本问题
# (防止它去下载不存在的 Go 1.25，强制使用本地安装的 1.23)
echo -e "\033[0;33m[*] 修正 go.mod 版本依赖...\033[0m"
export PATH=$PATH:/usr/local/go/bin
go mod edit -go=1.23
go mod edit -toolchain=none
# 设置国内代理，提高下载成功率 (可选，防止网络超时)
go env -w GOPROXY=https://goproxy.io,direct
# 5. 编译
echo -e "\033[0;33m[*] 开始编译...\033[0m"
go mod tidy
go build -o montecarlo-ip-searcher
# 6. 检查结果
if [ -f "./montecarlo-ip-searcher" ]; then     chmod +x montecarlo-ip-searcher;     echo -e "\033[0;32m======================================\033[0m";     echo -e "\033[0;32m[+] 恭喜！编译成功！\033[0m";     echo -e "\033[0;32m[+] 请输入 'cfip' 并选择 1 开始运行。\033[0m";     echo -e "\033[0;32m======================================\033[0m"; else     echo -e "\033[0;31m[!] 依然编译失败，请截图发给我。\033[0m"
    ls -la; fi
cd  
#!/bin/bash
# 1. 设置环境
export PATH=$PATH:/usr/local/go/bin
PROJECT_DIR="/opt/montecarlo-ip-searcher"
cd $PROJECT_DIR
echo -e "\033[0;33m[*] 正在尝试使用正确路径编译...\033[0m"
# 2. 关键修复：指定 cmd 目录进行编译
go mod tidy
# 这里加上了 ./cmd/montecarlo-ip-searcher
go build -o montecarlo-ip-searcher ./cmd/montecarlo-ip-searcher
# 3. 检查编译结果
if [ -f "./montecarlo-ip-searcher" ]; then     echo -e "\033[0;32m[+] 终于编译成功了！\033[0m";     chmod +x montecarlo-ip-searcher; else     echo -e "\033[0;31m[!] 编译依然失败，尝试备用路径...\033[0m"
    go build -o montecarlo-ip-searcher ./cmd;     if [ ! -f "./montecarlo-ip-searcher" ]; then         echo -e "\033[0;31m[!] 无法编译，请联系开发者确认目录结构。\033[0m";         exit 1;     fi; fi
#!/bin/bash
# 1. 设置环境
export PATH=$PATH:/usr/local/go/bin
PROJECT_DIR="/opt/montecarlo-ip-searcher"
cd $PROJECT_DIR
echo -e "\033[0;33m[*] 正在尝试使用正确路径编译...\033[0m"
# 2. 关键修复：指定 cmd 目录进行编译
go mod tidy
# 这里加上了 ./cmd/montecarlo-ip-searcher
go build -o montecarlo-ip-searcher ./cmd/montecarlo-ip-searcher
# 3. 检查编译结果
if [ -f "./montecarlo-ip-searcher" ]; then     echo -e "\033[0;32m[+] 终于编译成功了！\033[0m";     chmod +x montecarlo-ip-searcher; else     echo -e "\033[0;31m[!] 编译依然失败，尝试备用路径...\033[0m"
    go build -o montecarlo-ip-searcher ./cmd;     if [ ! -f "./montecarlo-ip-searcher" ]; then         echo -e "\033[0;31m[!] 无法编译，请联系开发者确认目录结构。\033[0m";         exit 1;     fi; fi
cfip
cd /opt/montecarlo-ip-searcher
# 关键修正：指定编译 cmd 子目录
/usr/local/go/bin/go build -o montecarlo-ip-searcher ./cmd/montecarlo-ip-searcher
# 赋予权限
chmod +x montecarlo-ip-searcher
cd  
# 1. 删除之前的项目文件夹
rm -rf /opt/montecarlo-ip-searcher
# 2. 删除之前的安装脚本
rm -f install_cf_ip.sh
# 3. 确保系统时间正确 (避免下载报错)
apt-get update && apt-get install -y ntpdate
ntpdate pool.ntp.org
# 停止可能正在运行的任务，卸载 apt 安装的老版本，删除手动安装的文件
pkill -f montecarlo-ip-searcher
apt-get remove -y golang golang-go golang-src
apt-get autoremove -y
rm -rf /usr/local/go
rm -rf /root/go
rm -rf /opt/montecarlo-ip-searcher
bash setup_cfip.sh
bash setup_cfip.sh
echo -e "nameserver 8.8.8.8\nnameserver 1.1.1.1" > /etc/resolv.conf
ping -c 3 google.com
bash setup_cfip.sh
bash install.sh
cat > install.sh
reboot
ip addr
nano /etc/ssh/sshd_config
systemctl restart sshd
bash install.sh
cfip
cat > /opt/montecarlo-ip-searcher/run.sh <<EOF
#!/bin/bash
cd /opt/montecarlo-ip-searcher
# 加上 --cidr-file 参数，指定使用本地的 ipv4cidr.txt
./montecarlo-ip-searcher --cidr-file ipv4cidr.txt | tee -a run.log
EOF

cfip
cat > /usr/local/bin/cfip <<EOF
#!/bin/bash
# 让 cfip 直接去跑我们改好的 run.sh
bash /opt/montecarlo-ip-searcher/run.sh
EOF

chmod +x /usr/local/bin/cfip
cfip
cat > /usr/local/bin/cfip <<'EOF'
#!/bin/bash

# ================= 配置区域 =================
PROJECT_DIR="/opt/montecarlo-ip-searcher"
RESULT_FILE="$PROJECT_DIR/scan_results.log"
FINAL_IPS="$PROJECT_DIR/best_10_speed.json"

# 读取配置
CF_TOKEN=$(jq -r .cloudflare.token $PROJECT_DIR/config.json)
CF_ZONE_ID=$(jq -r .cloudflare.zone_id $PROJECT_DIR/config.json)
CF_DOMAIN=$(jq -r .cloudflare.domain $PROJECT_DIR/config.json)
# ===========================================

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
PLAIN='\033[0m'

if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" == "null" ]; then
    echo -e "${RED}[!] 错误：配置文件中未找到 Cloudflare Token${PLAIN}"
    exit 1
fi

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   开始优选：目标最快速度 (Highest Speed) - Top 10   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"

cd $PROJECT_DIR
rm -f $RESULT_FILE

# 1. 运行扫描 (静默)
echo -e "${YELLOW}[*] 正在进行测速扫描 (约 60 秒)...${PLAIN}"
./montecarlo-ip-searcher --cidr-file ipv4cidr.txt > $RESULT_FILE 2>&1

# 2. 数据处理：按 download_mbps (下载速度) 从大到小排序，取前 10
echo -e "${YELLOW}[*] 正在筛选速度最快的 10 个 IP...${PLAIN}"

grep "^{" $RESULT_FILE | jq -s '
  map(select(.ok == true and .download_mbps > 0)) | 
  sort_by(.download_mbps) | reverse | 
  unique_by(.ip) | 
  .[0:10] | 
  {result: .}
' > $FINAL_IPS

IP_COUNT=$(jq '.result | length' $FINAL_IPS)

if [ "$IP_COUNT" -eq "0" ]; then
    echo -e "${RED}[!] 未找到有效 IP！可能是扫描时间太短或网络波动。${PLAIN}"
    exit 1
fi

echo -e "${GREEN}[+] 筛选成功！速度最快的 $IP_COUNT 个 IP：${PLAIN}"
printf "%-18s %-15s %-10s\n" "IP地址" "下载速度" "地区"
echo "------------------------------------------------"
jq -r '.result[] | "\(.ip) \t \(.download_mbps) Mbps \t \(.trace.colo)"' $FINAL_IPS | \
awk -F'\t' '{printf "%-18s %-15s %-10s\n", $1, $2, $3}'

# 3. 上传到 Cloudflare
echo -e "${YELLOW}[*] 正在更新 Cloudflare DNS 记录...${PLAIN}"

# 获取旧记录
EXISTING_RECORDS=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?type=A&name=$CF_DOMAIN" \
     -H "Authorization: Bearer $CF_TOKEN" \
     -H "Content-Type: application/json")

# 删除旧记录
echo "$EXISTING_RECORDS" | jq -r '.result[].id' | while read -r record_id; do
    curl -s -X DELETE "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records/$record_id" \
         -H "Authorization: Bearer $CF_TOKEN" \
         -H "Content-Type: application/json" > /dev/null
done

# 添加新记录
jq -r '.result[].ip' $FINAL_IPS | while read -r ip; do
    curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
         -H "Authorization: Bearer $CF_TOKEN" \
         -H "Content-Type: application/json" \
         --data '{"type":"A","name":"'"$CF_DOMAIN"'","content":"'"$ip"'","ttl":60,"proxied":false}' > /dev/null
done

echo -e "${GREEN}==============================================${PLAIN}"
echo -e "${GREEN}   搞定！速度最快的 10 个 IP 已解析到域名。   ${PLAIN}"
echo -e "${GREEN}==============================================${PLAIN}"
EOF

chmod +x /usr/local/bin/cfip
cfip
cfip
apt-get update && apt-get install -y jq
cfip
date -s "$(curl -s --head http://www.google.com | grep ^Date: | sed 's/Date: //g')"
apt-get -o Acquire::Check-Valid-Until=false update
apt-get -o Acquire::Check-Valid-Until=false install -y jq
cfip
cfip
# 1. 备份原配置
cd /opt/montecarlo-ip-searcher
cp config.json config.json.bak
# 2. 使用 jq 修改配置适应国内环境
jq '.min_speed = 0 | .max_thread = 20 | .source_url = "https://mirror.ghproxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/master/source.txt"' config.json > config_cn.json && mv config_cn.json config.json
echo "配置已修改为国内模式：不限最低速度，降低并发。"
# 删除旧的
rm -f ipv4cidr.txt
# 从加速源下载 IP 库
wget -O ipv4cidr.txt https://mirror.ghproxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/master/ipv4cidr.txt
# 检查文件大小，如果是非0说明下载成功
ls -lh ipv4cidr.txt
cd  
echo -e "nameserver 223.5.5.5\nnameserver 114.114.114.114" > /etc/resolv.conf
# 测试网络是否通畅
ping -c 2 baidu.com
cd /opt/montecarlo-ip-searcher
# 1. 删除刚才生成的 0 字节空文件
rm -f ipv4cidr.txt
# 2. 重新下载 (使用国内加速源)
wget -O ipv4cidr.txt https://mirror.ghproxy.com/https://raw.githubusercontent.com/Leo-Mu/montecarlo-ip-searcher/master/ipv4cidr.txt
