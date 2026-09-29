# 开发说明

## 当前可用范围

当前提供目录骨架、Python 包占位、配置模板和开发文档。没有 SC2 启动器、配置加载器、数据处理器、模型、训练 CLI 或业务测试；不提供尚不存在的 `train`／`play` 命令。

本轮没有安装 Python、依赖或游戏，没有创建虚拟环境或依赖锁，没有运行训练。本地 PATH 发现的 Python 命令为 WindowsApps 别名，尚未确认可用解释器；不要把它当作已就绪的 Python 环境。

## 环境验证顺序

1. 获得相应实施授权后，确认 Windows 游戏路径、最新正式版准确 build、数据版本和所需地图。
2. 检查现有解释器、驱动及 Ubuntu 环境；优先复用可用环境，按 100 GB 上限规划新增占用。
3. 为项目准备隔离依赖，验证 PyTorch 前后向及 SC2 通信，再固定精确版本和平台锁文件。
4. 填写本机配置和经过验证的版本清单，最后实现 BC／RL 入口。

`pyproject.toml` 声明 `src/scv_star` 包、Python 3.10 及以上的骨架目标和 setuptools 构建后端；空运行依赖列表仅表示当前占位包不依赖第三方库。它不是“完整项目无依赖”，也不证明所有 Python 新版本兼容未来训练代码。

构建后端版本范围尚未锁定，正式实验前仍需固定环境。不声明本项目为 Apache 2.0，不自动继承 DI-star 许可，也不设置 PyPI 发布流程。元数据的 `Private :: Do Not Upload` 是发布防护标记，不替代访问控制。

包元数据组织参考 [PyPA 的 pyproject.toml 指南](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)。

## 目前可执行的检查

下面的 Git 命令不会安装软件、启动游戏或训练：

```powershell
git status -sb
git diff --check
git ls-files
```

Windows PowerShell 可只读检查配置模板是否为有效 JSON：

```powershell
Get-ChildItem configs -Recurse -Filter '*.example.json' | ForEach-Object {
    Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null
    Write-Output $_.FullName
}
```

这只检查语法，不能证明字段完整、版本正确或训练可运行。业务代码和测试实现后，再补充经过实际验证的运行命令。

## 代码与文档约定

- Python 使用四空格缩进，JSON 两空格；文件采用 UTF-8，文本默认 LF，见 `.editorconfig` 和 `.gitattributes`。
- 函数、变量和模块使用 `snake_case`，类使用 `PascalCase`；跨模块接口添加类型标注和字段单位说明。
- 时间区分 wall-clock seconds、game loops 和决策步；存储统计采用字节，不混用 GB 和 GiB。
- 随机种子显式传入；不在 import 时启动进程、访问网络或创建运行目录。
- 格式化器、lint 工具和测试框架尚未安装或锁定；不能把编辑器约定写成已通过的自动检查。
- 修改接口、配置或输出格式时同步更新对应文档和 schema；记录真实测试结果，不把未运行写成通过。

## Windows／Ubuntu 与 Git

各系统维护自己的本机配置和依赖环境。双系统通常分时使用，跨系统交接缓存需验证版本和 schema；不要把 Windows 绝对路径带到 Ubuntu。

切换系统或设备前提交并同步需要保留的源码与文档；数据和权重不由 Git 同步。运行产物的跨系统副本仍计入项目存储预算。详细操作见 [Git 工作流程](GIT_WORKFLOW.md)。

## 变更完成条件

检查改动范围、文档链接与配置语法；若影响真实行为，执行相应测试并说明未满足的环境条件。确认没有凭据、录像、模型或游戏资源被跟踪，再提交和同步私有仓库。

目录搭建的完成不等于环境部署授权。下一阶段先按[里程碑](MILESTONES.md)验证最新版接口、GPU 和 TvT 解码。
