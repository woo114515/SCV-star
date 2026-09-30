# 91115 引擎获取与重放阻塞记录

2026-09-30：**已取得带有效暴雪数字签名的91115引擎，但运行环境未通过，IEM 2024录像仍有0场通过引擎重放。** 引擎来自社区存档，不是本轮从暴雪CDN取得。既有官方97563环境另行复测，两场录像、四个玩家视角全部通过。

## 取得的文件与身份

| 项目 | 实测结果 |
| --- | --- |
| 来源 | [暴雪论坛用户Talv发布的历史镜像](https://us.forums.blizzard.com/en/sc2/t/previous-version-of-the-game-replay-cannot-be-watched/28344/6)；帖子托管于官方论坛不代表镜像由暴雪发布 |
| ZIP | `Base91115.zip`，52,216,504字节，解压CRC检查通过 |
| ZIP SHA-256 | `832d2342d70532586225c1d093b8c7525cc7217e4bb1cd9805e912d41307d6f2` |
| 引擎 | `SC2_x64.exe`，64,961,664字节 |
| 引擎 SHA-256 | `aacdd0f1f4a8747f91bead27070e1c26c6ff7af39401dea48f94030b3d429fdc` |
| Authenticode | `Valid`；签名者Blizzard Entertainment, Inc.；DigiCert代码签名证书及2023时间戳 |
| 文件版本 | `5.0.12.91115`；原生错误日志也报告B91115 |
| 目标DataVersion | `7857A76754FEB47C823D18993C476BF0`，来自已有录像及历史版本清单 |

只在签名与文件版本校验通过后尝试运行。没有执行包内其他程序、修改EXE或安装依赖。下载、解密及包检查耗时约18.6秒，不代表部署或解码耗时。

文件保存在Git忽略的`downloads/sc2-base91115-community/`。版本记录为[`configs/engines/sc2-international-91115.json`](../configs/engines/sc2-international-91115.json)，状态明确为`binary_verified_runtime_blocked`、`api_identity_verified=false`。其中版本字段是待验证的固定目标，不能当作已通过API Ping的证据。

## 官方获取路径的实测

1. [历史版本归档](https://github.com/mdX7/ngdp_data/blob/master/US/s2/91115/versions)确认build key为`51dfb8730d960b7914d59ef9841f29ee`。本轮US、EU、KR及Akamai官方节点的该配置返回404；其他节点还出现403或TLS错误，不能统一解释为文件不存在。
2. 从[BlizzTrack保存的原始配置文本](https://blizztrack.com/config/s2/bc/51dfb8730d960b7914d59ef9841f29ee)恢复898字节清单，MD5与上述build key完全一致。这是已核对哈希的历史清单，并不证明其所有对象仍可下载。
3. 清单指向的安装对象`7ee68a29573ec98832df2ec814f0cb5e`及编码索引`ff9144e5fd1c388e6811277c68885843`在本轮US官方CDN请求中均返回404。历史CDN配置`8b8357e6560bf86a7983a108ed7c758e`仍可获取并核验哈希，但其loose-file索引返回404；本机154个归档索引也未找到上述两个对象。
4. 在隔离的97563客户端调用官方`RequestReplayInfo(download_data=True)`，得到`DownloadError: Replay build version is not available.`。未用97563强行启动91115录像。

这些结论只覆盖已检查入口，不证明全球所有副本均已消失。官方API的精确binary/data版本要求见[暴雪协议说明](https://github.com/Blizzard/s2client-proto/blob/master/docs/protocol.md)。

## 两次隔离运行与阻塞点

为避免修改国服及已验证的97563环境，实际复制约27.49 GB的SC2Data，并复制必要DLL、已验签引擎及配置，构建独立标准目录。复制约11.4秒；没有下载完整国际客户端。

| 尝试 | 配置与结果 |
| --- | --- |
| 91115历史build配置＋可用CDN配置 | 启动约13秒后退出，`e_errorIdStreaming (NGDP:E_NOT_AVAILABLE)`；日志明确缺少patch config `a643ab02995a5989ddd95f74d0e72392`。随后US、EU、Akamai对此对象的独立请求均为404 |
| 现有97563存储配置＋91115原版引擎和精确旧DataVersion参数 | 启动约10.3秒后退出，`e_errorIdStreaming (master index)`；未建立API连接，不能认定跨版本存储配置兼容 |

两次均在开始录像之前失败。未绕过版本校验、修改录像头或替换历史规则。另只读检查已有10场样本的资源引用，15个独立地图／模块对象中10个尚未缓存；本轮未下载这些资源，因为核心环境尚未可用。

进一步推进需要找到可核验的91115历史补丁配置、编码索引及匹配核心数据。仅重新安装当前国际客户端、重复下载EXE或补地图，均不能保证解决本轮阻塞。54场IEM TvT仍应保留为“已取得、未通过观测重建”，不计入训练可用量。

## 97563复核、空间与证据

官方97563环境再次完成原有两场Spawning Tool录像、四个视角的终局重放，耗时61.797秒，迷雾与胜负检查通过；粗步进及观测—动作对齐限制不变。报告SHA-256为`943deff54158c3a747fdf4ec79de20a20bcb6414ddbd993b1de3945b8603ff88`。这是重复验证，没有新增独立比赛。

已删除本轮失败的91115运行副本、冗余加密下载及失效本机配置；保留ZIP、已验签EXE、清单与日志。最终项目约**34.11 GB**，C盘剩余约154.85 GB；两次91115错误产生的项目外日志共780,986字节，另行记录。测试期间项目峰值约61.75 GB，未超过100 GB预算。

对比文件清单，国服安装和共享地图缓存均无新增、修改或删除；国服原始配置与97579二进制哈希不变，97563引擎哈希也不变。游戏用户目录日志等正常写入不属于这一“不变”结论。AGENTS.md未修改。

本机证据在`artifacts/runs/m2-international-91115-001/`：`summary.json`、官方对象请求、镜像下载与签名报告、隔离运行的两次失败报告、原始NGDP日志及`97563-regression/report.json`。原始证据、游戏文件、地图与私有路径不上传GitHub。没有修改训练或重放源码，也未运行训练。
