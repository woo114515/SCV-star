# M2 三来源小样本验证

2026-09-30：已完成本轮来源验证；**M2 整体验收尚未完成**。当前国服录像通过重放，两个公开来源通过下载和事件解析。后续已取得官方97563引擎并通过两场外部录像重放，详见[97563实测](M2_INTERNATIONAL_97563.md)；91115和97364仍未通过。未开始训练或大规模录像下载。下文保留首轮来源调查的数据和失败记录。

## 结果

| 来源 | 本轮范围 | 元数据／事件解析 | 匹配引擎重放 |
| --- | --- | --- | --- |
| 本机国服账号录像 | 只读扫描997个文件；278个双人实际TvT候选；当前97579版本20个，取最近10个 | 997个元数据可读；官方s2protocol缺少这些国服build的精确协议，未擅自回退 | 10场、20个玩家视角全部通过；API确认双方为人族玩家 |
| SC2ReSet IEM Katowice 2024 | 下载一个88,978,891字节ZIP；423场中54场TvT；仅提取10场 | 10/10完整解析game／tracker事件流 | 所需Base91115未安装；未验证合法玩家观测重建 |
| Spawning Tool | 下载90542、91121、91200三场，共248,912字节 | 3/3确认为TvT，精确协议事件解析通过 | 97364缺失；已有97563文件不完整，启动失败；0场通过引擎重放 |

“TvT候选”仅表示两个实际种族为Terran的玩家记录，不自动代表人类天梯、质量合格或可用于训练。选Random后实际为Terran的对局纳入TvT。公开样本的精确协议details均为两个不同队伍的用户控制玩家；本机抽样另由原生API确认Participant身份。

用户提供的通用 `Documents/StarCraft II/Replays/Multiplayer` 目录有78个文件，主要为Mass Recall战役和M0测试。实际账号录像在 `Documents/StarCraft II/Accounts/<账号>/<角色>/Replays/Multiplayer`。后者总计108,333,241字节，保留原位置，没有复制或修改。账户、玩家名称和完整路径只保存在Git忽略的本地报告。

## 版本不能按日期或补丁名称混用

| 样本 | 录像GameVersion | BaseBuild | DataVersion |
| --- | --- | --- | --- |
| 本机当前国服 | 5.0.15.97579 | 97579 | CA45995A39D1BC3967B2AB29FA1C0A73 |
| IEM 2024 | 5.0.12.91115 | 91115 | 7857A76754FEB47C823D18993C476BF0 |
| Spawning 90542 | 5.0.16.97364 | 97364 | 9305DCF6E5680FFEBD8B679EF05C947F |
| Spawning 91121／91200 | 5.0.16.97563 | 97563 | F364D7C8BB1A0444ABC9BEE547B3FBB3 |

国服战网发布标签仍是5.0.16.97579；上表保留录像/API实际返回的5.0.15.97579。文件修改时间仅用于选取近期样本，不能当作比赛日期或版本证据。本机候选跨多个旧build，甚至含`0.0.95740`标签，暂不把它们混入当前版本桶。

新版、旧版和国服／国际版轨迹分别保存版本身份。跨版本监督学习的效果仍待受控实验；本轮没有训练结果支持“混合无影响”。后续比较当前版单独训练、旧版预训练后当前版微调及带版本条件的混合训练，固定数据划分和样本预算；不重写历史价格、时间或录像头以伪造新版轨迹。

## 本机重放证据与边界

固定M0验证过的97579引擎及二进制SHA-256，使用录像原始地图依赖。10场覆盖Lockdown、Rorschach、At Eternity's Edge、Sanctuary III、Fear and Faith五张LE地图。API报告19个非空MMR值，范围2551–2807；另一个玩家MMR缺失，不补造。用户说明其水平为黄金，本轮不从跨地区MMR强推段位。

每场分别指定玩家1、2，保留战争迷雾，关闭额外隐身显示及score接口；初始均为9个己方单位、0个可见敌方单位，并有非空visibility层。20个视角均取得终局结果；原始文件SHA-256保持不变，客户端正常退出。汇总复核发现3场的终局观测早于录像头末帧18、23、44 loops，两侧差值相同；其余7场末帧一致。保留这一差异，不能把粗步进的终局返回称为全部逐帧对齐。后续精确解码需继续检查尾帧及API终局语义。

追加同批10场／20视角复查，所有终局胜负均与ReplayInfo一致，尾帧差值复现。复查总耗时272.156秒，其中视角加载及重放261.392秒，RAW命令计数与首轮相同。报告为`local-results-check/report.json`，SHA-256：`4600b8f3a6ef43b8431bf0240ead39670253f87b541b379390ef8960aa965fc7`；其中保存最终源码、依赖及锁文件哈希。此次追加复查仅处理发现的尾帧疑点，没有扩充样本。

首轮10场粗步进112 loops，累计2,090次观测、11,210条RAW单位命令，重放部分耗时270.296秒。该时间包含每个视角加载与退出，不含部署、下载和开发；不是训练采样吞吐。没有导出逐帧大张量。返回动作含game_loop，但**动作前观测对齐、同loop多动作、遗漏率和动作合法性掩码尚未验收**。

这10场包括3场极短对局（33、59、631 loops，按Faster速度约1.5、2.6、28.2秒），其余7场超过4分钟。全部20个当前版候选中，17场超过2分钟，另3场即上述极短对局。极短对局可验证加载、终局和失败路径，不应与正常对局等权进入BC。本轮没有据此删除录像；后续训练按持续时间、有效动作和异常原因分层，不能一概删除正常短局或只保留胜者。

997个本机文件和423个赛事文件均未发现字节级重复。所选样本initData哈希也互异，但这一分组只是线索；尚未证明能识别同场双方保存、截断副本及跨来源重复。因此还没有生成训练／验证／测试划分，也不把20个视角计作20场独立比赛。

## 失败与下一步

本机Base97563的`SC2_x64.exe`仅28,114,944字节，其PE节区声明需要至少64,993,280字节。原样启动返回WinError 193。没有覆盖、修补或补下载它，也没有修改游戏安装。其不完整性足以解释此次启动失败，不能据此断言国服不能重放所有国际录像。

推荐优先用已通过的10场当前国服TvT验证动作前观测对齐、TvT动作语义和去重分组，再考虑扩大样本。现有当前版仅20个TvT候选，不足100场；旧版278个候选不能直接当作当前版数据补足。

继续公开来源的完整重放，需要取得对应Base91115／97364／97563及数据、地图。是否必须另装完整国际客户端、能否只补齐历史引擎及下载占用尚未确认；按用户要求，实际下载前先说明方案。没有证据时不以97579强行重放。所有来源通过各自的小样本门槛后才扩大下载。

当前项目逻辑文件占用约6.28 GB，远低于100 GB上限；其中本轮录像下载约89.23 MB，只有10场赛事录像额外解压。逻辑大小会重复计算虚拟环境／缓存硬链接；游戏外部缓存净增量未单独计量，不伪报为零。

## 重现命令与报告

在既有Python 3.11.16、M0环境中安装轻量解析依赖；不安装或启动训练：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'cache/uv'
.\downloads\uv-0.12.20\uv.exe pip install --python .venv/Scripts/python.exe --require-hashes -r requirements/m2-replays.txt
# 只读扫描指定目录；输出包含私有路径，应留在artifacts内
.\.venv\Scripts\python.exe -m scv_star.data.replay_sources <录像目录> --output artifacts/runs/<新编号>/inventory.json
# input为本地JSON路径数组，最多20个；每次使用新的output目录
.\.venv\Scripts\python.exe -m scv_star.runtime.replay_probe --input <路径数组.json> --output artifacts/runs/<新编号>/probe
```

扫描命令可加`--events`解析精确build支持的事件；缺少精确协议时明确报告，不使用邻近协议替代。重放入口核对录像头、元数据、引擎Ping与ReplayInfo身份；粗步进仅用于可行性验证，输出明确标记`bc_alignment_verified=false`。开始前沿用项目80 GB预警与C盘20 GB可用空间检查，给100 GB硬限制留余量。

本轮私有证据位于 `artifacts/runs/m2-sources-001/`：`local-inventory.json`、`sc2reset-inventory.json`、`spawning-inventory.json`、`local-ten-probe/report.json`、`spawning-97563-probe/report.json`及`existing-97563-binary-audit.json`。下载来源、大小与哈希在 `downloads/m2-samples/provenance.json`；这些实体文件不上传GitHub。

代码验证：24项unittest通过，Ruff格式／静态检查与依赖一致性检查通过。包括新增的错误胜者拒绝、尾帧差异显式保留测试；未把单元测试等同于精确轨迹验收。AGENTS.md保持原文。

赛事包MD5为`400c9aa59b4d960a1df28faa3dcf3b62`，与发布页一致；SHA-256为`a0bec492008565f9c24c0418fdccd17db04671d7f289d41a244fee82614e1af5`。赛事样本按ZIP成员顺序选取首10个TvT，只用于技术验证，不宣称随机代表全库。采集时单包上限100 MB、单场5 MB，没有递归批量下载。

## 原始依据

- [SC2ReSet 2.0原始发布](https://zenodo.org/records/14963356)：IEM 2024样本包及发布校验值；发布日期不等于比赛日期。
- Spawning Tool原始下载页：[90542](https://lotv.spawningtool.com/90542/)、[91121](https://lotv.spawningtool.com/91121/)、[91200](https://lotv.spawningtool.com/91200/)。版本结论来自已下载录像内部数据。
- [暴雪s2protocol源码](https://github.com/Blizzard/s2protocol)：元数据／事件解析器，不生成完整玩家观测。
- [暴雪SC2 API协议](https://github.com/Blizzard/s2client-proto/blob/master/docs/protocol.md)：精确binary/data版本、原始地图、玩家重放接口及动作转换误差说明。
- 录像和地图不因解析工具使用MIT许可而取得再分发授权，参见[项目许可整理](SC2_LICENSE_REQUIREMENTS.md)。
