# Git 工作流程

主分支为 `main`，GitHub 仓库按用户选择使用私有可见性。Git 只管理项目源码、配置模板和文档；当前尚未开始训练实现。

## 日常提交与同步

在仓库根目录运行：

```powershell
git status
git diff
git add README.md docs/
git diff --cached
git commit -m "Update TvT research plan"
git push
```

按实际改动显式选择 `git add` 的路径。提交信息使用简洁祈使句，每次提交集中处理一个主题。只有成功 push 后，提交才已同步到 GitHub。

在另一套系统继续工作前，先确认没有未提交修改，再执行：

```powershell
git pull --ff-only
```

如果出现分叉或冲突，先检查历史并解决，不使用强制推送覆盖已有提交。两个系统的未提交修改不会通过 GitHub 自动同步。

## 开发分支

有明确开发任务时创建分支，例如：

```powershell
git switch -c feat/replay-metadata
```

完成并验证后提交，首次推送分支：

```powershell
git push -u origin feat/replay-metadata
```

需要审阅时再创建 PR，描述问题、改动、验证及尚未解决的限制。建立版本管理不代表已授权部署、训练或发布模型。

## 提交边界

- 不提交账号令牌、密码、真实 `.env`、游戏资源、原始录像或训练权重。
- `.gitignore` 只阻止未跟踪文件被默认加入，不能移除已提交文件或历史中的敏感内容。
- 数据和模型使用带哈希、版本及来源的清单关联到代码提交，实体文件保存在 Git 之外，并遵守 100 GB 项目存储上限。
- 当前不安装或启用 Git LFS；有明确的大文件版本管理需求后再决定。
- 提交身份仅配置于本仓库，使用 GitHub noreply 邮箱；不修改全局 Git 身份。
- 私有仓库仍不应存储凭据，且不会改变第三方代码、游戏或数据的许可要求。

## 检查同步状态

```powershell
git remote -v
git fetch origin
git status -sb
git log --oneline -5
```

没有 upstream 或 push 失败时，不视为上传完成；先修复远程地址或认证，再确认本地 `main` 与 `origin/main` 指向同一提交。
