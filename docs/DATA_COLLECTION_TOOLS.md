# 数据收集工具与归档

在项目根目录使用 `.venv\Scripts\python.exe`。以下命令仅操作清单或读取资源，不自动下载、启动SC2或运行训练。

## 统一快照

```powershell
.venv\Scripts\python.exe -m scv_star.data.collection_archive --source data/manifests/unified-tvt-2026-10-06-002 --screening data/manifests/screen-sample-885-2026-10-07 --output data/manifests/NEW_SNAPSHOT
```

输出必须是未存在的新目录；加 `--activate` 才原子切换 `current.json`。工具校验全部原始录像的大小与SHA-256，保留来源条款，并拒绝身份冲突与孤立抽样记录。本次已生成 `unified-tvt-2026-10-07-001`，不要覆盖或重复生成该目录。

## 地图资源复核

```powershell
.venv\Scripts\python.exe -m scv_star.data.resource_audit --manifest artifacts/runs/batch-replays-885-2026-10-07/resource-downloads.json --output artifacts/runs/NEW_RUN/resources.json
```

只读核对已有缓存，不覆盖或下载；缺失及哈希不符均返回失败。归档时42项全部通过。

## 新的分层抽样计划

```powershell
.venv\Scripts\python.exe -m scv_star.data.sample_plan --catalog data/manifests/unified-tvt-2026-10-07-001/catalog.jsonl --seed 20261007 --output artifacts/runs/NEW_RUN/plan.json
```

每个合格的引擎×地图名称×来源层随机选一份，记录种子与层大小，不执行回放。它是今后完整覆盖各层的计划工具，不复刻之前96个样本的限时选择算法，也不表示新的验收已发生。

实际回放继续使用现有 `scv_star.runtime.replay_probe`，先用 `--help` 确认参数；必须指定匹配的引擎与本地配置。`--info-only`仅检查元信息，不代表完整回放。完整回放须另外确定样本数及时间预算，不恢复已取消的全量任务。

## 证据与临时文件

本轮整理记录位于 `artifacts/runs/data-collection-archive-2026-10-07/`：`inventory.json`为文件盘点，`resource-audit.json`为资源校验，`script-archive-index.json`保存13个历史脚本/入口文件的原路径、归档路径及哈希。归档副本位于 `historical-scripts/`。

旧临时入口保留用于历史引用，已标记归档；全量回放停止标记保留。人工验收脚本不在本次清理范围。旧运行日志、快照、下载包、原始录像与引擎保留，避免破坏证据链。

以后一次性脚本统一放 `tmp/YYYY-MM-DD-purpose/`，每轮结束检查引用，先归档必要脚本与证据，再清理可重建缓存。本次仅清理旧批量脚本目录的Python字节码缓存。所有逐场清单和归档索引仅留本地；Git只保存源码、文档与脱敏统计。

2026-10-07 后续清理：`artifacts/` 顶层及早期M2目录的33个固定批次脚本已归并为 `artifacts/archives/2026-10-07-retired-scripts/scripts.zip`，核验后删除散落原件及1个字节码缓存。原路径到压缩包成员的映射和SHA-256见同目录 `manifest.json`。旧路径不再作为运行入口；上述13个已索引的历史脚本/入口文件及M0执行源码快照保留原位。清理前后其余1,988个产物文件大小和修改时间一致，未运行归档脚本、下载或回放任务。
