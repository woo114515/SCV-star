# 开发说明

## 当前可用范围

当前提供M0 SC2启动与通信、基础动作/迷雾/真人检查，M1 GPU合成数据前后向和恢复检查，以及[M2录像元数据扫描和小样本重放](M2_REPLAY_SOURCES.md)。正式训练轨迹导出器、策略模型、BC／RL和联赛仍为占位；不提供尚不存在的`train`／`play`命令。

M0/M1 使用项目内 `.venv/Scripts/python.exe`（Python 3.11.16），通信、GPU 和开发工具依赖分别保存哈希锁。未修改系统 PATH；未找到可复用 pyenv，用户表示可能不存在。M1 已安装 PyTorch 2.13.0+cu130 并在 NVIDIA GPU 上通过小型验证，未运行正式训练。详见 [M0 部署与命令](M0_ACCEPTANCE.md)和[M1 验证命令](M1_ACCEPTANCE.md)。

## 环境验证顺序

1. 获得相应实施授权后，确认 Windows 游戏路径、最新正式版准确 build、数据版本和所需地图。
2. 检查现有解释器、驱动及 Ubuntu 环境；优先复用可用环境，按 100 GB 上限规划新增占用。
3. 为项目准备隔离依赖，验证 PyTorch 前后向及 SC2 通信，再固定精确版本和平台锁文件。
4. 填写本机配置和经过验证的版本清单，最后实现 BC／RL 入口。

`pyproject.toml` 声明 `src/scv_star` 包、Python 3.11+、setuptools 84.0.0 和 `m0`/`m1` 可选依赖。顶层空依赖列表不代表检查入口不需要第三方库；安装时使用相应固定依赖清单。M1 的 CUDA wheel 需要专用索引，命令见其部署说明；完整训练依赖仍需后续验证。

M0 依赖已经固定；其他阶段正式实验前仍需单独固定环境。不声明本项目为 Apache 2.0，不自动继承 DI-star 许可，也不设置 PyPI 发布流程。元数据的 `Private :: Do Not Upload` 是发布防护标记，不替代访问控制。

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

这只检查语法，不能证明字段完整、版本正确或训练可运行。单元测试命令为 `.\.venv\Scripts\python.exe -m unittest discover -s tests/unit -v`；真实游戏命令及副作用见 [M0 说明](M0_ACCEPTANCE.md)。

格式化与静态检查使用 Ruff 0.16.9，规则固定在 `pyproject.toml`；开发工具的哈希锁与通信依赖分开维护：

```powershell
.\downloads\uv-0.12.20\uv.exe pip install --python .venv/Scripts/python.exe --require-hashes -r requirements/dev-windows-py311.txt
.\.venv\Scripts\ruff.exe format --check src tests
.\.venv\Scripts\ruff.exe check src tests
```

需要格式化时运行 `.\.venv\Scripts\ruff.exe format src tests`。Python 使用 100 字符目标行宽；检查范围和配置参考 [Ruff 官方配置说明](https://docs.astral.sh/ruff/configuration/)。

## 代码与文档约定

- Python 使用四空格缩进，JSON 两空格；文件采用 UTF-8，文本默认 LF，见 `.editorconfig` 和 `.gitattributes`。
- 函数、变量和模块使用 `snake_case`，类使用 `PascalCase`；跨模块接口添加类型标注和字段单位说明。
- 时间区分 wall-clock seconds、game loops 和决策步；存储统计采用字节，不混用 GB 和 GiB。
- 随机种子显式传入；不在 import 时启动进程、访问网络或创建运行目录。
- 测试使用标准库 unittest；格式化与静态检查使用固定版本 Ruff。测试通过不替代实际客户端验收。
- 修改接口、配置或输出格式时同步更新对应文档和 schema；记录真实测试结果，不把未运行写成通过。

## 临时文件管理

存储例外（用户于 2026-10-07 授权）：本阶段缺失引擎获取及配套环境验证允许项目占用最高 200 GB；阶段结束必须归并临时副本并恢复到 100 GB 以内。记录开始、过程测量和结束占用；保留系统磁盘余量，不能将阶段性授权视为永久提高预算。归并前核对哈希、依赖及可恢复性，归并后复测受影响环境。

按用户 2026-10-07 的要求，一次性诊断、人工验收启动文件和临时处理脚本统一放入项目 `tmp/<日期>-<任务>/`，不再散落根目录或 `artifacts/`。`tmp/` 已被 Git 忽略；临时文件同样计入 100 GB 预算。可重复使用的正式工具仍放入 `scripts/` 或 `src/`。

每次任务收尾检查本任务临时文件，每个阶段收尾集中清理已完成任务的临时目录；这是工作流程约定，不是已安装的后台定时任务。删除前核对绝对路径位于项目 `tmp/` 内，确认没有运行进程或文档、脚本引用，并先保留必要的脱敏结论和验收证据。原始录像、下载的引擎、地图、模型及 `artifacts/runs/` 的验收记录不作为临时文件自动删除。历史散落文件先核对依赖再迁移或清理。

## Windows／Ubuntu 与 Git 工作流

各系统维护自己的本机配置和依赖环境。双系统通常分时使用，跨系统交接缓存需验证版本和 schema；不要把 Windows 绝对路径带到 Ubuntu。

切换系统或设备前提交并同步需要保留的源码与文档；数据和权重不由 Git 同步。运行产物的跨系统副本仍计入项目存储预算。详细操作见 [Git 工作流程](GIT_WORKFLOW.md)。

## 变更完成条件

检查改动范围、文档链接与配置语法；若影响真实行为，执行相应测试并说明未满足的环境条件。确认没有凭据、录像、模型或游戏资源被跟踪，再提交和同步私有仓库。

用户已授权并完成M0接口、M1 GPU验证及M2三来源小样本验证。M2解析依赖固定在`requirements/m2-replays.txt`，原生重放还需要M0依赖；不需要GPU。后续精确对齐、扩大数据与正式训练仍按[里程碑](MILESTONES.md)分阶段开展。
