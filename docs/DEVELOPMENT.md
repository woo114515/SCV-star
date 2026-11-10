# 开发说明

## 当前可用范围

当前提供 M0 SC2 通信与真人检查、M1 GPU 与恢复检查、M2 元数据扫描、固定版本完整回放、同遍观测—动作提取及训练分片验收。Ubuntu 24.04 离线训练环境已于 2026-11-08 通过 GPU、单元测试和集合读取检查；M4 v1 的 BC 缓存、模型、APM 加权采样与可恢复训练器已实现，2 小时训练与最终测试已完成（结果按欠拟合记录）；诊断轮用表示探针定位了三处实现缺陷，v2 修复后训练中。RL 学习器、联赛和在线执行仍为占位，不提供尚不存在的在线 `play` 命令。数据入口和命令见[对齐与训练数据](REPLAY_ALIGNMENT.md)。

Windows 与 Ubuntu 分别使用项目内独立环境：Windows 为 `.venv/Scripts/python.exe`（Python 3.11.16），Ubuntu 为 `.venv/bin/python`（uv 管理的 Python 3.11.16）；通信、GPU 和开发工具依赖各自保存哈希锁，不跨系统复用。M1 已在 Windows 上通过 PyTorch 2.13.0+cu130 的小型验证；Ubuntu 2026-11-08 复跑了同类 GPU 探针并通过，详见 [M1 验证命令](M1_ACCEPTANCE.md)、[M0 部署与命令](M0_ACCEPTANCE.md)和[Ubuntu 环境验收](experiments/M1_LINUX_2026-11-08.md)。未运行正式 BC／RL 训练。

## Ubuntu 离线训练环境（2026-11-08）

- 系统：Ubuntu 24.04.5 LTS、kernel 6.8.0-137、24 线程、30 GiB RAM；NVIDIA RTX 5070 Ti Laptop 12,227 MiB、驱动 595.99.02。
- 解释器与依赖：uv 0.12.20 管理的 Python 3.11.16、`.venv`、torch 2.13.0+cu130、CUDA runtime 13.0；锁文件为 `requirements/bc-linux-py311-cu130.txt`（m0+m1+m2+dev 组合，仅 linux 平台）。
- 数据：本机 `SCV-star/dataset`（仓库父目录，不提交）链接到 `data/processed/`，入口为 `data/manifests/training-current.json`；集合 SHA-256 `373cfc6f…97da`，1,000 场／2,467,676 行。
- 验收：110 项单元测试 109 通过、1 项 Windows 专用跳过；Ruff 格式与静态检查通过；M1 GPU 探针 FP32／FP16／BF16 前后向及恢复逐位一致；`bc_reader_check` 覆盖 11 个 build、15 个案例、最大 531 实体，优化器更新为 0。
- 边界：M4 v1 已实现；小样本过拟合、2 小时训练和最终测试待续。M0／M3／M5 的游戏接口仍使用 Windows 客户端，Linux 不启动 SC2。证据见[Ubuntu 环境验收](experiments/M1_LINUX_2026-11-08.md)。

## 环境验证顺序

1. 获得相应实施授权后，确认 Windows 游戏路径、最新正式版准确 build、数据版本和所需地图。
2. Ubuntu 环境已于 2026-11-08 验收；Windows 侧继续检查解释器、驱动和游戏路径。新增占用按常规 100 GB 与 M2 轮 150 GB 授权的历史边界规划，不自动扩大预算。
3. 为项目准备隔离依赖，验证 PyTorch 前后向及 SC2 通信，再固定精确版本和平台锁文件。Ubuntu 的组合 Linux 锁已生成；Windows 继续使用各自阶段锁。
4. 填写本机配置和经过验证的版本清单。M4 BC 入口已实现；RL 入口待实现。

`pyproject.toml` 声明 `src/scv_star` 包、Python 3.11+、setuptools 84.0.0 和 `m0`/`m1` 可选依赖。顶层空依赖列表不代表检查入口不需要第三方库；安装时使用相应固定依赖清单。M1 的 CUDA wheel 需要专用索引，命令见其部署说明；Ubuntu 侧使用 `requirements/bc-linux-py311-cu130.txt`，完整 M4 训练依赖仍需在实施时确认。

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

Ubuntu 24.04 使用独立环境执行同类检查：

```bash
.venv/bin/python -m unittest discover -s tests/unit -v
.venv/bin/ruff format --check src tests
.venv/bin/ruff check src tests
.venv/bin/python -m scv_star.runtime.m1 --output artifacts/runs/<新目录>
.venv/bin/python -m scv_star.runtime.bc_reader_check --collection data/processed/m2-expand-2026-10-19/collection.json --output artifacts/runs/<新目录>/gpu-check.json
```

Linux 结果与 Windows 分开记录；当前 151 项中 1 项 Windows 专用测试在 Linux 按设计跳过。详见 [Ubuntu 环境验收](experiments/M1_LINUX_2026-11-08.md)。

M4 BC v1 入口（缓存已生成；`<新目录>` 必须不存在）：

```bash
.venv/bin/python -m scv_star.data.bc_cache --collection data/processed/m2-expand-2026-10-19/collection.json --build 86383 --output data/interim/bc-86383-v1 --splits train validation \
  --apm-manifest data/manifests/quality-current.json
.venv/bin/python -m scv_star.training.bc --config configs/experiments/bc-86383-v1.json --output artifacts/runs/<新目录> --max-updates 2 --device cpu
```

小样本过拟合、断点恢复、2 小时训练和最终测试的命令与判据见 [M4 BC v1 记录](experiments/20261108_M4_bc86383_seed20261108.md)。

## 代码与文档约定

- Python 使用四空格缩进，JSON 两空格；文件采用 UTF-8，文本默认 LF，见 `.editorconfig` 和 `.gitattributes`。
- 函数、变量和模块使用 `snake_case`，类使用 `PascalCase`；跨模块接口添加类型标注和字段单位说明。
- 时间区分 wall-clock seconds、game loops 和决策步；存储统计采用字节，不混用 GB 和 GiB。
- 随机种子显式传入；不在 import 时启动进程、访问网络或创建运行目录。
- 测试使用标准库 unittest；格式化与静态检查使用固定版本 Ruff。测试通过不替代实际客户端验收。
- 修改接口、配置或输出格式时同步更新对应文档和 schema；记录真实测试结果，不把未运行写成通过。

## 临时文件管理

2026-10-19 数据扩容授权：本轮处理至至少1,000场训练可用TvT，项目上限为 **150 GB**，保留达到目标的数据；无用文件先复核用途、哈希与保留副本再删除。此授权覆盖相应引擎、地图、小样本验证及批量提取，不包含正式模型训练。此前100 GB常规预算及已结束的200 GB例外保留为历史；本轮结束报告实际占用与清理结果。

存储例外（用户于 2026-10-07 授权）：本阶段缺失引擎获取及配套环境验证允许项目占用最高 200 GB；阶段结束必须归并临时副本并恢复到 100 GB 以内。记录开始、过程测量和结束占用；保留系统磁盘余量，不能将阶段性授权视为永久提高预算。归并前核对哈希、依赖及可恢复性，归并后复测受影响环境。

按用户 2026-10-07 的要求，一次性诊断、人工验收启动文件和临时处理脚本统一放入项目 `tmp/<日期>-<任务>/`，不再散落根目录或 `artifacts/`。`tmp/` 已被 Git 忽略；临时文件同样计入 100 GB 预算。可重复使用的正式工具仍放入 `scripts/` 或 `src/`。

每次任务收尾检查本任务临时文件，每个阶段收尾集中清理已完成任务的临时目录；这是工作流程约定，不是已安装的后台定时任务。删除前核对绝对路径位于项目 `tmp/` 内，确认没有运行进程或文档、脚本引用，并先保留必要的脱敏结论和验收证据。原始录像、下载的引擎、地图、模型及 `artifacts/runs/` 的验收记录不作为临时文件自动删除。历史散落文件先核对依赖再迁移或清理。

## Windows／Ubuntu 与 Git 工作流

各系统维护自己的本机配置和依赖环境。双系统通常分时使用，跨系统交接缓存需验证版本和 schema；不要把 Windows 绝对路径带到 Ubuntu。

切换系统或设备前提交并同步需要保留的源码与文档；数据和权重不由 Git 同步。运行产物的跨系统副本仍计入项目存储预算。详细操作见 [Git 工作流程](GIT_WORKFLOW.md)。

## 变更完成条件

检查改动范围、文档链接与配置语法；若影响真实行为，执行相应测试并说明未满足的环境条件。确认没有凭据、录像、模型或游戏资源被跟踪，再提交和同步私有仓库。

用户已明确 2026-10-21 的 M2 轮只完成数据验收，不实现 BC 网络或训练器；2026-11-08 的 Ubuntu 环境轮按平台授权安装了固定 Linux 锁并完成环境验收；M4 v1 实现已按批准的计划完成，但正式 BC 训练／测试与 RL／在线执行尚未授权或运行。M2 解析依赖固定在 `requirements/m2-replays.txt`，原生重放还需要 M0 依赖；训练读取与 GPU 检查分别使用 M1 PyTorch 锁和 Linux 组合锁。后续模型和在线 TvT 执行按[里程碑](MILESTONES.md)开展。

## 项目管理与会话交接

采用阶段验收、实验登记和版本冻结管理项目；以下是工作约定，不表示任何阶段已经通过。

### 文档分工与任务范围

- [里程碑](MILESTONES.md)统一记录阶段状态、验收标准及证据；[对齐与训练数据](REPLAY_ALIGNMENT.md)维护数据流程和命令；本页维护开发与交接规则。更新现有章节，避免为每次操作新增文档或重复维护状态。
- 每个任务写明目标、输入、输出、验收方法与时间／存储上限，优先推进当前阶段的一个主要目标。技术决策记录日期、理由、影响及尚未验证的假设。
- 脚本已写、进程运行中、程序正常退出均不等于验收通过。M2需有可读取的训练分片、观测与动作对齐证据、固定数据划分和GPU读取检查；BC需验证学习效果及断点恢复；在线策略需验证有效动作和完整对局；RL先验证采样、更新、保存与恢复闭环，再扩大规模。具体标准以里程碑为准。

### 数据与实验记录

- 区分原始录像、筛选候选和冻结训练数据。每轮实验引用清单及哈希，记录来源、筛选规则和划分；同场比赛的不同视角或重复副本不得跨训练、验证、测试集。保留副本不等于允许划分泄漏。
- 为实验分配唯一编号，固定代码提交（含未提交差异说明）、数据快照、游戏build／DataVersion、地图、依赖、随机种子、配置及预算。离线训练记录数据提取时的引擎身份；无须把游戏引擎当作离线训练的运行依赖。
- 受控比较每次只改变一个主要因素，明确采用相同样本量还是相同训练时长；记录验证指标、实际耗时、资源峰值和失败结论。
- 长批次逐项记录待处理、运行中、成功、失败和隔离状态，保存错误与重试次数；重启时核对产物完整性，只重做缺失或失败项。报告吞吐、失败率和剩余量，不能只凭启动计数宣称完成。

### 系统、存储与交接

- Windows与Ubuntu分别锁定环境，通过显式的数据schema、清单哈希和模型格式交接。切换双系统前保存检查点、运行命令和恢复位置，并确认没有仍需运行的任务。
- Windows 常规预算仍为 100 GB；M2 扩容轮 150 GB 授权与已结束的 200 GB 例外保留历史口径。2026-11-08 实测 Ubuntu 根分区可用约 1.4 TB；Ubuntu 环境与链接的本机数据约 9.9 GB。Linux 容量不能自动扩大 Windows 预算或迁移数据；跨系统副本仍计入项目存储，实际迁移与清理须单独确认。
- Git保存源码、配置模板及脱敏文档；录像、游戏资源、环境、模型权重和含受限来源信息的清单按现有忽略与许可规则管理，见[Git工作流程](GIT_WORKFLOW.md)。
- 每次任务收尾交代：完成项与证据、检查结果、剩余问题及影响、产物位置与资源占用、下一项最小验证任务。清理按本页临时文件管理规则执行。
- 聊天用于讨论，确认的决策和验收结果归入对应文档。新会话先读取里程碑及相关阶段文档，不假定自动取得其他会话的完整历史；交接摘要写明当前目标、约束、已验证结论、运行任务和下一步。并行会话应明确文件修改范围，避免干扰正在运行的任务。
- 分享聊天前检查个人路径、凭据和受限数据链接；项目交接优先分享脱敏摘要及文档，不以聊天共享代替数据使用许可。
