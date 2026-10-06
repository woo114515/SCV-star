# 数据工作区

| 目录 | 内容 | 保留原则 |
| --- | --- | --- |
| `raw/` | 合法来源的原始录像 | 获取后保留原始字节和来源，避免覆盖 |
| `interim/` | 元数据提取与解码中间结果 | 可重建；清理须遵守约定，不能误删原始录像 |
| `processed/` | 合法玩家观测与动作分片 | 带版本、玩家、比赛组及 schema |
| `manifests/` | 原始清单、去重、集合划分、哈希和失败记录 | 同一文件视角与切片保持同集合；比赛级去重按用户要求跳过 |

这些目录由 `.gitkeep` 保留，Git 忽略所有实际数据文件。`manifests/` 也可能包含玩家标识和绝对路径，因此不默认提交；需要版本化的脱敏摘要放到 `docs/experiments/`。

Kevin 邮件提供的 Spawning Tool 导出另受三项要求约束：注明来源、回传最终报告或其他成果、不再分发数据，其他获取请求转交 Kevin。原始 TAR、逐场 CSV 和筛选后的录像均不上传 GitHub，包括私有仓库；本地来源记录为 `manifests/spawningtool-kevin-2026-10-05.json`。详见[许可记录](../docs/SC2_LICENSE_REQUIREMENTS.md#8-spawning-toolkevin-提供的录像导出)。2026-10-06 已下载并核对：保留1,720个候选，934个复用现有文件，786个新增文件位于 `raw/replays/spawningtool-kevin-tvt/`。独立清单位于 `manifests/spawningtool-kevin-2026-10-06/`，已合入当前主清单，原来源快照保留；详见[获取记录](../docs/M2_SPAWNING_TOOL_EXPORT.md)。

最新环境可用性矩阵为 `manifests/engine-availability-2026-10-07.json`，区分EXE缺失、已有但未通过运行、已通过样本重放。

最新引擎验证及逐文件增量证据位于 `manifests/engine-audit-2026-10-06/`，按SHA-256关联当前清单；历史快照保持不变。完整引擎需求见[验证记录](../docs/M2_ENGINE_AUDIT.md)。

原始录像不是完整逐帧观测，需由匹配引擎重放；不同版本失败不能静默改用新版。详细流程见[技术方案](../docs/SC2_TVT_RESEARCH_PLAN.md)和[架构与数据契约](../docs/ARCHITECTURE.md)。

当前统一入口为 `manifests/current.json`，指向 `unified-tvt-2026-10-06-002/`。清单含3,077个TvT候选文件：解码候选2,832个、91115暂缓182个、版本身份待核对63个。入口schema为2，以每条记录的 `local_replay`（相对项目根目录）定位录像；`replay_directories` 列出三个存储目录。934个重复文件保留双方来源记录及各自使用条件。旧全种族派生清单已删除；完整来源扫描索引作为审计资料保留，不能作为当前训练清单。训练就绪数量为0，详情见[数据集总览](../docs/DATASET_OVERVIEW.md)。

新增黄金录像包保留165个TvT文件，存于 `raw/replays/local-gold-2026-10-06/`；来源清单位于 `manifests/local-gold-2026-10-06/`。原ZIP保留在用户提供位置，非TvT及种族未明成员未解压纳入工作集。详见[接收记录](../docs/M2_GOLD_REPLAY_IMPORT.md)。

所有新增数据计入项目100 GB上限。公开ZIP与原始本机录像保留为来源档案；筛选后的TvT副本存放于`raw/replays/tvt-curated/`、`raw/replays/spawningtool-kevin-tvt/`及`raw/replays/local-gold-2026-10-06/`。本机账号原件继续留在游戏目录。公开包位于`downloads/public-replays/`；逐文件全量扫描索引位于`artifacts/runs/m2-public-001/`，仅作审计，不纳入当前清单。字节去重尚不等于比赛级去重，后者已按用户要求跳过；尚无正式训练分片或模型。
