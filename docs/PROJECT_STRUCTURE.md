# 项目目录结构

本仓库采用 Python `src` 布局。`envs/sc2_client.py` 与 `runtime/m0*.py` 实现 M0 接口验收，`runtime/m1.py` 实现 GPU 合成数据与恢复验证；正式策略模型、训练和联赛仍为占位，目录名不代表能力完成。

```text
SCV-star/
├── README.md                    项目入口、状态与目标
├── AGENTS.md                    原始贡献指南，保留不改
├── pyproject.toml               包元数据、M0 可选依赖与命令入口
├── requirements/                M0 固定版本与下载哈希
├── .editorconfig                UTF-8、换行与缩进约定
├── .gitattributes               Git 文本换行约定
├── .gitignore                   生成物、本机配置和凭据排除规则
├── src/scv_star/
│   ├── data/                    元数据、去重分组、划分与录像解码
│   ├── envs/                    引擎启动、协议连接、对局生命周期
│   ├── features/                观测编码、schema 与 critic 信息隔离
│   ├── actions/                 TvT 动作语义、掩码和协议转换
│   ├── models/                  编码器、循环策略、动作头、价值网络
│   ├── training/                BC 与 V-trace／UPGO 学习器
│   ├── league/                  对手池、历史快照和对局统计
│   ├── evaluation/              固定评测、指标与失败归因
│   └── runtime/                 actor 采样与真人实时推理编排
├── configs/
│   ├── project.example.json     项目范围、路径和存储预算
│   ├── local.example.json       本机信息模板
│   ├── local/                  忽略实际机器配置，只保留说明
│   └── experiments/            BC、RL 和评测设计模板
├── scripts/                     run_m0.ps1 与脚本说明
├── tests/
│   ├── unit/                   无游戏环境的逻辑验证
│   ├── integration/            游戏／GPU／录像集成验证
│   └── fixtures/               未来人工构造的小型样例
├── data/                        本地数据，不提交实体内容
│   ├── raw/                    原始录像与本地验收地图
│   ├── interim/                中间处理结果
│   ├── processed/              观测与动作分片
│   └── manifests/              来源、版本、哈希与集合清单
├── artifacts/                   本地运行产物，不提交实体内容
│   ├── checkpoints/            权重、优化器与历史快照
│   ├── runs/                   配置快照、日志、指标
│   └── evaluations/            逐局评测记录
└── docs/                        架构、开发、调研、许可与实验摘要
    └── experiments/            可提交的脱敏实验记录与模板
```

## 放置规则

- Python 文件、模块和函数用 `snake_case`；类用 `PascalCase`；常量用 `UPPER_SNAKE_CASE`。
- 新能力先放进既有职责模块，出现实际需求再细分，例如 `training/bc/` 和 `training/rl/`，不预建大量空层级。
- `src/scv_star/data/` 是数据处理代码，根目录 `data/` 是实际数据；二者不同。
- `src/scv_star/models/` 是网络结构，`artifacts/checkpoints/` 才是权重。
- `.gitkeep` 只用于让 Git 保留工作目录，不是数据或完成标记。数据与产物目录只提交说明及占位文件。
- 暂不创建 `assets/`、notebooks、Docker、CI、第三方源码副本或游戏安装目录，等有明确使用场景再加入。
- 用户已有 SC2 安装位于仓库之外，不搬入项目，也不修改其内容。

## 文档归属

模块关系见[架构文档](ARCHITECTURE.md)，配置语义见[配置说明](../configs/README.md)，运行证据见[实验记录](experiments/README.md)。技术调查中的安装路径和硬件是历史记录；新机器的真实运行配置留在 `configs/local/`。
