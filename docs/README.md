# 文档导航

## 当前项目约定

| 文档 | 阅读目的 |
| --- | --- |
| [目录结构](PROJECT_STRUCTURE.md) | 找到源码、配置、数据与产物的正确位置 |
| [架构与数据契约](ARCHITECTURE.md) | 理解模块职责、信息边界和数据传递 |
| [开发说明](DEVELOPMENT.md) | 了解当前能做什么、后续环境验证顺序与代码约定 |
| [里程碑](MILESTONES.md) | 查看完成状态和下一阶段验收标准 |
| [M0 接口验收](M0_ACCEPTANCE.md) | 环境、固定版本、本机命令和真人连接 |
| [M0 实测记录](experiments/M0_2026-09-29.md) | 中国区 build、动作、迷雾及真人运行证据 |
| [M1 GPU 验证](M1_ACCEPTANCE.md) | 固定 PyTorch 环境、合成数据前后向和检查点恢复 |
| [M1 实测记录](experiments/M1_2026-09-30.md) | GPU、CUDA runtime、精度、显存和恢复一致性证据 |
| [M2 三来源小样本](M2_REPLAY_SOURCES.md) | 本机国服重放、公开样本解析、版本差异与历史引擎限制 |
| [实验记录约定](experiments/README.md) | 记录版本、预算、结果与可复现证据 |
| [配置模板说明](../configs/README.md) | 区分项目默认值、实验配置和本机配置 |
| [测试规划](../tests/README.md) | 区分单元、集成与需要实际环境的验证 |
| [Git 工作流程](GIT_WORKFLOW.md) | 提交、分支和双系统同步 |

## 调研依据

- [SC2 TvT 技术方案](SC2_TVT_RESEARCH_PLAN.md)：2026-09-29 的源码调查、算法路线与资源判断。
- [许可要求](SC2_LICENSE_REQUIREMENTS.md)：暴雪 AI 许可、DI-star LICENSE 与未完成的适用性核查。

调研文件保留调查当时的事实与授权背景。当前结构及进度以本目录的项目约定和里程碑为准；尚未实测的版本兼容性、数据量与吞吐仍不能视为已验证。

历史 [AGENTS.md](../AGENTS.md) 保持原文，其空目录描述不代表当前目录状态。
