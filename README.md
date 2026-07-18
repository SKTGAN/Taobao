# 淘宝辅助购买工具 V2

一个面向 Windows 的淘宝辅助购买原型。当前阶段支持一个已扫码登录账号和一个运行中任务：用户提前核对商品、SKU、数量、地址与价格并进行单次授权，程序按指定时间提交订单，识别到淘宝官方待付款页面后停止。

> 软件不会保存淘宝密码、不会处理或绕过验证码、不会伪造浏览器指纹，也不会自动支付。自动提交订单必须由用户针对当前任务显式授权；程序重启后授权自动失效。

## 功能

- 侧边导航式 Flet GUI
- SQLite 本地数据持久化
- 多账号本地管理
- 每个账号独立的 Chrome 用户资料目录
- 使用淘宝 App 扫码登录
- 商品链接、数量和 SKU 备注管理
- 毫秒级计划时间和单账号定时任务
- 商品页、登录页、确认订单页、验证页和待付款页识别
- 第一阶段固定 SKU、款式说明和数量，并提前进入确认订单页
- 用户在确认订单页人工处理地址、价格、号码保护和协议选项后，再执行最终授权
- 到点只点击已经人工核对过的“提交订单”，到待付款即停止
- 任务停止、失败原因和运行日志
- 应用重启后通过 `DevToolsActivePort` 重新连接账号 Chrome
- 登录、商品、任务和异常日志
- 固定使用 Google Chrome，不调用系统默认 Edge
- 不需要 ChromeDriver 或 Selenium Manager

## 当前不包含

- 验证码或滑块自动处理
- Canvas、WebGL、UA 等浏览器指纹伪造
- 动态代理池或自动更换 IP
- 自动发现和枚举淘宝页面上的全部 SKU 选项（当前由用户填写 SKU ID 或粘贴已选款式链接）
- 自动支付或保存支付信息
- 多账号并发、多商品组合和无限重试
- 秒杀成功率保证

淘宝页面结构、库存、账号资格和平台风控都可能变化。任何自动化方案都不能保证成交或保证账号不受限制。

## 工作流程

```mermaid
flowchart LR
    A[添加账号备注] --> B[创建独立 Chrome 资料目录]
    B --> C[打开淘宝官方登录页]
    C --> D[用户使用淘宝 App 扫码]
    D --> E[检查我的淘宝页面]
    E --> F[添加商品和 SKU 备注]
    F --> G[创建单账号定时任务]
    G --> H[准备商品并执行页面预检]
    H --> I[第一步固定本次 SKU 数量和款式说明]
    I --> J[应用精确 SKU 并提前进入确认订单]
    J --> K[人工处理地址 价格 号码保护和协议]
    K --> L[关闭规则说明标签并执行最终授权]
    L --> M[到点仅提交已核对订单]
    M --> N[进入待付款后停止]
```

## 实现原理

### 1. 账号隔离

每次添加账号时，程序会在当前 Windows 用户的通用数据目录下创建一个随机命名的 Chrome 用户资料目录。Cookie、LocalStorage 和登录状态由真实 Chrome 自己管理，不会写入项目配置或 Git 仓库。

数据库的 `accounts` 表只保存：

- 本地账号备注
- Chrome 资料目录路径
- 登录状态
- 启用状态和更新时间

数据库中没有密码字段。

### 2. 扫码登录

扫码登录由 `src/safe_browser.py` 实现：

1. 查找本机正式版 `chrome.exe`。
2. 使用独立 `--user-data-dir` 启动可见 Chrome。
3. 打开淘宝官方登录页。
4. 用户自行使用淘宝 App 扫码和处理平台验证。
5. 点击“检查登录”时打开“我的淘宝”，仅根据页面是否跳回官方登录域名判断状态。

程序不会读取密码、二维码内容或 Cookie。

### 3. 本地状态端口

独立 Chrome 使用随机的本机调试端口。端口只绑定 `127.0.0.1`，用途是新建标签页并读取当前页面 URL，从而判断是否跳回登录页。它不上传浏览历史或登录数据。

### 4. 数据持久化

`src/v2_store.py` 使用 Python 标准库 `sqlite3`。默认数据目录与项目代码分离，数据库位于：

```text
%LOCALAPPDATA%\TaobaoAssistant\taobao_assistant_v2.db
```

配置文件、Chrome 资料、服务日志也位于 `%LOCALAPPDATA%\TaobaoAssistant\`。因此项目可放在任意磁盘或中文目录，更新代码时不会覆盖登录资料。可通过环境变量 `TAOBAO_ASSISTANT_DATA_DIR` 改为其他目录。

旧版本如果存在项目内的 `data/taobao_assistant_v2.db`，首次启动会把数据库和账号 Chrome 资料复制到通用数据目录，并更新数据库中的资料路径。旧目录会保留作为备份，不会自动删除。

主要数据表：

- `accounts`：账号备注与独立资料目录
- `products`：商品链接、数量和 SKU 备注
- `tasks`：账号与商品组合的辅助购买任务，以及本次任务专用的 SKU 链接、款式说明和数量
- `events`：登录、商品、任务和异常日志

`data/` 已加入 `.gitignore`，禁止提交本地登录状态。

## 环境要求

- Windows 10 或 Windows 11
- Python 3.10 及以上，建议 Python 3.12
- Google Chrome 正式版
- 能正常访问淘宝和 Python 包索引的网络

## 一键安装（交付推荐）

1. 安装 64 位 Python 3.10 或更高版本，安装时勾选 **Add Python to PATH**。
2. 安装 Google Chrome 正式版。
3. 解压项目后，双击 `install-and-start.cmd`。

脚本会自动创建 `.venv`、安装依赖、初始化通用数据目录并启动程序。第一次安装需要能访问 Python 包索引；以后直接双击 `start-v2.ps1` 即可。

详细交付步骤见 [`docs/delivery-guide.md`](docs/delivery-guide.md)。

## 手动安装

```powershell
git clone https://github.com/SKTGAN/Taobao.git
cd Taobao

python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

## 启动

推荐方式：

```powershell
.\start-v2.ps1
```

脚本会：

1. 从 `%LOCALAPPDATA%\TaobaoAssistant\config.json` 读取 Chrome 路径和服务端口。
2. 端口被其他程序占用时，自动选择一个临时空闲端口。
3. 在后台启动 GUI 服务，并把输出写入通用数据目录。
4. 明确使用 Google Chrome 打开页面，避免系统默认打开 Edge。

也可以手动启动：

```powershell
python main.py --no-browser --port 8550
```

然后用 Chrome 打开：

```text
http://127.0.0.1:8550
```

## 第一次使用

1. 程序会自动检查 Windows、Python、数据目录、Google Chrome、端口以及淘宝/支付宝 HTTPS 连接。
2. 如果有未通过项，进入“设置”查看原因；也可手动填写 `chrome.exe` 完整路径和本地端口。
3. 进入“账号管理”，点击“添加账号”，只填写本地备注。
4. 点击“扫码登录”，在新 Chrome 窗口中使用淘宝 App 扫码。
5. 登录完成后回到 V2，点击“检查登录”。
6. 添加一个普通、低价商品进行验证。
7. 创建任务，计划时间建议先设置为当前时间后 5 分钟。
8. 点击“准备商品”，等待商品页预检通过。
9. 回到任务中心点击“授权”。如果商品有颜色、口味、尺码等款式，勾选“必须固定 SKU”，填写 SKU ID，或粘贴已经选好款式的同一商品链接；同时填写款式说明和数量。
10. 勾选第一步确认后，程序会应用精确 SKU 并进入确认订单页，但不会提交订单。
11. 在 Chrome 的确认订单页人工核对收货地址、价格、数量、号码保护和协议选项。若打开了“隐私号保护规则说明”“协议”或“规则”标签，请处理完相关选项并关闭这些辅助标签。
12. 回到任务中心点击“核对后授权”，再次确认计划时间并勾选最终授权。只有确认订单页和提交按钮仍然有效、且没有辅助规则标签打开时，任务才进入“等待中”。
13. 到点后不要关闭或切换账号 Chrome 中的确认订单页；程序只点击已核对页面的“提交订单”，并在“待付款”状态停止。

首次真实验证请使用你自己的普通低价商品，并全程看着 Chrome。出现验证码、风控或页面结构变化时，任务会转为“需人工处理”。

## 测试

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

本地真实 Chrome 模拟结算回归测试（只访问 `127.0.0.1`，不会访问淘宝）：

```powershell
$env:RUN_BROWSER_TESTS = "1"
python -m unittest tests.test_browser_checkout_flow -v
```

GitHub Actions 会在推送和 Pull Request 时使用 Python 3.12 运行测试。

## 项目结构

```text
Taobao/
├── main.py                  # 程序入口
├── install-and-start.cmd    # 接收方双击的一键安装入口
├── install.ps1              # 创建环境、安装依赖和初始化数据
├── start-v2.ps1             # 读取配置并启动 Chrome/服务
├── src/
│   ├── app_config.py       # 用户级 Chrome/端口配置
│   ├── environment_check.py # 首次运行环境自检
│   ├── cli.py              # GUI 服务参数和入口
│   ├── gui_v2.py           # Flet 管理界面
│   ├── cdp_client.py       # 本机 Chrome DevTools 通道
│   ├── page_automation.py  # 页面识别和有限点击动作
│   ├── safe_browser.py     # 真实 Chrome、扫码登录和会话重连
│   ├── task_runner.py      # 单账号定时提交状态机
│   ├── v2_store.py         # SQLite 数据层
│   └── paths.py            # 通用数据路径与旧数据迁移
├── tests/
│   ├── mock_shop/          # 仅本机使用的模拟商品/结算页面
│   └── test_*.py
└── .github/workflows/
    └── tests.yml
```

## 隐私与安全

- 不要提交 `data/`、Chrome profile、SQLite 数据库或运行日志。
- 不要在 Issue、截图或日志中公开 Cookie、手机号、地址、订单号和支付信息。
- 遇到验证码、短信验证或账号保护页面时，请由账号本人在淘宝官方页面处理。
- 代理不是必需配置；能正常访问淘宝时应保持系统网络稳定，不要频繁切换出口。

## 可移植配置

设置页可以保存 Chrome 路径和端口。高级交付场景也可在启动前设置：

```powershell
$env:TAOBAO_ASSISTANT_DATA_DIR = "D:\TaobaoAssistantData"
$env:TAOBAO_ASSISTANT_CHROME = "D:\Apps\Chrome\Application\chrome.exe"
$env:TAOBAO_ASSISTANT_PORT = "9000"
.\start-v2.ps1
```

优先级为：启动参数或环境变量 > `%LOCALAPPDATA%\TaobaoAssistant\config.json` > 自动探测/默认值。登录资料包含敏感会话信息，不应在不同人员之间复制；每位接收者应在自己的电脑上重新扫码登录。

## 后续计划

- 订单记录与 Excel 导出
- 商品和任务编辑/删除
- 登录失效提醒
- 任务到期桌面通知
- 页面选择器的版本化适配与更多回归样本
- 在单账号流程稳定后再评估多账号和多商品组合

真实平台能力将继续保持“可见浏览器、扫码登录、人工核对、单次授权、不自动支付”的边界。

## 上游与许可证

本项目基于 [mc-yzy15/TaoBaoGoods](https://github.com/mc-yzy15/TaoBaoGoods) 的 Flet 项目结构进行重构，保留上游 GPL-3.0 许可要求。V2 重写了账号、数据存储、浏览器启动和 GUI 工作流，并移除了密码登录与指纹伪造依赖。

项目采用 [GNU General Public License v3.0](LICENSE)。分发修改版或可执行文件时，请同时遵守 GPL-3.0 的源代码提供和许可证保留要求。

