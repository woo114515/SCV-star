# 当前任务交接

更新：2026-10-21（Windows M2 交接）；2026-11-08 Ubuntu/DSH 环境与 M4 v1 实现补充见文末。负责人：Windows Codex；分支 `main`。

## 当前结果

**M2 扩容与数据验收完成：1,000 个 TvT 录像文件、2,000 个玩家视角、2,467,676 条样本。** 原100场包含在总数中，冻结清单不变；覆盖11个历史build、105个地图名称。当前没有回放或训练任务，不再恢复本轮批处理。

| 划分 | 录像文件 | 玩家视角 | 样本行 |
| --- | ---: | ---: | ---: |
| 训练 | 824 | 1,648 | 2,049,826 |
| 验证 | 87 | 174 | 210,416 |
| 测试 | 89 | 178 | 207,434 |

训练入口为 `data/manifests/training-current.json`（schema_version 2），指向 `data/processed/m2-expand-2026-10-19/collection.json`，集合SHA-256 `373cfc6f4a267130e5e2259f8bd3f2e109fa35536d7cb8745c6061b690a897da`。901份子清单、2,000份有效分片及1,000个原始录像的哈希已复核；原100场清单SHA仍为 `738a34fe3ccd4ee184d9e75ddbc9e29592159f30246b56e61e539c1d2ec090a6`，入口内保留完整旧指针。

已完成的是数据准备，正式BC网络、训练器、在线TvT合法性与新版部署尚未完成。NVIDIA 5070 Ti Laptop GPU通过11个版本、三个split及最大531实体观测共15个案例的读取与有限梯度检查；参数未变，优化器更新为0。

## 验收证据

脱敏摘要见 [M2最终摘要](experiments/M2_READY_2026-10-21.json)，数据入口、读取方式及边界见 [数据契约](REPLAY_ALIGNMENT.md)。私有证据统一在 `artifacts/runs/m2-expand-1000-2026-10-19/`：

- `completion-audit-post-cleanup.json`：完整RAW／有效分片、来源、划分、旧100、版本与GPU证据复核通过；SHA `f7cf4f4150e92206cdab152997bb99f2ba74c4d5d8f8a2af18e26f6879cbe544`。清理前证据为 `completion-audit.json`。
- `qualification-audit-1000-final.json`：1,000场API资格通过，836场精确initData解码、164场国服API-only，资格与初始化异常均为0；SHA `2eda5e9a69325923b33b54aaf62789cf1ee48287e0c3fc73bfe419d350327ad4`。
- `gpu-final.json`：SHA `b88e4388e7ce8765de41097bbeba17c6d8fb6fae5a0da974b899da66448655a9`。
- `finalization.json` 与 `acceptance-summary.json` 内容一致，状态为 `data_acceptance_complete`。新增900场接受、45场质量门槛排除、16场处理失败分别保存；失败不冒充质量判定。
- `cleanup-result-20261021T072747Z-86f187fd.json`：131项无效／中断分片、重复EXE和旧本机配置已清理，共261,687,994字节；SHA `83d94446ae7ff9ea5bf93644f518b61a57027e9403af83aa1ff77cd125e59caf`。原录像、有效分片、地图、三套仍在使用的CASC根与诊断保留。

两个临时工作树已在核对干净状态、补丁等价合入main及文件哈希后正常移除，分支提交保留；证据为 `worktree-retirement-preflight-20261021.json`。48份临时源码归档在 `artifacts/archives/2026-10-21-m2-expansion-scripts/scripts.zip`，包SHA `529bd1894f19994b96f517ac52784304ba961a51622b661211d7d3c9571c389c`；同目录 `manifest.json` 保存原路径与逐文件哈希。历史审计中引用的临时源码由该包追溯，需要重现时按原相对路径恢复，不把旧启动脚本当作当前任务。

项目逻辑占用约102.40 GB，低于本轮150 GB授权；最终实测见私有目录中的 `storage-final-20261021.json`。统计跳过junction，不含项目外SC2安装和暴雪缓存。此前100 GB及200 GB限制保留为历史，不删除有用数据来强行回到100 GB。

## 保留边界与下一项工作

- 比赛级去重按用户要求未执行；双方视角绑定，已知初始化指纹跨集合为0，未知跨来源副本泄漏仍不能完全排除。
- 各build、DataVersion和ability目录分开，读取按版本形成批次，不合并原始技能ID，不伪造新版价格或时间轨迹。
- 国服164场有匹配引擎和API证据，缺少精确协议时未假装完成initData解码；地图扩展标记及赛事大厅不自动判坏，模组触发器语义未全面认证。
- 使用各自合法观测并保留战争迷雾。RAW标签可逆转换不等于在线动作一定合法；effects、完整武器／护甲等特征仍有缺失。
- Spawning Tool须署名、后续回传最终成果，禁止再分发；原始录像、派生数据、逐场清单、玩家信息和私有日志均不上传GitHub。
- 当前国服安装清单98370的API尚待复验，最终目标仍为开发时确认的最新正式版。Ubuntu训练环境尚未验收；不能复用Windows虚拟环境或绝对路径。

下一项最小任务是在取得模型阶段授权后，确定BC输入白名单、自回归动作头及损失掩码，再用少量训练片段检查过拟合和断点恢复；验证／测试集保持隔离。M3在线动作闭环与M4模型实现按 [里程碑](MILESTONES.md) 推进。

## 暂停与恢复历史说明

用户于2026-10-21 14:12明确“先继续任务”，随后再次要求继续，已完成本轮目标。当天14:00的停止是Agent在上下文压缩后误重放昨天的暂停消息，不是用户新的暂停指令；恢复记录 `resume-20261021-r2.json` 绑定真实消息时间。暂停、恢复及643／903／951场快照只作历史，不能覆盖最终状态或重复执行旧脚本。

正式提取、schema和worker源码保持在已验收实现 `3f558e4`，110项单元测试与Ruff的已有证据为 `code-checks-v4.log`；本轮恢复未修改这些源码。Git仅同步源码和脱敏文档。其他会话的AGENTS、架构、算力说明、人工回放工具及根目录删除项保留，不混入本轮提交。

## Ubuntu/DSH 侧补充（2026-11-08）

- 分支：`chore/ubuntu-training-env`（基于 `a24412a`），只在该侧分支提交，不直接改 `main`；本补充的锁文件和文档改动尚未合并。
- 环境：Ubuntu 24.04.5 LTS、Python 3.11.16、torch 2.13.0+cu130、RTX 5070 Ti Laptop 12,227 MiB、驱动 595.99.02；依赖锁 `requirements/bc-linux-py311-cu130.txt`（SHA-256 `df9b497ae2bb56c7697406de7e405004aebb001bc8bd6a84a94af80a934c98a2`）。Windows 与 Ubuntu 环境互不复制。
- 数据：本机 `SCV-star/dataset`（仓库父目录，不提交）链接到 `data/processed/`，入口 `data/manifests/training-current.json`；集合 SHA-256 `373cfc6f4a267130e5e2259f8bd3f2e109fa35536d7cb8745c6061b690a897da`、入口 SHA-256 `79390eea6ed18a7039e7a5d88457fcedb3b5b4b8ff6ce243ae185bc26ccfbc98`；1,000 场／2,000 视角／2,467,676 行。
- 验收：环境验收时 110 项，M4 v1 后当前 146 项（145 通过／1 项 Windows 专用跳过）；Ruff 通过；M1 GPU 探针 FP32／FP16／BF16 与恢复逐位一致；`bc_reader_check` 15 案例、11 版本、最大 531 实体、优化器更新 0。证据 `artifacts/runs/m1-gpu-linux-20261108/report.json`（SHA-256 `caa968fd14bdcfdf17265d97290e0353839be9af4cbc6f03bf4005904a12a054`）与 `artifacts/runs/m2-linux-reader-check-20261108/gpu-check.json`（SHA-256 `d48baddcc047eae94177b52d13ca26b677c7a6175a82afdf6f8958ce8ed7a785`）；记录见 [M1 Linux 环境验收](experiments/M1_LINUX_2026-11-08.md)。
- 限制：M4 v1 缓存／模型／APM 加权训练器已实现，但小样本过拟合、2 小时训练和最终测试尚未运行；M3 在线动作／最新版 API 仍需 Windows。现有 `m1.py` 是 Windows 入口，报告中的 `dependency_lock_sha256` 字段仍指 Windows 锁 `6db46834531e1fd871e1417c186cc42a2d252a55c974d9fd2c552b1e47cdea51`，实际 Linux 锁以本条为准。
- Windows 工作副本：存在未提交的 APM 质量代码／测试／文档（`bc_quality.py` 等）；截至本记录其描述的质量清单在当前数据入口中不存在。该工作由用户稍后提交；Ubuntu 侧未合并、未改写，不要覆盖。
- 下一步：完成 M4 v1 小样本过拟合与断点恢复、2 小时训练和最终测试；首轮固定 86383 单 build，验证／测试集保持隔离。

## M4 v1 实现与暂停（2026-11-08）

- 分支：`feat/m4-bc-v1`，基于 `chore/ubuntu-training-env`／`135531e`；实现与实验计划提交见该分支，`main` 未改。
- 代码：`data/bc_catalog.py`（按 build 规范词表）、`data/bc_cache.py`（张量化序列缓存）、`data/bc_features.py`、`models/bc.py`（实体／标量、两层 LSTM、8 个动作头）、`training/bc.py`（可恢复训练器）、`training/checkpoint.py`、`training/metrics.py`；命令入口 `scv-bc`／`scv-bc-cache`。
- 测试：146 项单元测试（145 通过／1 项 Windows 专用跳过），Ruff 通过；覆盖词表、缓存、模型、掩码损失、检查点、指标、APM 标注与加权采样。
- 数据：86383 训练／验证张量缓存位于 `data/interim/bc-86383-v1/`（派生数据，不提交），带 per-view APM 标签：训练 10,546 序列／326,361 行／22 分片，验证 1,197／36,694／3；APM 清单 SHA-256 `f15141e…c436`；86383 的 300 个视角中，训练 152 high／34 medium／56 low。配置固定种子 `20261108`、batch 2×32、FP32、AdamW 1e-4、2 小时预算、验证每 5,000 次更新（计划为 1,000，偏差理由见实验记录）；`sampling_weights` high 3.0／medium 2.0／low 1.0。
- 真实数据触发：ability 316 同时为 command／autocast，v1 使用独立 2 类 kind 头；原“由 ability 推导 kind”不成立。实验记录：`docs/experiments/20261108_M4_bc86383_seed20261108.md`。
- 待续（下次会话）：小样本过拟合（1—2 场、500 次更新、各头损失下降与训练子集能力 top-1 ≥ 0.9）、保存／恢复下一步逐位比较、2 小时全量训练、训练后构建 test 缓存并用 `best.pt` 只评估一次、补全记录与状态文档。
- 暂停前最后一个任务（缓存构建）已完成；没有运行 BC 优化器。Windows 工作副本的未提交 APM 质量代码／文档仍未合并、未改写；其质量清单目前不存在。
