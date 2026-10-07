# 运行产物

| 目录 | 用途 |
| --- | --- |
| `checkpoints/` | BC、RL、优化器状态及历史策略快照 |
| `runs/` | 按实验 ID 保存解析后配置、指标、日志及运行状态 |
| `evaluations/` | 冻结评测逐局记录、失败信息与原始统计输出 |
| `archives/` | 已退役的一次性脚本压缩档案、原路径索引及完整性校验；仅供追溯 |

除本说明和 `.gitkeep` 外全部不提交 Git。源码中的模型结构属于 `src/scv_star/models/`，训练得到的权重属于本目录，不混放。

建议 ID 使用 `YYYYMMDD_stage_description_seedN`；一次实验固定版本并具有唯一 ID。模型文件需关联自身 SHA-256、代码 commit、配置、数据清单和 schema；最终格式待训练实现确定。

断点恢复不只保存网络权重，还需优化器、随机状态、数据位置或采样／对手池状态。恢复到新 build 或改变算法时应建立新实验，不能当作原实验的无缝续训。

不自动清理，先测空间再约定保留策略。可分享的脱敏报告摘要放到 `docs/experiments/`，不得把未经核查的权重或游戏资源随报告发布。

## 脚本与清理规则

可复用工具放在 `src/` 或 `scripts/`，新的一次性脚本放在 `tmp/YYYY-MM-DD-purpose/`；不再把执行入口散放在 `artifacts/` 顶层或运行目录中。运行目录内的 `executed-source/` 是当时实际执行的源码证据，不能当作重复代码删除。

2026-10-07 已将顶层20个、早期M2运行目录13个退役脚本归并至 `archives/2026-10-07-retired-scripts/scripts.zip`。同目录 `manifest.json` 保存原路径、逐文件SHA-256和压缩包SHA-256；校验后移除33个散落脚本及1个可重建字节码文件。归档包含历史固定路径和下载诊断，仅供查看，不应直接执行。

`runs/m0-human-002/executed-source/` 和 `runs/data-collection-archive-2026-10-07/historical-scripts/` 保持原位，以维持既有验收证据和索引。日志、报告、模型、录像及游戏资源不在本次清理范围。档案可能包含私有路径和来源链接，不提交GitHub。
