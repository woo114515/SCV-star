# 当前任务交接

## 当前任务：扩展到1,000场（进行中，2026-10-19）

负责人Windows Codex，分支main；用户明确恢复任务。本轮允许项目最多150 GB，保留1,000场，清理前复核。目标尚未完成，未开始正式BC／RL训练。原100场冻结清单及下面的SHA记录保持不变，当前训练入口仍指向原100场。

计划、注册表、逐场进度和日志在 `artifacts/runs/m2-expand-1000-2026-10-19/`；新增输出在 `data/processed/m2-expand-2026-10-19/`。plan固定种子20261019、原始SHA和已知比赛指纹划分，启用版本共1,063条新候选；已有100场另计。所有来源限制延续到派生数据，逐场清单、收据及日志不得上传Git。

11个启用版本：83830、84643、86383、87702、88500、90136、93333、96516、96999、97563、97579，均通过边界和完整单场试点。94137仅备用，未计可用；91115继续暂停，92440获取失败。CN96999/97579须使用 `cache/sc2-cn-96516/Versions/` 内EXE，registry引用r2配置；不要混用国际根EXE。mutable CASC三个数据根均仍有使用，不得硬链接或直接删除。

恢复从212场开始；共享进程池在 `batch-v4.log` 中发生BrokenProcessPool，根因未证实。已归档失败快照，仅关闭精确匹配本轮单场临时目录的6个孤儿SC2；复用完整报告后达到221场、442个玩家视角、555,887条样本，7场基础设施中断重新排队。证据为 `pool-recovery.json`、`pool-orphan-cleanup.json` 和 `isolated-resume.json`。不要把这些中断解释为录像质量问题。

当前批处理日志 `batch-v5.log`。`bc_isolated.py` 每场启动一个新Python进程，父进程核对任务哈希、PID、结果收据及完整manifest；私有日志在 `worker-attempts/`。基础设施异常最多自动重试两次，连续异常触发停止；每任务最多1小时。110项单元测试、Ruff及差异检查通过，日志 `code-checks-v4.log`。提取与schema未改变，仍按原门槛完整回放双方、逐条检查。

并发上限8路，按RAM和Windows提交内存同时限流，实际可低于8路；详见[数据契约](REPLAY_ALIGNMENT.md)。批处理与最终验收等待已启动，接手先查进程和执行收据，不重复启动。registry设 `drain: true` 可停止派发并等待当前任务结束。恢复前核对没有存活manager，保留完整产物，仅将有证据的基础设施中断重新排队；不得删除真实质量排除或原始失败记录。

恢复命令：`.venv/Scripts/python.exe -m scv_star.data.bc_batch --plan artifacts/runs/m2-expand-1000-2026-10-19/plan.json --registry artifacts/runs/m2-expand-1000-2026-10-19/registry.json --destination data/processed/m2-expand-2026-10-19 --progress artifacts/runs/m2-expand-1000-2026-10-19/progress.json --target 1000 --workers 8`。仅完整单场manifest计数，不按视角或启动次数计数；实际进度以progress.json和manifest复核为准。

最终验收等待程序为 `tmp/2026-10-19-data-1000/finalize.py`，日志 `finalization-v3.log`，状态 `finalization.json`。达到1,000场后检查全部子manifest、分片哈希、SHA唯一性、比赛指纹与划分，再在NVIDIA GPU验证各版本、三个split及最大实体观测，0次优化器更新。全部通过才发布 `training-current.json`；之后仍须复核清理、更新里程碑及脱敏汇总，不能仅凭后台脚本完成标记任务通过。124场预检在 `gpu-preview-v1/`，不替代最终验收。

已核验删除13份引擎运输副本，回收757,973,270字节；获取脚本归档在 `artifacts/archives/2026-10-19-engine-acquisition-scripts/`。清理证据为 `engine-transport-cleanup.json` 与 `storage-after-transport-cleanup.json`。另约138 MB重复EXE须先退休旧本机配置引用再处理。阶段收尾复核中断分片、临时脚本和工作树；不能删除仍运行任务引用的文件。其他会话未提交的AGENTS、架构、算力说明与人工回放工具保持原状，不混入本轮提交。

下方为首批100场的冻结验收记录，状态与版本号不代表本轮目标已完成。

更新：2026-10-12。负责人：Windows Codex；分支 `main`，M2 实现提交 `251482cece98fd141e28868736041de1b9108f85`（起点 `caade58`）。本文件后续提交仅补录版本号。其他会话的 AGENTS、架构／算力说明及人工回放工具改动保留，不混入本轮提交。

## 范围与状态

**M2 首批数据验收完成**：固定 86383，100 场人类 TvT／200 个玩家视角，80／10／10 场划分，共 285,459 条样本。全分片审计、资源哈希、NVIDIA 5070 Ti 读取与前后向、63 项单元测试通过。没有实现正式 BC 网络／训练器，没有优化器更新。权威状态见[里程碑](MILESTONES.md)与[摘要](experiments/M2_READY_2026-10-12.json)。

复用暂停前 72 场，重做中断项；最终尝试 102 场，排除 2 场配对率不足的录像。恢复批次约 46.4 分钟，最终审计约 8 分钟，正式训练耗时为零。`pause-state.json` 是历史暂停快照，不代表当前状态。当前无本轮运行任务。

## 产物与恢复

- 数据入口：`data/manifests/training-current.json`；冻结输出：`data/processed/m2-bc-2026-10-08/86383-v3/manifest.json`，SHA-256 为 `738a34fe3ccd4ee184d9e75ddbc9e29592159f30246b56e61e539c1d2ec090a6`。200 个 XZ 分片及静态地图哈希均在清单中。
- 选择计划及诊断：`artifacts/runs/m2-readiness-2026-10-08/`。原始录像快照保持不变，来源限制延续到派生数据，禁止提交到 Git。
- 不再恢复旧提取批次，不修改冻结分片。读取接口与增量提取命令见[数据契约](REPLAY_ALIGNMENT.md)。重新检查清单可运行 `Get-FileHash data/processed/m2-bc-2026-10-08/86383-v3/manifest.json -Algorithm SHA256`。
- 临时脚本及早期提取器源码已归档到 `artifacts/archives/2026-10-12-m2-readiness-scripts/scripts.zip`；同目录 `manifest.json` 保存原路径、逐文件与压缩包哈希，仅供追溯。中断分片清理索引另行保留。
- 下一项最小任务：取得下一阶段授权后，确定 BC 网络及多头损失契约，再用少量训练样本验证过拟合和恢复。在线 TvT 合法性／动作闭环及操作频率限制仍待实现。

Windows 项目约 97.54 GB，继续遵守 100 GB 上限。Ubuntu 环境尚未验收；如迁移，单独传输获许可的数据并核验上述哈希，不复制 Windows 虚拟环境，也不把录像或派生数据上传 GitHub。

用户已重新下载国服，当前本机路径为 `C:\Program Files (x86)\StarCraft II`；2026-10-12 安装清单标记 `5.0.17.98370`，仅存在 `Base98370`。这只是本机安装身份，尚未验证 API 或确认发布渠道最新状态。M2 的 86383 使用项目内独立数据目录和 EXE，不改成国服新版本。旧 M0 本机配置暂不覆盖，以免旧版本锁与新路径混用。
