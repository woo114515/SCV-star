# 当前任务交接

## 当前任务：扩展到1,000场（2026-10-19）

负责人Windows Codex，分支main；目标已恢复为活动状态。用户允许本轮项目最多150 GB，保留1,000场，清理须先复核。原100场清单与哈希保持下面的冻结记录，不重新提取。当前仍未开始正式训练。

新阶段计划、注册表、逐场进度和日志位于 `artifacts/runs/m2-expand-1000-2026-10-19/`；候选输出位于 `data/processed/m2-expand-2026-10-19/`。`plan.json`固定种子20261019并继承旧比赛指纹划分；`registry.json`只允许通过受控时序及单场完整提取验收的版本进入批处理。新集合达到1,000场前不替换原 `data/manifests/training-current.json`。

已恢复96999、97579，新增83830、87702、88500、90136及备用94137的校验EXE；官方获取证据在 `downloads/engine-expand-2026-10-19/`。11个启用版本已通过受控动作时序验证；94137仅备用，未计可用。CN96999/97579须使用 `cache/sc2-cn-96516/Versions/` 内EXE，二者同根复测均通过；registry引用r2本机配置。91115继续暂停，92440有界获取失败，未纳入计数。

单场验收与集合代码已合入 `9366a87`，地图获取为 `5605caa`；本轮提取、调度及试点仍有未提交修改。受控实验 `boundaries.py`、试点 `pilots.py` 的一次性入口在 `tmp/2026-10-19-data-1000/`。当前试点日志为 `pilots-v3.log`，批处理为 `batch-v2.log`，受控汇总为 `boundaries.log`。先查真实进程与报告，不重跑已通过项目。来源标签缺失修复已核对全部1,063条可启用新候选，完整提取报告可直接重做验收；标签从精确下载证据和原始来源政策恢复，保留所有限制。中断输出保留在对应build的 `.interrupted/`，不计可用。

97项单元测试与Ruff通过；124场的中途集合通过8个版本、三个split及最大实体观测的NVIDIA GPU检查，0次优化器更新，证据在本轮 `gpu-preview-v1/`。这只是预检，不替代最终1,000场的集合与GPU验收。当前计数以 `progress.json` 为准，剩余批量仍在运行；不要把冻结100场或预检124场当作目标完成。复核后可回收约896MB引擎运输/重复副本；mutable CASC三个数据根仍须保留。

正式批处理恢复命令：`.venv/Scripts/python.exe -m scv_star.data.bc_batch --plan artifacts/runs/m2-expand-1000-2026-10-19/plan.json --registry artifacts/runs/m2-expand-1000-2026-10-19/registry.json --destination data/processed/m2-expand-2026-10-19 --progress artifacts/runs/m2-expand-1000-2026-10-19/progress.json --target 1000 --workers 6`。运行前核对代码与当前任务进程；仅完整单场manifest可增加可用计数，未完成输出先归档恢复。整体版本混合、GPU读取、指纹与SHA检查通过后才发布新入口。

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
