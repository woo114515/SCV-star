# SCV-star

面向《星际争霸 II》完整 1v1 人族对人族（TvT）的神经网络 AI 学习与研究项目，参考 DI-star 的架构，从人类录像行为克隆开始，再进行强化学习、自我对战和固定条件评测。

## 当前状态

已完成初步技术调研、许可要求整理、Git／GitHub 管理与基础目录搭建。**M0 已通过用户验收**：验证本轮中国区正式 build 97579、基础动作、双向迷雾和真人操作；用户接受修复后已有的约8分钟运行证据，不再要求补足10分钟。实际时长及中断原因保留在[M0记录](docs/experiments/M0_2026-09-29.md)。

**M1 GPU兼容性已通过实测**：项目内 PyTorch 2.13.0+cu130 在 NVIDIA RTX 5070 Ti Laptop 上完成 FP32/FP16/BF16 合成数据前后向及检查点恢复；未使用Intel核显。详见[M1说明](docs/M1_ACCEPTANCE.md)和[实测记录](docs/experiments/M1_2026-09-30.md)。

**M2整体验收未完成**：项目数据目录现统一保存2,126个已识别TvT副本；清理前全量来源扫描有26,811个可读录像，非TvT及种族未明条目已从当前项目样本清单移除。TvT中1,957个进入解码候选队列，129个91115暂缓，40个版本文本待核对。已有12场／24个视角的重放证据，BC对齐尚未验收，训练就绪文件为0。完整口径见[数据集总览](docs/DATASET_OVERVIEW.md)，来源范围见[公开采集记录](docs/M2_PUBLIC_DATA.md)。Spawning的6,876个目录ID未全部下载；未运行正式BC／RL。

## 基本结构

| 路径 | 用途 |
| --- | --- |
| `src/scv_star/` | 数据、环境、观测、动作、模型、训练、联赛、评测与推理模块 |
| `configs/` | 项目、机器和实验配置模板；真实本机配置不提交 |
| `scripts/` | M0 本机运行入口与脚本约定 |
| `tests/` | 版本、地图、协议异常和观测配置的单元测试；其他能力待实现 |
| `data/` | 原始录像、中间结果、训练分片和清单，仅保留目录占位 |
| `artifacts/` | 检查点、运行记录和评测输出，仅保留目录占位 |
| `docs/` | 架构、开发、调研、许可、里程碑和实验记录 |

完整目录树见[结构说明](docs/PROJECT_STRUCTURE.md)。`pyproject.toml` 声明 Python 3.11+、`m0`/`m1`/`m2` 可选依赖及 `scv-m0`/`scv-m1` 入口；`requirements/` 分别保存阶段依赖和开发工具锁。M1 CUDA依赖按其专用锁安装；M2使用文档中的Python模块入口，尚无正式训练 CLI。

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

## 目标与约束

- 最终支持开发、验收时确认的 SC2 最新正式版；每轮实验固定准确 build、数据、地图及依赖版本。
- 支持内置人族电脑、当前与历史策略自我对战，以及真人实时 TvT。
- 策略使用己方合法观测，保留战争迷雾；额外价值信息与执行策略隔离。
- 从随机初始化网络自行训练；旧录像按对应版本解码，不伪造新版轨迹。
- 项目新增存储占用不超过 100 GB，额外空间须事先征得用户同意。
- 可使用已有 Windows／Ubuntu 双系统；当前已实施M0接口、M1 GPU验证、M2小样本验证及本轮公开数据采集。其他引擎获取和正式训练另行安排。

## 文档

- [文档导航](docs/README.md)：推荐阅读顺序与当前约定。
- [架构与数据契约](docs/ARCHITECTURE.md)：模块职责、观测边界、版本及轨迹。
- [开发说明](docs/DEVELOPMENT.md)：当前可执行检查与后续环境验证。
- [里程碑](docs/MILESTONES.md)：实际状态与验收标准。
- [实验记录](docs/experiments/README.md)：受控实验与结果模板。
- [调研与技术方案](docs/SC2_TVT_RESEARCH_PLAN.md)：源码审计、数据流程、模型、迁移、里程碑及评测。
- [许可要求整理](docs/SC2_LICENSE_REQUIREMENTS.md)：暴雪 AI 许可与 DI-star Apache 2.0 的适用范围和待核查事项。
- [Git 工作流程](docs/GIT_WORKFLOW.md)：本地提交、同步及分支协作。
- [贡献指南](AGENTS.md)：项目初始通用约定；其中空目录描述反映创建该文件时的状态。

## 仓库内容与许可

Git 维护源码、配置模板、文档和必要目录占位；录像、解码缓存、模型权重、游戏客户端／地图、凭据及机器专用配置不提交。`data/` 和 `artifacts/` 只跟踪说明与 `.gitkeep`，实际文件继续忽略。具体规则见 [.gitignore](.gitignore)。

当前未为整个项目指定开源许可证，也未导入 DI-star 源码。引用上游项目不代表游戏资源、数据和权重自动取得 Apache 2.0 授权；未来复用代码时应保留其许可证和适用声明。
