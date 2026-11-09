# SCV-star

面向《星际争霸 II》完整 1v1 人族对人族（TvT）的神经网络 AI 学习项目：参考 DI-star，从人类录像行为克隆开始，再进行强化学习、自我对战及固定条件评测。

## 当前状态

**M2 已扩容至 1,000 场并通过验收，离线 BC 数据入口冻结。** 1,000 个录像文件／2,000 个玩家视角，训练／验证／测试为 824／87／89 场，共 2,467,676 条观测—动作样本；覆盖 11 个历史 build 和 105 个地图名称，原 100 场包含在总数内并保持冻结。完整回放、同遍时间配对、动作逆转换、逐条审计及 NVIDIA GPU 读取／前后向检查通过；尚未进行正式 BC／RL 更新。

- [M2 数据契约与验收](docs/REPLAY_ALIGNMENT.md)：实现、命令、覆盖范围和限制。
- [数据总览](docs/DATASET_OVERVIEW.md)：3,077 个原始文件、835 个轻筛合格候选；1,000 场是其中已验收子集。
- [最终验收摘要](docs/experiments/M2_READY_2026-10-21.json)：实际样本量、配对率、依赖、GPU 和存储。
- [Ubuntu 环境验收](docs/experiments/M1_LINUX_2026-11-08.md)：Linux GPU／PyTorch、环境验收与集合读取。
- [M4 BC v1](docs/experiments/20261108_M4_bc86383_seed20261108.md)：86383 首次 BC 的缓存、模型、掩码损失、训练与恢复记录。

M0 历史验收包含国服 97579、战争迷雾和真人操作，见[M0记录](docs/experiments/M0_2026-09-29.md)。10月8日旧97579启动回归失败；10月12日用户重新安装国服，本机清单为5.0.17.98370，新安装的API尚未验收；最终新版RL和真人部署必须重新验收。旧版BC数据就绪不替代这一要求。

M1 使用 Python 3.11.16、PyTorch 2.13.0+cu130 与 **NVIDIA RTX 5070 Ti Laptop**；Windows 环境已通过，Ubuntu 24.04 离线环境于 2026-11-08 通过同类 GPU 探针；当前分支 146 项单元测试（145 通过／1 项 Windows 专用跳过）及 1,000 场集合读取。Windows 侧项目约 102.40 GB（M2 扩容轮 150 GB 授权内，未声称回到 100 GB）；Ubuntu 侧环境约 4.8 GB，另链接本机已冻结数据 4.6 GB。原始录像与派生数据仅留本机；GitHub 不保存录像、玩家详情或逐场清单。

M4 v1 的数据缓存、模型、可恢复训练器、APM 加权采样和 146 项单元测试已实现；小样本过拟合、2 小时训练及最终测试待续。M3 在线动作合法性及闭环执行仍需在 Windows 补齐。当前 GPU 检查只验证数据可读、探针前后向和恢复链路，不代表策略已训练或达到任何段位。

## 基本结构

| 路径 | 用途 |
| --- | --- |
| `src/scv_star/` | 数据、环境、观测、动作、模型、训练、联赛、评测与推理模块 |
| `configs/` | 项目、机器和实验配置模板；真实本机配置不提交 |
| `scripts/` | M0 本机运行入口与脚本约定 |
| `tests/` | 版本、地图、协议、M2 数据、M4 BC 缓存／模型／训练器与客户端守护的单元测试 |
| `data/` | 原始录像、中间结果、训练分片和清单，仅保留目录占位 |
| `artifacts/` | 检查点、运行记录和评测输出，仅保留目录占位 |
| `docs/` | 架构、开发、调研、许可、里程碑和实验记录 |

完整目录树见[结构说明](docs/PROJECT_STRUCTURE.md)。`pyproject.toml` 声明 Python 3.11+、`m0`/`m1`/`m2` 可选依赖及 `scv-m0`/`scv-m1` 入口；`requirements/` 分别保存阶段依赖和开发工具锁。Ubuntu 侧新增 `requirements/bc-linux-py311-cu130.txt`（m0+m1+m2+dev 组合锁）；Windows 侧继续使用各自阶段锁。M2 尚无正式训练 CLI；M4 提供 `scv-bc`／`scv-bc-cache`／`scv-apm`。

## 本地命令

本轮使用项目内 Python 3.11.16。首次安装固定依赖与项目的步骤见 [M0 部署说明](docs/M0_ACCEPTANCE.md)，Ruff 0.16.9 的安装见[开发说明](docs/DEVELOPMENT.md)。已有环境中执行：

```powershell
.\.venv\Scripts\scv-m0.exe --help
# 安装 M1 专用锁后执行小型 GPU 验证；不启动游戏
.\.venv\Scripts\scv-m1.exe --output artifacts/runs/m1-gpu-next
.\.venv\Scripts\python.exe -m unittest discover -s tests/unit -v
.\.venv\Scripts\ruff.exe format --check src tests
.\.venv\Scripts\ruff.exe check src tests
# 构建本地 Python wheel（M0 运行仍使用仓库内的 editable 安装）
.\downloads\uv-0.12.20\uv.exe build --wheel --no-build-isolation --python .venv/Scripts/python.exe --out-dir artifacts/builds
# 启动真实游戏，要求本机配置和固定地图；每次使用新的 RunId
.\scripts\run_m0.ps1 -Mode smoke -RunId m0-smoke-next
```

Ubuntu 24.04 离线环境（已验收，仅环境与读取检查；不启动游戏）：

```bash
.venv/bin/python -m unittest discover -s tests/unit -v
.venv/bin/ruff format --check src tests
.venv/bin/ruff check src tests
.venv/bin/python -m scv_star.runtime.m1 --output artifacts/runs/<新目录>
.venv/bin/python -m scv_star.runtime.bc_reader_check --collection data/processed/m2-expand-2026-10-19/collection.json --output artifacts/runs/<新目录>/gpu-check.json
```

数据保存在本机 `SCV-star/dataset` 并链接到 `data/processed/`，不提交 Git；固定步骤与结果见 [Ubuntu 环境验收](docs/experiments/M1_LINUX_2026-11-08.md)。

M4 BC v1（缓存已生成；`<新目录>` 必须不存在）：

```bash
.venv/bin/python -m scv_star.data.bc_cache --collection data/processed/m2-expand-2026-10-19/collection.json --build 86383 --output data/interim/bc-86383-v1 --splits train validation \
  --apm-manifest data/manifests/quality-current.json
.venv/bin/python -m scv_star.training.bc --config configs/experiments/bc-86383-v1.json --output artifacts/runs/<新目录> --device cuda
```

完整设计、过拟合／恢复和最终测试命令见 [M4 BC v1](docs/experiments/20261108_M4_bc86383_seed20261108.md)。

## 目标与约束

- 最终支持开发、验收时确认的 SC2 最新正式版；每轮实验固定准确 build、数据、地图及依赖版本。
- 支持内置人族电脑、当前与历史策略自我对战，以及真人实时 TvT。
- 策略使用己方合法观测，保留战争迷雾；额外价值信息与执行策略隔离。
- 从随机初始化网络自行训练；旧录像按对应版本解码，不伪造新版轨迹。
- 常规项目存储预算为 100 GB；2026-10-19 M2 扩容轮按 150 GB 授权执行，最终 Windows 侧约 102.40 GB，未声称已恢复到 100 GB。Ubuntu 侧本轮环境与数据约 9.9 GB；新增空间仍须事先征得用户同意。
- 可使用已有 Windows／Ubuntu 双系统；当前已实施 M0 接口、M1 GPU 验证（Windows 与 Ubuntu）、M2 全部数据验收及 Ubuntu 离线训练环境验收。M3／M4 正式实施及训练另行授权。

## 文档

- [文档导航](docs/README.md)：推荐阅读顺序与当前约定。
- [架构与数据契约](docs/ARCHITECTURE.md)：模块职责、观测边界、版本及轨迹。
- [开发说明](docs/DEVELOPMENT.md)：当前可执行检查与后续环境验证。
- [里程碑](docs/MILESTONES.md)：实际状态与验收标准。
- [实验记录](docs/experiments/README.md)：受控实验与结果模板。
- [Ubuntu 环境验收](docs/experiments/M1_LINUX_2026-11-08.md)：Linux GPU／PyTorch、环境验收与集合读取记录。
- [M4 BC v1](docs/experiments/20261108_M4_bc86383_seed20261108.md)：86383 首次 BC 的缓存、模型、掩码损失与训练恢复。
- [调研与技术方案](docs/SC2_TVT_RESEARCH_PLAN.md)：源码审计、数据流程、模型、迁移、里程碑及评测。
- [许可要求整理](docs/SC2_LICENSE_REQUIREMENTS.md)：暴雪 AI 许可与 DI-star Apache 2.0 的适用范围和待核查事项。
- [Git 工作流程](docs/GIT_WORKFLOW.md)：本地提交、同步及分支协作。
- [贡献指南](AGENTS.md)：项目初始通用约定；其中空目录描述反映创建该文件时的状态。

## 仓库内容与许可

Git 维护源码、配置模板、文档和必要目录占位；录像、解码缓存、模型权重、游戏客户端／地图、凭据及机器专用配置不提交。`data/` 和 `artifacts/` 只跟踪说明与 `.gitkeep`，实际文件继续忽略。具体规则见 [.gitignore](.gitignore)。

当前未为整个项目指定开源许可证，也未导入 DI-star 源码。引用上游项目不代表游戏资源、数据和权重自动取得 Apache 2.0 授权；未来复用代码时应保留其许可证和适用声明。
