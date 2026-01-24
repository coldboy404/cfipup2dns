# Cloudflare 优选 IP 自动测速与解析脚本 (Shell版)

这是一个基于 [MonteCarlo IP Searcher](https://github.com/Leo-Mu/montecarlo-ip-searcher) 的自动化脚本套件。

## 功能特点
1. **全自动安装**：自动解决 Go 环境、依赖问题。
2. **国内优化**：内置国内加速源，自动生成 IP 库，适应国内网络环境。
3. **智能优选**：自动筛选**速度最快**的 Top 10 IP。
4. **自动解析**：自动将优选结果更新到 Cloudflare DNS。
5. **无人值守**：支持开机自启和每 30 分钟自动运行。

## 使用方法

1. 下载脚本：
   `git clone https://github.com/coldboy404/cfip2ddns.git`
   
2. 进入目录：
   `cd cfip2ddns`

3. 运行安装：
   `bash install.sh`

4. 日常使用：
   `cfip`
