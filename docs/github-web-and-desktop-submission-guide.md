# GitHub 网页端与图形界面提交操作指南

## 1. 文档目的

本文介绍如何把本地项目上传到 GitHub、创建 Pull Request（合并请求）、邀请朋友审查代码并合并到主分支。

示例仓库：

```text
https://github.com/SKTGAN/Taobao
```

GitHub 上的代码协作通常分为四个阶段：

```text
本地修改 -> 创建提交 -> 推送分支 -> 创建并合并 Pull Request
```

其中：

- 本地修改、创建提交和推送，推荐使用 GitHub Desktop。
- Pull Request、代码审查、自动测试和合并，推荐使用 GitHub 网页。
- 熟悉 Git 后，也可以使用 PowerShell 命令完成全部操作。

## 2. 三种操作方式对比

| 方式 | 适合场景 | 优点 | 注意事项 |
|---|---|---|---|
| GitHub 网页 | 少量文件、在线修改、创建和合并 PR | 无需安装命令行工具，操作直观 | 大批量文件、删除文件、复杂目录和换行规则处理不方便 |
| GitHub Desktop | 日常本地开发和提交 | 图形化查看修改、提交、推送和切换分支 | 需要安装客户端，首次使用需要登录 GitHub |
| Git/CLI | 开发者、自动化和精确控制 | 功能完整、速度快、便于排错 | 需要记忆命令，证书和网络异常时需要诊断 |

推荐组合：

```text
GitHub Desktop 负责本地提交和推送
GitHub 网页负责 Pull Request、审查和合并
```

## 3. 为什么不建议直接上传到 main

`main` 是项目的主分支，通常应保存已经检查并确认可用的代码。

每次开发新功能时，建议创建独立分支，例如：

```text
agent/single-account-checkout
```

新代码先进入独立分支，再通过 Pull Request 合并到 `main`。这样可以：

- 在合并前查看全部差异。
- 让朋友进行代码审查。
- 等待 GitHub Actions 自动测试。
- 发现问题时继续修改，而不影响主分支。
- 保留清晰的开发历史。

## 4. 仅使用 GitHub 网页上传文件

GitHub 网页支持上传文件并直接创建提交，适合少量、简单的文件修改。

### 4.1 打开仓库

在浏览器访问：

```text
https://github.com/SKTGAN/Taobao
```

确认右上角显示的是有权访问该仓库的 GitHub 账号。

### 4.2 进入上传页面

在仓库文件列表上方点击：

```text
Add file -> Upload files
```

### 4.3 选择文件

可以点击 `choose your files` 选择文件，也可以把文件或文件夹拖到浏览器页面。

不要上传以下本地内容：

```text
.venv/
data/
profiles/
*.db
*.db-wal
*.db-shm
*.log
```

也不要上传包含以下信息的截图或配置：

- 淘宝 Cookie 或登录状态。
- 手机号和收货地址。
- 订单号。
- 支付账号或支付信息。
- API Key、访问令牌或私人密码。

### 4.4 填写提交说明

在页面下方填写简短的提交说明，例如：

```text
新增单账号定时辅助购买流程
```

### 4.5 创建新分支

如果页面允许选择提交目标，优先选择：

```text
Create a new branch for this commit and start a pull request
```

不要在没有检查的情况下直接提交到 `main`。

### 4.6 网页上传限制

根据 GitHub 官方说明：

- 浏览器上传时，单个文件最大为 25 MiB。
- 网页一次最多上传 100 个文件。
- 网页上传会忽略 `.gitattributes` 中的处理逻辑。
- 受保护分支可能禁止直接通过网页上传或修改文件。

官方说明：

- [Adding a file to a repository](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository)
- [Uploading a project to GitHub](https://docs.github.com/en/get-started/start-your-journey/uploading-a-project-to-github)

对于包含多个 Python 模块、测试和文档的项目，建议使用 GitHub Desktop 或 Git，而不是反复通过网页上传。

## 5. 使用 GitHub Desktop 提交本地项目

GitHub Desktop 是最适合初学者的本地提交方式。它不是网页，但提供完整的图形界面，不需要记忆大部分 Git 命令。

### 5.1 安装并登录

1. 访问 `https://desktop.github.com/`。
2. 下载并安装 GitHub Desktop。
3. 启动后登录 GitHub 账号。
4. 确认账号对目标仓库具有写入权限。

### 5.2 添加已有本地仓库

在 GitHub Desktop 中选择：

```text
File -> Add local repository
```

选择项目目录：

```text
<你的项目目录>\Taobao
```

### 5.3 创建开发分支

点击顶部的 `Current branch`，然后选择 `New branch`。

分支名称示例：

```text
agent/single-account-checkout
```

创建前确认基础分支是 `main`。

### 5.4 检查修改文件

左侧会显示所有修改、新增和删除的文件。

逐项确认：

- 源代码属于本次功能。
- 测试文件属于本次功能。
- 文档内容正确。
- 没有数据库、虚拟环境、账号资料和私人截图。

对于不准备提交的文件，可以取消左侧复选框。

### 5.5 创建提交

在左下角 `Summary` 填写提交说明，例如：

```text
feat: add single-account scheduled checkout workflow
```

然后点击：

```text
Commit to agent/single-account-checkout
```

### 5.6 推送分支

提交完成后点击顶部：

```text
Push origin
```

第一次推送新分支时，按钮也可能显示：

```text
Publish branch
```

### 5.7 打开 Pull Request

推送完成后点击：

```text
Preview Pull Request
```

确认：

```text
base: main
compare: agent/single-account-checkout
```

然后点击 `Create Pull Request`。GitHub Desktop 会打开浏览器，后续操作在 GitHub 网页完成。

## 6. 在 GitHub 网页创建 Pull Request

如果分支已经推送到 GitHub，可以完全通过网页创建 Pull Request，不需要使用 `gh pr create`。

### 6.1 从仓库首页进入

打开仓库后，GitHub 通常会显示新分支提示：

```text
Compare & pull request
```

点击该按钮。

也可以进入：

```text
Pull requests -> New pull request
```

### 6.2 选择分支

设置：

```text
base: main
compare: agent/single-account-checkout
```

`base` 是准备接收代码的目标分支，`compare` 是包含新修改的来源分支。

### 6.3 填写标题和说明

标题示例：

```text
新增单账号定时辅助购买流程
```

说明示例：

```text
完成单账号商品预检、单次授权、毫秒级定时任务、确认订单与待付款页面识别、Chrome 会话重连、运行日志及本地 Chrome 回归测试。

程序到达待付款或官方收银台页面后停止，不执行自动支付。
```

### 6.4 创建草稿或正式 PR

尚需自己继续检查时，选择：

```text
Create draft pull request
```

已经准备好让朋友检查时，选择：

```text
Create pull request
```

官方说明：

- [Creating a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request)

## 7. 检查 Pull Request

Pull Request 页面主要包含以下标签：

### 7.1 Conversation

用于查看说明、评论、审查状态、自动测试结果和是否存在冲突。

### 7.2 Commits

用于查看该分支包含的提交记录。

### 7.3 Checks

用于查看 GitHub Actions 自动测试。

常见状态：

```text
All checks have passed
```

表示自动测试通过。

如果显示测试失败，不要直接合并，应先进入失败项目查看日志并修复。

### 7.4 Files changed

用于逐行查看新增、修改和删除的代码。

合并前至少确认：

- 修改文件数量合理。
- 没有上传敏感信息。
- 没有意外删除重要文件。
- 文档与代码改动一致。
- `.gitignore` 仍然排除本地运行数据。

## 8. 从草稿转为正式审查

草稿 PR 会显示：

```text
Draft
This pull request is still a work in progress
```

自动测试通过且文件检查完成后，点击：

```text
Ready for review
```

确认后，PR 会从草稿转为可审查状态。

## 9. 邀请朋友审查代码

在 Pull Request 页面右侧找到：

```text
Reviewers
```

点击齿轮或建议用户旁边的 `Request`，选择朋友或仓库负责人。

审查者可以：

- 查看全部代码差异。
- 在具体代码行留下评论。
- 提出修改要求。
- 选择 `Approve` 批准合并。

如果审查者提出修改，不需要关闭 Pull Request。在同一个开发分支继续修改、提交并推送，新提交会自动出现在原 PR 中。

## 10. 在网页合并 Pull Request

确认以下条件后再合并：

- 自动测试通过。
- 没有分支冲突。
- 文件检查完成。
- 必要的代码审查已经批准。

在 PR 页面底部点击：

```text
Merge pull request
```

然后点击：

```text
Confirm merge
```

如果仓库启用了其他合并方式，也可能看到：

- `Squash and merge`：把多个提交压缩为一个提交。
- `Rebase and merge`：把提交依次放到目标分支顶部。

对于本次只有一个提交的功能分支，普通 `Merge pull request` 或 `Squash and merge` 都可以；团队应统一使用仓库约定的方式。

官方说明：

- [Merging a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/merging-a-pull-request)

## 11. 合并后的本地同步

网页合并完成后，在 GitHub Desktop 中：

1. 切换到 `main`。
2. 点击 `Fetch origin`。
3. 点击 `Pull origin`。

如果使用 PowerShell，可以执行：

```powershell
git switch main
git pull origin main
```

确认本地 `main` 已包含最新代码后，可以删除已经合并的本地开发分支：

```powershell
git branch -d agent/single-account-checkout
```

GitHub 网页在 PR 合并后通常会提供：

```text
Delete branch
```

点击后可以删除远程开发分支，不会删除已经合并到 `main` 的代码。

## 12. 后续修改的推荐流程

每次准备开发新功能时，都使用新的分支：

```text
同步 main
-> 创建新分支
-> 修改代码
-> 运行测试
-> 检查敏感文件
-> 创建提交
-> 推送分支
-> 网页创建 Pull Request
-> 自动测试和人工审查
-> 合并到 main
```

不要长期在同一个旧分支里混合多个不相关功能，也不要把所有修改直接推送到 `main`。

## 13. 常见问题

### 13.1 网页上传和 Git 推送有什么区别？

网页上传适合少量文件。Git 或 GitHub Desktop 能准确识别修改、新增和删除，并保留完整目录结构，更适合软件项目。

### 13.2 Pull Request 已创建，是否代表代码已经进入 main？

不是。只有完成 `Merge pull request` 后，代码才正式进入 `main`。

### 13.3 自动测试通过是否代表真实淘宝一定成功？

不是。自动测试只证明项目内部逻辑和本地模拟流程通过。真实淘宝页面可能因页面结构、库存、账号资格、验证或平台风控发生变化。

### 13.4 可以直接提交数据库和 Chrome 登录资料吗？

不可以。数据库、Cookie、Chrome profile、收货地址、订单号和支付信息都属于本地敏感数据，不应上传到 GitHub。

### 13.5 为什么推荐先创建草稿 PR？

草稿 PR 可以提前运行自动测试和查看完整差异，同时明确表示代码尚未准备合并。检查完成后再点击 `Ready for review`。

---

文档适用仓库：`SKTGAN/Taobao`  
推荐协作方式：GitHub Desktop + GitHub 网页  
安全原则：源代码与测试可以提交，本地账号和运行数据不得提交
