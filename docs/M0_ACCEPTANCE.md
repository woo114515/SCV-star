# M0：客户端接口验收

本阶段验证现有 Windows 中国区客户端的接口、动作、迷雾和真人连接。测试控制器仅用于验收，不是学习策略。实际结果见[本轮记录](experiments/M0_2026-09-29.md)。

## 环境与复现

实测环境为项目内 Python 3.11.16、setuptools 84.0.0；通信依赖见带哈希的 [M0 锁文件](../requirements/m0-windows-py311.txt)。安装于 `.venv/`，Python 本体与 uv 缓存位于 `cache/`，下载位于 `downloads/`，均不提交。没有修改系统 PATH，也没有安装 PyTorch。

官方 `s2clientprotocol==5.0.16.97563.0` 的生成代码不能直接配合 protobuf 7.36.2 导入。本阶段固定 protobuf 3.20.3；这只是 M0 的兼容环境，不能据此锁定未来训练环境。Python 下限调整为 3.11，使用标准库的流式文件哈希接口。

已有环境中可执行：

```powershell
# 安装/校验固定通信依赖；需要联网时先确认当前任务授权范围
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'cache/uv'
.\downloads\uv-0.12.20\uv.exe pip install --python .venv/Scripts/python.exe --require-hashes -r requirements/m0-windows-py311.txt
# 在已装有 setuptools 84.0.0 的环境中安装项目
.\downloads\uv-0.12.20\uv.exe pip install --python .venv/Scripts/python.exe --no-deps --no-build-isolation -e .
.\.venv\Scripts\scv-m0.exe --help
.\.venv\Scripts\python.exe -m unittest discover -s tests/unit -v
```

贡献代码前还需安装独立的开发工具锁并运行 Ruff，命令见[开发说明](DEVELOPMENT.md)。开发工具不改变 M0 通信依赖锁。

初次部署使用 uv 0.12.20 官方 Windows x64 包，压缩包 SHA-256 为 `95f9bc30fbb3574d276e28ac4a6de932d25153645853d13da8c21eec3bc88d06`。Python 安装目录通过 `UV_PYTHON_INSTALL_DIR` 指定为项目内 `cache/python`，不依赖全局 Python 或激活脚本。

首次在另一台已授权机器上部署时，从 [uv 官方版本附件](https://github.com/astral-sh/uv/releases/download/0.12.20/uv-x86_64-pc-windows-msvc.zip)获取压缩包并核对上述哈希，解压到 `downloads/uv-0.12.20`；随后在没有现成 `.venv` 的项目副本中执行：

```powershell
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location) 'cache/python'
$env:UV_PYTHON_BIN_DIR = Join-Path (Get-Location) 'cache/bin'
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'cache/uv'
.\downloads\uv-0.12.20\uv.exe python install 3.11.16
.\downloads\uv-0.12.20\uv.exe venv --python 3.11.16 .venv
.\downloads\uv-0.12.20\uv.exe pip install --python .venv/Scripts/python.exe setuptools==84.0.0
```

再执行前述固定通信依赖与项目安装命令。游戏和地图另行准备，Python 环境安装不会下载 SC2。地图来源为[官方 Ladder2019Season3 包](https://blzdistsc2-a.akamaihd.net/MapPacks/Ladder2019Season3.zip)，本轮只将 AcropolisLE 提取到 `data/raw/maps/`，其哈希见实测记录。

## 本机配置

`configs/local/m0.json` 按 [m0.example.json](../configs/m0.example.json) 填写安装目录、明确 build、地图路径与 SHA-256；`configs/local/engine-m0.json` 按 [engine.example.json](../configs/engine.example.json) 保存实测引擎身份和二进制哈希。两者均被 Git 忽略。已核对的国服97579发行身份另存于[可提交清单](../configs/engines/sc2-cn-97579.json)，不含机器路径。

第一次使用 `probe` 显式指定安装目录和 build，结果只是版本发现，不证明其为最新正式版。人工核对发行地区和正式发布状态后才能作为目标版本；禁止把 `latest` 写成运行值或遇到错误静默换版本。

`inspect` 可检查已缓存的战网地图，但地图名称不是内容哈希，不能替代固定地图验收。`smoke`、`fog`、`human` 必须提供存在且哈希匹配的本地地图。研究包地图按其原许可使用，不上传资源文件。

## 运行入口

在项目根目录执行；每次使用新的 `RunId`，拒绝覆盖已有报告：

```powershell
.\scripts\run_m0.ps1 -Mode smoke -RunId m0-smoke-next -Seed 0
.\scripts\run_m0.ps1 -Mode fog -RunId m0-fog-next -Seed 0
# 此命令打开真人窗口，持续至少 600 秒；需操作者在场
.\scripts\run_m0.ps1 -Mode human -RunId m0-human-next -Seed 0
```

若 PowerShell 的执行策略不允许运行脚本，可直接使用 `scv-m0.exe` 并传入 `--help` 列出的显式参数；不要修改全局执行策略。

| 模式 | 检查内容 | 通过的含义 |
| --- | --- | --- |
| `probe` | Ping、可用地图、退出 | 指定客户端可连接；不代表最新版确认 |
| `inspect` | 缓存地图建局、己方 RAW 观测、游戏数据 | 地图可运行；不代表地图已锁定 |
| `smoke` | 移动、采矿、造 SCV、造补给站、无效单位指令 | 实际状态变化与拒绝路径成立；主动退出不是胜负评测 |
| `fog` | 双客户端，双方侦察、撤回、隐藏单位移动 | 测试情景中没有实时隐藏状态暴露；不等于穷尽所有隐身/幻象机制 |
| `human` | 真人端加可控测试端，持续 10 分钟 | 连通性测试完成；仍需真人确认操作体验 |

真人验收请移动单位、建造、生产，保留双方基地直到计时结束；建议保留测试端工人，以便持续采集动作时延。窗口名为 `SCV-star - Human M0 acceptance`，结束时自动保存录像并退出。测试端每两秒移动一名工人，没有神经网络。工人损失后选择存活的己方工人；没有工人时继续观测并记录动作采样中断，不能将缺失样本计为零延迟。提前结束比赛不满足长测要求。

本机必须使用已验证的“真人端建局并作为房主/玩家1、程序端加入”流程；真人使用原生接口，程序使用 RAW 观测。此前真人作为加入方时，出现悬停正常但场景内鼠标点击无效的问题，改变显示模式和单独移除 RAW 均未解决。房主配置已获用户确认操作正常，用户随后要求结束测试；修复后的完整10分钟长测未完成，见实测记录。底层原因尚未确认。

`human-check` 是直接 CLI 提供的短诊断模式，可传 `--seconds 120`，不算10分钟验收；`--human-display-mode` 只影响该次真人客户端。诊断中可在对应运行目录创建 `stop-request` 文件请求下个循环退出，程序会保存失败原因并清理自己的客户端。不得将短诊断的自动完成标志当作人工验收通过。

## 证据与边界

每次运行在 `artifacts/runs/<RunId>/` 保存报告、客户端日志和适用的录像。真人测试另有 `ready.json`、`progress.json`。报告保留失败，不能只统计成功重试。后续运行还保存源码哈希、依赖锁哈希、Git HEAD/未提交状态和启动前存储检查；早期探索报告没有全部追溯字段，记录中单独说明。

客户端只监听本机地址；关闭时仅处理自己创建的进程。创建对局与每次观测均保留迷雾，关闭额外隐身、潜地影子、建筑占位及全局分数接口。迷雾测试的第二玩家信息仅作测试判据，不传入策略。

版本、地图校验失败即退出。存储检查在项目达到 80 GB 或分区剩余不足 20 GB 时停止；它不等于全盘配额系统，游戏可能写入外部日志/缓存，仍需统计和保留空间。项目总新增占用不得超过 100 GB。

本阶段不验收胜率、模型质量、GPU 训练兼容性或采样扩展性。真人时延是测试控制器的基线，加入网络后必须重新测量。早期报告按双方观测批次计时且有Windows时钟量化；最终复测改用 `perf_counter`，从程序侧观测接收到动作确认单独计时，并在协议能返回对应动作时记录源观测到动作生效的 game-loop 差及样本数。不能将该差值等同于纯网络延迟。

## 原始依据

- [暴雪协议](https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/sc2api.proto)：Ping 身份字段、建局、双客户端同步、RAW 观测与动作。
- [暴雪地图下载说明](https://github.com/Blizzard/s2client-proto#downloads)：官方地图包和许可。
- [PySC2 客户端流程](https://github.com/google-deepmind/pysc2/blob/master/pysc2/env/sc2_env.py)：用于核对并发 join 和端口结构；本项目未复制其实现。
- [uv Python 管理](https://docs.astral.sh/uv/concepts/python-versions/)：项目内解释器安装。
