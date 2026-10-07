# 本地数据入口

当前入口为 `manifests/current.json`，指向 `unified-tvt-2026-10-07-001/`（schema 3）。
总计3,077个原始文件：活动候选2,781，91115暂缓182，身份待核对63，排除51。训练就绪为0。

快照内 `catalog.jsonl` 与 `catalog.sqlite` 保存全部记录；各队列JSONL用于筛选；`screened_eligible.jsonl` 为835个轻筛合格候选；`outcome_label_review.jsonl` 为10个胜负未知候选。
`summary.json` 给出统计，`provenance.json` 保存输入哈希及旧入口，历史快照不改写。

使用每条记录的 `local_replay` 定位原始文件；勿将轻筛合格解释为完整回放或训练就绪。
原始文件保持在 `raw/replays/tvt-curated/`、`raw/replays/spawningtool-kevin-tvt/`、`raw/replays/local-gold-2026-10-06/`。
未执行比赛级去重。下载包、引擎及个人游戏目录原件保持原位。

数据与逐场清单只留本地。Spawning Tool 导出不可再分发，须署名并回传最终成果；完整条款和当前统计见[数据总览](../docs/DATASET_OVERVIEW.md)。
