# 淘宝辅助购买 V2 交付与部署指南

## 1. 交付目标

本版本按“代码目录可移动、用户数据与代码分离、接收方可自行安装”的方式交付。项目可以放在桌面、D 盘、中文目录或 Git 仓库中运行，不依赖开发者电脑用户名和绝对路径。

程序仍有明确边界：只在用户人工核对并单次授权后提交订单，到淘宝/支付宝官方待付款或收银台页面即停止；不保存密码、不处理验证码、不自动支付。

## 2. 接收方电脑要求

- Windows 10 或 Windows 11（64 位）
- Python 3.10 或更高版本，建议 Python 3.12
- Google Chrome 正式版
- 首次安装时可访问 Python 包索引
- 运行时可正常访问淘宝和支付宝相关域名

Python 安装时必须勾选 **Add Python to PATH**。如果公司网络、代理、VPN 或安全软件拦截 HTTPS，首次自检会给出失败提示，需要先让接收方的网络管理员处理。

## 3. 推荐交付内容

将完整 Git 仓库或源码压缩包交给接收方，但不要包含以下本机数据：

- `.venv/`
- `data/`
- `profiles/`
- `*.db`、`*.db-wal`、`*.db-shm`
- `*.log`
- Cookie、账号截图、地址、订单号或支付信息

这些内容已由 `.gitignore` 排除。每位接收者必须在自己的电脑上扫码登录，不能把开发者的 Chrome 资料目录作为交付物。

## 4. 接收方一键安装

1. 解压项目，尽量不要放在只读目录或 `C:\Program Files` 下。
2. 双击 `install-and-start.cmd`。
3. 脚本自动检查 Python 3.10+、创建 `.venv`、安装依赖并初始化数据目录。
4. 安装完成后自动用 Google Chrome 打开管理页面。
5. 如果窗口显示错误，不要立即关闭，复制完整错误信息给维护者。

以后启动可双击 `start-v2.ps1`。如果 Windows 阻止直接运行 PowerShell 脚本，可继续使用 `install-and-start.cmd`，它会以当前进程临时放行脚本，不修改整机执行策略。

## 5. 通用数据目录

默认目录：

```text
%LOCALAPPDATA%\TaobaoAssistant\
```

主要内容：

```text
TaobaoAssistant/
├── config.json                 # Chrome 路径、服务端口、首次自检状态
├── taobao_assistant_v2.db      # 账号备注、商品、任务、运行日志
├── profiles/                   # 每个账号独立的 Chrome 登录资料
├── service.out.log             # 后台服务标准输出
└── service.err.log             # 后台服务错误输出
```

更新或重新克隆代码不会覆盖上述目录。卸载项目代码也不会自动删除登录数据；如需彻底清理，先退出项目专用 Chrome 和程序，再由账号本人手动删除该目录。

### 旧版本迁移

如果旧项目目录中存在 `data/taobao_assistant_v2.db`，首次运行会：

1. 使用 SQLite 安全备份方式复制数据库；
2. 把能读取的账号 Chrome 资料复制到通用数据目录；
3. 更新新数据库中的资料路径；
4. 保留原 `data/` 作为备份。

迁移前最好关闭项目专用 Chrome，避免部分缓存文件被锁定。程序不会自动删除旧数据。

## 6. Chrome 与端口配置

应用会自动查找常见安装位置中的 `chrome.exe`。便携版 Chrome 或非标准安装位置可在“设置”页填写完整路径，例如：

```text
D:\Apps\GoogleChrome\Application\chrome.exe
```

默认管理端口为 `8550`，只监听 `127.0.0.1`，不会对局域网公开。端口被其他程序占用时，启动脚本会为本次运行选择临时空闲端口。设置页修改端口后需要重启。

企业部署可以使用环境变量：

```powershell
$env:TAOBAO_ASSISTANT_DATA_DIR = "D:\TaobaoAssistantData"
$env:TAOBAO_ASSISTANT_CHROME = "D:\Apps\Chrome\chrome.exe"
$env:TAOBAO_ASSISTANT_PORT = "9000"
.\start-v2.ps1
```

## 7. 首次运行环境自检

首次打开后，程序自动检查：

- 是否为 Windows 10/11 环境
- Python 是否达到 3.10+
- 用户数据目录是否可写
- Google Chrome 是否存在
- 配置端口是否合法
- `www.taobao.com:443` 是否能建立 TLS 连接
- `tbapi.alipay.com:443` 是否能建立 TLS 连接

结果保存在当前会话的“设置”页。首次自动检查后仍可随时点击“运行环境自检”。自检只验证基础环境，不代表淘宝库存、账号资格、风控或页面结构一定允许成交。

## 8. 交付验收清单

在接收方电脑上逐项确认：

- [ ] 双击安装脚本能创建 `.venv`
- [ ] Chrome 能打开本地管理页
- [ ] 设置页显示正确的数据目录和 Chrome 路径
- [ ] 环境自检无未解释的失败项
- [ ] 添加账号后能打开独立 Chrome 并扫码
- [ ] 重启程序后账号资料仍存在
- [ ] 使用普通低价商品完成“准备商品 → 固定 SKU/数量 → 提前进入确认订单 → 人工处理号码保护/协议 → 核对后授权 → 到点提交”演练
- [ ] 程序到待付款/官方收银台停止，没有自动付款动作
- [ ] Git 仓库中没有 `data/`、数据库、日志和 Chrome profile

## 9. 常见故障

### 未找到 Python

安装 Python 3.10+ 64 位并勾选 PATH，关闭旧终端后重新双击脚本。

### pip 安装失败

检查网络、系统时间、企业代理和 TLS 证书。不要通过关闭证书校验来长期解决。

### 未找到 Chrome

安装正式版 Chrome，或在设置页/环境变量中指定 `chrome.exe` 完整路径。

### 本地页面打不开

查看 `%LOCALAPPDATA%\TaobaoAssistant\service.err.log`。如果 8550 被占用，启动脚本会显示实际使用的临时端口。

### 淘宝可打开但支付宝页面连接关闭

这是网络出口、代理/TUN、DNS、防火墙或安全软件问题，不是本地页面点击逻辑本身。先在同一 Chrome 中直接验证相关官方域名；更换网络后重新运行环境自检。

## 10. 维护者升级流程

1. 备份 Git 分支并运行完整测试。
2. 只更新项目代码，不覆盖 `%LOCALAPPDATA%\TaobaoAssistant`。
3. 在新代码目录运行 `install.ps1 -SkipStart` 更新依赖。
4. 运行 `start-v2.ps1`，完成环境自检和普通低价商品回归。
5. 确认待付款识别后再交付。
