# 公开录像下载与引擎需求统计

## 范围与口径

2026-09-30，用户授权先收集公开原始录像，再统计所需引擎；91115引擎恢复暂缓。本轮不获取其他引擎，不启动游戏、训练或修改国服安装。

- [SC2ReSet 2.0](https://zenodo.org/records/14963356)：固定记录14963356的71个赛事ZIP及3个说明／许可文件，发布清单合计4,539,835,228字节。复用已验证的IEM 2024包，其余文件校验发布方MD5并记录SHA-256。
- [Spawning Tool TvT目录](https://lotv.spawningtool.com/replays/?tag=12)：固定采集时的276页、6,876个不同ID。目录记录不等于已下载录像，也不是去重后的比赛数。
- [Spawning Tool赛事包页](https://lotv.spawningtool.com/replaypacks/)：补充SC2ReSet未收录且本轮可直接获取的赛事原始包和公开文件夹，包括近期HSC及ESL地区赛。赛事年份不用于推断build。Google Drive来源没有发布方校验值时，只声称记录本地哈希及通过文件格式／ZIP成员CRC检查。

Spawning Tool的[robots.txt](https://lotv.spawningtool.com/robots.txt)排除了`/*/download/`与`/zip/`，本轮未批量请求这些接口，改用其主动公布的主办方原始包。**不能宣称6,876条目录记录全部下载或全部被赛事包覆盖。** DSCL National Finals与Copa America 2016链接实测404；HSC29的Gerald vs Vaeda文件夹为空。其余未逐项验证的历史外链也不计作“已下载”。网站目录与包内录像尚未建立逐场ID映射。

## 数据处理与验收边界

原始字节保留在`downloads/public-replays/`，不整体解压，不上传GitHub。ZIP内录像逐个读取；SHA-256相同的内容只解析一次，保留全部来源位置。部分`.SC2Replay`实际是AppleDouble元数据，根据[RFC 1740文件标识](https://www.rfc-editor.org/rfc/rfc1740)单独计数，避免将其误算成比赛或引擎故障。

引擎按`BaseBuild + DataBuild + DataVersion`分组，同时保留录像GameVersion。录像头与内嵌元数据相互核对；不以日期、补丁名称或相邻版本替代精确身份。未知精确s2protocol只标记为缺失，不回退解析；版本头可读不代表原生引擎可重放。

TvT候选要求恰好两个玩家实际种族为Terran，包含Random最终成为Terran的情况；精确details可解码时另核查双方为用户控制且分属不同队伍。两分钟阈值按Faster的22.4 loops/s换算，仅用于质量统计，不自动删除正常短局。原始包可能包含其他种族、练习、自定义规则或其他异常，后续仍需筛选。

字节去重不等于同场比赛去重。已保留initData哈希作为线索，尚未验收双方另存、截断副本和跨来源比赛分组；没有划分训练／验证／测试集。没有新增玩家观测重建、动作前对齐或训练就绪样本的验收。

## 工具与本机证据

使用现有Python 3.11与M2依赖，无新依赖。下载工具支持大小／MD5验证、保留部分下载、校验Range续传偏移；下载批次按最坏体积预留空间，使用80 GB项目预警与20 GB空闲空间保护，低于用户100 GB硬限制。

```powershell
# 使用本轮保存的固定清单，可重用已校验文件
.\.venv\Scripts\python.exe -m scv_star.data.public_download --manifest artifacts/runs/m2-public-001/zenodo-manifest.json --output downloads/public-replays/sc2reset-2.0 --report artifacts/runs/m2-public-001/zenodo-downloads.json --workers 3
# 增量扫描完整ZIP／录像；.part不进入清单
.\.venv\Scripts\python.exe -m scv_star.data.public_inventory downloads/public-replays downloads/m2-samples --database artifacts/runs/m2-public-001/inventory.sqlite --report artifacts/runs/m2-public-001/inventory.json
```

`artifacts/runs/m2-public-001/`保存发布清单、下载结果、目录快照、来源映射、SQLite逐文件清单和失败记录；这些可能含玩家信息及本机路径，继续由Git忽略。脱敏后的最终统计与引擎清单另列于本页及配套JSON，不将采集数量当作训练能力验收。

另保存[可迁移下载清单](experiments/M2_PUBLIC_DOWNLOAD_MANIFEST_2026-09-30.json)，仅含公开文件名、静态来源URL、大小及校验值，没有本机路径或玩家名称。`checksum_source`区分发布方MD5与本轮首次获取后记录的校验值。新机器可将此JSON作为下载工具的`--manifest`；本机继续用原分组清单和目录续传，避免重复占用空间。清单不包含之前的三场Spawning Tool逐场样本。

## 最终统计（本轮选定清单已完成）

SC2ReSet的71个赛事包与3个说明文件已全部取得（含复用的IEM包）；新增补充7个赛事ZIP和5个公开赛事文件夹中的726个录像文件。Spawning Tool目录只完成索引，范围限制见上文。

共扫描26,644个后缀为`.SC2Replay`的来源条目，SHA-256去重后保留**25,814个有效录像文件、1,848个TvT候选**；另单列33个不同AppleDouble元数据文件。字节重复条目797个；该重复数包含元数据文件重复，不能解释为重复比赛数。有效录像头解析失败与身份冲突均为0。

| 来源范围 | 去重有效录像（各族） | TvT候选 |
| --- | ---: | ---: |
| sc2reset | 23,675 | 1,749 |
| spawning_public_sources | 2,586 | 126 |

各来源分别去重，其间仍可能重叠，不能相加替代联合去重。2个initData哈希组包含多个不同TvT文件，尚不据此自动合并比赛。

全种族录像涉及**53个BaseBuild、57种精确版本组合**；TvT涉及**46个BaseBuild、46种组合**。暂停91115后，还剩**1,719个TvT候选，涉及45个BaseBuild、45种组合**。91115的129个TvT候选保留归档，不纳入当前引擎获取优先级。

下表按TvT候选量列出非91115的前18个版本组合；完整DataVersion、其他版本及各项计数见[精确引擎需求JSON](experiments/M2_PUBLIC_ENGINES_2026-09-30.json)。同BaseBuild可能出现多行，不应合并不同DataVersion。

| BaseBuild | DataBuild | 录像GameVersion | TvT候选 | 精确details确认人类1v1 |
| --- | --- | --- | ---: | ---: |
| 84643 | 84643 | 5.0.7.84643 | 205 | 205 |
| 81433 | 81433 | 5.0.2.81433 | 117 | 0 |
| 86383 | 86383 | 5.0.8.86383 | 101 | 101 |
| 87702 | 87702 | 5.0.9.87702 | 88 | 88 |
| 90136 | 90136 | 5.0.11.90136 | 84 | 84 |
| 80188 | 80188 | 4.12.0.80188 | 62 | 0 |
| 75025 | 75025 | 4.9.3.75025 | 56 | 56 |
| 89165 | 89165 | 5.0.10.89165 | 55 | 0 |
| 92174 | 92174 | 5.0.13.92174 | 54 | 54 |
| 49716 | 49957 | 3.10.1.49957 | 53 | 0 |
| 78285 | 78285 | 4.11.4.78285 | 50 | 50 |
| 92440 | 92440 | 5.0.13.92440 | 48 | 48 |
| 72282 | 72282 | 4.8.3.72282 | 47 | 47 |
| 73620 | 73620 | 4.8.6.73620 | 47 | 47 |
| 88500 | 88500 | 5.0.10.88500 | 47 | 47 |
| 76811 | 76811 | 4.10.4.76811 | 44 | 0 |
| 83830 | 83830 | 5.0.6.83830 | 44 | 44 |
| 70154 | 70326 | 4.7.1.70326 | 42 | 42 |

“精确details确认”为0可能表示现有s2protocol缺少该版本，不能据此判定候选全是电脑录像。此表是**引擎需求量，不是引擎可获取性或重放成功率**。仅97563保持此前两场四视角通过记录；本轮没有新增引擎验收。先按覆盖量挑选少数版本做可达性和单场验证，再考虑扩大解码，不为每个版本直接复制完整客户端。

本轮结束时项目约**39.45 GB**，C盘剩余约149.52 GB，均按十进制字节计算。没有生成逐帧训练张量。36项单元测试通过，Ruff格式与静态检查通过；新增检查覆盖下载路径、大小、校验和、续传偏移、元数据文件分类及精确版本分组。AGENTS.md哈希保持不变。
