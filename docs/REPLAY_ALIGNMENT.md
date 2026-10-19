# M2 观测—动作对齐与训练数据

## 2026-10-19：扩容至1,000场（进行中）

用户授权本轮项目上限150 GB，并要求保留1,000场可用数据、清理前复核。原100场冻结清单保持不变；新数据逐场构建、完整提取、逐条验收，最终由 `tvt-bc-collection-v1` 集合引用。当前训练入口仍指向原100场，只有最终集合和GPU检查通过后才切换。下面首批100场的版本、数量、时长及100 GB限制均属历史记录。

启用历史build为83830、84643、86383、87702、88500、90136、93333、96516、96999、97563、97579。每个版本先验受控SCV动作时序，再验一场双视角完整提取；候选来源、人数种族、胜负、地图和资源哈希须核对。缺失地图仅从暴雪官方depot获取，按录像记录的SHA校验。CN版本读取地图依赖时显式记录97563的details协议回退；游戏回放及训练标签仍由匹配的CN引擎生成，不宣称存在对应的完整事件解码协议。

增量候选固定种子20261019，继承已知初始化指纹的旧split，新组按80/10/10概率固定划分，最终报告实际数量。沿用≥128条/视角、≥98%配对率、同遍标签、逐条逆转换、双方终局等门槛。原始ability ID、DataVersion及各版本技能目录分开保留；未实现跨版本语义映射前，训练读取须按版本形成批次，不把旧规则改写成新轨迹。

```powershell
# 可恢复的全量提取与验收；配置、地图和受控时序须事先通过
.venv\Scripts\python.exe -m scv_star.data.bc_batch --plan artifacts/runs/m2-expand-1000-2026-10-19/plan.json --registry artifacts/runs/m2-expand-1000-2026-10-19/registry.json --destination data/processed/m2-expand-2026-10-19 --progress artifacts/runs/m2-expand-1000-2026-10-19/progress.json --target 1000 --workers 8
# 最终集合发布后验证读取、张量与有限梯度；不进行优化器更新
.venv\Scripts\python.exe -m scv_star.runtime.bc_reader_check --collection data/processed/m2-expand-2026-10-19/collection.json --output artifacts/runs/m2-expand-1000-2026-10-19/gpu-final.json
```

调度以实际内存限制并发：保留6 GB系统余量，按每个新任务1.6 GB估算；低于25 GB空闲磁盘暂停派发，低于20 GB或逼近150 GB项目上限时保存进度并停止。registry设置 `drain: true` 可停止派发、等当前任务完成后退出；重启前须核对没有另一个manager。

读取接口为 `iter_collection_rows(collection_path, split, builds={86383})`。子manifest和集合均禁止覆盖。实际进度与恢复信息见[交接](HANDOFF.md)；预检通过不代表1,000场完成，也不代表正式BC模型已实现。

## 首批100场验收记录

更新：2026-10-12。本页接续[数据收集归档](M2_DATA_COLLECTION.md)。**首批 M2 验收通过**：100 场／200 个视角、285,459 条样本，正式训练清单已发布；没有启动正式 BC／RL。

## 范围与验收门槛

目标为首批 **100 场人类 TvT、200 个玩家视角**，训练／验证／测试按录像划分为 80／10／10。固定历史版本 **5.0.8.86383**、DataVersion `22EAC562CD0C6A31FB2C2C21E3AA3680`；引擎、地图、源码和依赖保留哈希。不混用规则版本，不将旧观测改写成新版轨迹。

从轻筛合格、胜负已知且超过 4,480 loops 的候选中，以种子 `20261008` 固定首批及备用顺序。质量不合格时，按预定顺序补足对应集合，不能根据训练结果挑选测试集。同一录像双方视角归入同一集合。按用户要求不做比赛级去重；另检查已有初始化指纹是否跨集合，但不能保证识别所有跨来源副本。

每个视角须满足：

- 两遍回放到实际终局，双方胜负与录像信息一致；记录额外录制尾部。
- 同遍回放的合法观测与动作配对，至少 128 条样本，策略动作配对率不低于 98%。
- 接受动作经编码及逆转换后与原 API protobuf 完全一致；失败原因计数并隔离。
- 分片哈希、原始录像不变、SC2 退出及存储检查通过。
- 全部分片逐条验收，三个集合可送入 NVIDIA GPU 完成有限值前后向检查；优化器更新次数为 0。

这是首批可开始 BC 的数据门槛，不代表全部 835 个候选通过，也不代表已训练出策略或完成新版部署。

## 时间语义与两遍回放

[暴雪协议](https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/sc2api.proto)返回自上次观察以来的动作及执行 loop；[协议说明](https://github.com/Blizzard/s2client-proto/blob/master/docs/protocol.md#inaccuracies-of-actions)指出录像转 API 动作可能有误差。[DI-star 解码器](https://github.com/opendilab/DI-star/blob/12b1c69350ad41e17895c602a66a52d98dd58452/distar/agent/default/replay_decoder.py)采用先收集动作、再取得对应观测的结构，本项目保留这一思路并实测边界。

97563 和 86383 的受控 SCV 实验中，loop 64 提交生产命令，回放动作时间戳为 64；loop 64 矿物仍为 50、订单为空，loop 65 变为 0 并出现订单。当前采用 `observation_loop = max(action_loop - 2, 0)`，保留两 loop 提前量。这是实测支持的保守契约，未穷尽所有技能时序。

完整录像中，改变观察步长后，API 转换出的目标 tag、甚至命令数可能改变。因此，第一遍只提供候选观察时间；**标签与观测都从第二遍取得**。没有准确提前观测的动作显式隔离，不拼接第一遍标签、不猜测 tag、不取未来状态。两遍动作流是否相等仅作诊断。

## 数据契约与信息边界

schema 为 `tvt-bc-v2`，正式模块为 `src/scv_star/data/bc_schema.py`、`bc_extract.py`、`bc_accept.py`、`bc_dataset.py`。

- RAW `unit_command` 和 `toggle_autocast` 均保留；镜头动作计数但不作为 RAW 策略输出。标签保留原 ability ID、顺序化选中索引、队列标记及单位／坐标目标。同 loop 多命令不去重。
- 双方分别使用自己的合法观测，保留战争迷雾，不开额外隐形／埋地显示。策略输入不含胜负、分数、聊天和返回动作。
- 保存己方资源、实体、生产订单、附件关联、升级、载员、迷雾与菌毯。移除非己方订单、载员和附件信息；历史快照动态战斗数值置零。
- 感应塔雷达点可能没有 tag，多个点均为默认 tag 0。它们单独保存为位置型 `blips`，不虚构单位身份、不进入可寻址实体表。重复非零 tag 仍拒绝。
- 共享地图输入仅含尺寸、地形高度、通行／建造网格及可玩区域；排除可能随玩家视角变化的 `start_locations`。己方出生位置来自己方单位。
- 相邻有效动作保留延迟；隔离动作导致延迟掩码关闭并断开序列。有效零延迟与缺失标签区分。

结构检查及逆转换通过，不等于完整游戏规则合法性掩码。起降、附件互换、技能条件、建造位置等在线合法性与闭环执行仍属于 M3。这里保留完整 RAW 能力及参数，不用脚本宏观动作替代 TvT 目标。

首批使用简化特征白名单，并非逐项复现 DI-star：尚未编码 RAW effects 层、完整护甲／武器属性等。保留这些限制以便解释首轮 BC 表现；扩展特征需要升级 schema 并重新解码，不能从现有分片虚构缺失信息。API 返回的 action errors 单独统计，目前不把无法明确对应时间的错误强行匹配到某条标签；BC 标签表达玩家命令尝试，不保证每次尝试成功执行。

## 文件与运行入口

原始收集快照不改写。候选计划、边界实验及失败诊断在本地 `artifacts/runs/m2-readiness-2026-10-08/`；一次性脚本已归档至 `artifacts/archives/2026-10-12-m2-readiness-scripts/`。冻结训练产物为 `data/processed/m2-bc-2026-10-08/86383-v3/`，早期失败实验不能用于训练。

每场保存双方压缩 JSONL、隔离记录、地图静态数据、API 单位／技能数据和报告。`manifest.json` 只由完整分片验收生成；GPU 检查通过后再发布 `data/manifests/training-current.json`，指向可用训练快照。

```powershell
# 单场完整提取；output 必须是新的 data/processed 子目录
.venv\Scripts\python.exe -m scv_star.data.bc_extract --replay <录像路径> --engine configs/engines/sc2-international-86383.json --local configs/local/m2-86383.json --split train --output data/processed/<新目录>
# 整批验收；不能覆盖已有 manifest.json
.venv\Scripts\python.exe -m scv_star.data.bc_accept --root data/processed/m2-bc-2026-10-08/86383-v3 --selection artifacts/runs/m2-readiness-2026-10-08/selection-86383-final.json --boundary artifacts/runs/m2-readiness-2026-10-08/boundary-86383/report.json --minimum-files 100
```

读取接口为 `iter_rows(manifest_path, split)`、`iter_sequences(..., length=64)`、`collate(rows, manifest["ability_ids"])`。不截断实体，保留选中索引顺序和条件掩码。核心张量可送入 PyTorch；订单、升级、载员、地图等结构字段留给具体网络编码器。简易 GPU 检查不代表完整策略网络已实现；归一化参数只能从训练集拟合。

## 环境、预算与限制

Windows 客户端隐藏窗口启动，无需人工点击；仍会初始化图形。先用三个进程测量资源余量，再提高到六个并行提取，不主动下载新资源。每场输出上限 100 MB，启动前项目不得超过 99 GB，C 盘至少保留 20 GB，整个项目继续遵守 100 GB 上限。已回收可重新生成的 `cache/uv` 包缓存，保留安装环境、原始录像、下载包、地图和引擎。

完成且关闭的分片由 `bc_repack.py` 无损重新压缩，逐字节验证解压后 SHA-256 不变，保留原／新压缩哈希及恢复记录。先验证gzip压缩，再使用标准库XZ（preset 3）降低重复观测占用；`bc_storage.py`统一读取两种格式，不新增依赖。发布训练 manifest 后禁止重压缩或改写。早期失败、被替代的分片清理前记录哈希，保留报告与隔离证据。

旧录像目录的跨执行身份访问差异已解决：保存原 ACL，只给当前游戏运行用户补充读取权限。97563 短片段对照随后通过。原国服 97579 本轮无法再次启动，安装目录出现更高 build；尚未证明确切失败原因，也没有更新游戏。历史 M0 证据保留，最终最新版 RL／真人部署前必须重新固定正式版本并验收。

首批单版本数据用于流程复现，不能承诺对战水平。增量数据应独立构建快照，比较质量及采样收益。Spawning Tool 须署名、回传最终成果且不得再分发；原始和派生数据、玩家详情及逐场清单只留本机，GitHub 只保存代码、文档和脱敏汇总。

## 历史证据

2026-10-07：国服 97579 一场短片段双方视角、步长 1／8 对照，82 条命令、164 个候选配对通过序列化检查，当时尚未验证动作前状态边界。见[首轮摘要](experiments/ALIGNMENT_PILOT_2026-10-07.json)。原报告空间扫描漏读问题已修复，历史不完整数值不可用于预算判断。

2026-10-08：97563 短片段对照通过；97563／86383 控制实验确认生产边界；完整录像发现跨遍动作转换差异及无 tag 雷达点，已据此修正契约。10月12日恢复并完成最终验收，结果如下。

2026-10-12：用户重新安装国服至 `C:\Program Files (x86)\StarCraft II`，本机安装清单为 `5.0.17.98370`，新安装 API 尚未复验。独立 86383 引擎通过启动与身份复核，旧版数据提取未改用新国服引擎。


## 本轮最终结果

100 场／200 视角，20 个地图名称，来源计数为 {'SC2ReSet': 62, 'SpawningTool_Kevin_export': 38}。训练／验证／测试分别为80／10／10场，样本条数见[脱敏汇总](experiments/M2_READY_2026-10-12.json)。最低单视角配对率 98.7805%，总体配对率 99.7920%；未配对动作隔离，不参与监督学习。

观察到 156 种能力ID、38 条自动施法切换、3,427 个同loop多命令组；队列、附件、载员和雷达特征统计见汇总。不能据此宣称所有TvT技能、合法性条件都已覆盖。

63 项单元测试通过，Ruff检查通过；格式检查覆盖已跟踪代码及本次新增模块，既有未跟踪manual_replay.py的格式问题保留未改。训练／验证／测试及最大实体观测均通过NVIDIA GPU数据读取与小型能力头前后向检查，优化器更新0次。没有为消除NumPy缺失提示而额外安装依赖；本轮纯PyTorch张量路径通过。

项目逻辑文件约 97.54 GB（不含已有客户端和项目外暴雪共享缓存）。本轮部署调试、录像处理、验收单独记录；正式训练耗时为0。训练入口为本地 `data/manifests/training-current.json`，原始收集快照未改写。
