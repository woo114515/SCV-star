# 本地数据入口

当前入口为 `manifests/current.json`，指向 `unified-tvt-2026-10-07-001/`（schema 3）。
总计3,077个原始文件：活动候选2,781，91115暂缓182，身份待核对63，排除51。这是收集归档时的状态；训练就绪统计使用下述独立入口。

快照内 `catalog.jsonl` 与 `catalog.sqlite` 保存全部记录；各队列JSONL用于筛选；`screened_eligible.jsonl` 为835个轻筛合格候选；`outcome_label_review.jsonl` 为10个胜负未知候选。
`summary.json` 给出统计，`provenance.json` 保存输入哈希及旧入口，历史快照不改写。

使用每条记录的 `local_replay` 定位原始文件；勿将轻筛合格解释为完整回放或训练就绪。
原始文件保持在 `raw/replays/tvt-curated/`、`raw/replays/spawningtool-kevin-tvt/`、`raw/replays/local-gold-2026-10-06/`。
未执行比赛级去重。下载包、引擎及个人游戏目录原件保持原位。

数据与逐场清单只留本地。Spawning Tool 导出不可再分发，须署名并回传最终成果；完整条款和当前统计见[数据总览](../docs/DATASET_OVERVIEW.md)。

## 首批 BC 数据

`manifests/training-current.json` 指向通过验收的训练 manifest：固定86383，100场、200视角，80／10／10场划分。读取器只接受通过验收且哈希匹配的分片；同场双方不跨集合。`manifests/current.json`和历史原始快照保持归档时内容，其中training_ready=0不能当作新的训练状态。

实现及使用方法见[对齐与训练数据](../docs/REPLAY_ALIGNMENT.md)。只使用训练入口指向的快照；失败试验及早期pilot不是训练数据。所有派生数据继承来源限制，禁止上传GitHub。
