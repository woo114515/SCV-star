# 测试规划

当前使用标准库 `unittest`，`unit/test_m0_guards.py` 验证版本漂移、缺失身份字段、地图变更、协议错误及观测配置。Windows 运行 `.\.venv\Scripts\python.exe -m unittest discover -s tests/unit -v`；Ubuntu 运行 `.venv/bin/python -m unittest discover -s tests/unit -v`。真实客户端验收通过 [M0 命令](../docs/M0_ACCEPTANCE.md) 单独运行，不在单元测试中隐式启动游戏。

当前 Git 分支共 146 项单元测试，覆盖版本／协议保护、M1 恢复、录像筛选、下载与清单合并、观测时间对齐、雷达点、动作逆转换、序列边界、条件掩码、冻结数据哈希和无损重压缩，以及 M4 BC 词表／缓存／模型／掩码损失／检查点／训练器恢复、APM 视角标注与加权采样。依赖 M0、M2 及 M1 的 PyTorch 环境；缺依赖的跳过不能算通过。真实客户端回放、全分片逐条检查和 NVIDIA GPU 前后向另外执行，见[M2 数据验收](../docs/REPLAY_ALIGNMENT.md)。

Ubuntu 24.04 实测：145 通过、1 项按设计跳过（`test_host_memory` 仅 Windows 原生内存计数器），0 失败；Ruff 格式检查（72 个已跟踪 Python 文件）与静态检查通过。详见 [Ubuntu 环境验收](../docs/experiments/M1_LINUX_2026-11-08.md) 和 [M4 BC v1 记录](../docs/experiments/20261108_M4_bc86383_seed20261108.md)。Windows 工作副本另有待提交的 APM 质量测试，合并后数量会更新。

| 目录 | 未来覆盖范围 | 依赖边界 |
| --- | --- | --- |
| `unit/` | ID 语义、序列对齐、终局与截断、返回值计算、掩码、指标 | 尽量不启动 SC2；真实数值逻辑出现时再选定框架 |
| `integration/` | 固定引擎连接、TvT 能力、实际录像解码、GPU 前后向和恢复 | 明确标记游戏、地图、数据、GPU 条件；缺依赖报告未运行 |
| `fixtures/` | 人工构造的小型元数据或协议样例 | 可提交文本样例；不放私有录像、游戏资源、凭据或模型 |

命名采用 `test_<behavior>.py`。尚未设置覆盖率阈值、pytest 或 CI；单元测试通过不代表游戏、GPU 或训练通过。

首批有意义的测试：未知版本拒绝回退；同一比赛双方与所有切片不跨集合；动作前观测对齐；修改 critic 专用信息不改变 actor logits；起降／附件／队列／变形执行；终局与时间截断的 bootstrap 区别。

测试用的脚本动作只用于检查接口，不加入执行策略。集成测试产生的录像与日志放到 `artifacts/`，单独计入存储预算。
