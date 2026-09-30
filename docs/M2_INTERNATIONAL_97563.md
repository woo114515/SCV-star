# 官方97563独立引擎与外部录像验证

2026-09-30：**两场Spawning Tool TvT、共4个玩家视角重放通过**。官方97563引擎已取得并验证，无需下载安装完整国际客户端。当前国服97579仍是最终执行与RL目标；本记录只证明这一固定国际版本的样本可重放，不代表91115或97364已经可用。

## 固定环境

| 项目 | 本轮实测 |
| --- | --- |
| GameVersion／BaseBuild／DataBuild | `5.0.16.97563`／`97563`／`97563` |
| DataVersion | `F364D7C8BB1A0444ABC9BEE547B3FBB3` |
| Windows引擎SHA-256 | `c86a6dd6a9295f300709d84ce0aa15375f8a345e7f7b36493017d78bd32fe01a` |
| 官方内容MD5 | `b16a1b9203fcc656dd283b41d13c5571` |
| 官方传输对象／解包大小 | 52,694,264／65,003,728字节 |
| 数字签名 | Windows Authenticode：Valid；Blizzard Entertainment, Inc. |
| 依赖 | 延用Python 3.11.16及M0、M2哈希锁；未增加Python依赖 |

引擎清单已固定在 [`configs/engines/sc2-international-97563.json`](../configs/engines/sc2-international-97563.json)。获取过程核对官方BLTE头哈希、992个分块MD5、每块解包长度、最终内容MD5及SHA-256，再进行签名验证。没有执行社区修复程序或修改游戏二进制。

官方来源链：

1. [版本配置](https://us.cdn.blizzard.com/tpr/sc2/config/5b/c8/5bc8dcc1fade3a320c12936586b7c0ed)声明B97563及上述DataVersion。
2. [安装清单对象](https://us.cdn.blizzard.com/tpr/sc2/data/64/e8/64e89df516ed46c2856ac9306a02963c)给出`Versions/Base97563/SC2_x64.exe`的内容哈希和大小。
3. 通过官方内容索引定位[引擎传输对象](https://us.cdn.blizzard.com/tpr/sc2/data/50/bf/50bfe00c0c061d32848d9e02b53ca5b0)。该对象采用BLTE封装，不能直接改名为EXE。

## 隔离目录与资源补齐

Windows引擎直接放在下载目录运行时，报错`e_errorNoArchiveRepairable`，实际在项目根目录寻找`Mods/Core.SC2Mod`。因此不能假定传入`-dataDir`就能将任意位置的Windows二进制变成可用安装。

本轮采用项目内的标准目录：

```text
cache/sc2-international-97563/
  .build.info                  独立的国际97563配置
  Versions/Base97563/SC2_x64.exe
  Support64/                   从既有安装复制必要DLL
  SC2Data/                     从既有安装实际复制的数据
```

复制数据约27.59 GB，耗时13.55秒；不是重新下载完整客户端。使用实际副本，没有把可写数据目录通过硬链接或目录联接指向国服。项目内配置使用官方97563 build key与CDN key，国服原始配置保留。

引擎随后成功连接API，Ping和ReplayInfo身份全部匹配。但首次StartReplay因地图／模块关联内容缺失失败，启用官方`download_data`也未解决该依赖。进一步从录像的`m_cacheHandles`读取精确资源哈希：8项依赖中7项已缓存，缺少的模块为：

`c83c41fd12eecffbda442091df5f98cd25c3e1fe9feea3ea728fb9ed3784d23d.s2ma`

从[暴雪EU资源服务](https://eu-s2-depot.classic.blizzard.com/c83c41fd12eecffbda442091df5f98cd25c3e1fe9feea3ea728fb9ed3784d23d.s2ma)取得746,656字节，SHA-256与录像引用一致。全部8项均核对内容哈希后，仅将缺少的1项添加到Windows共享`ProgramData/Blizzard Entertainment/Battle.net/Cache`；没有覆盖已有缓存。

资源地址规则参考[sc2reader维护者源码](https://github.com/ggtracker/sc2reader/blob/upstream/sc2reader/utils.py)。本轮旧`eu.depot.battle.net`地址连接失败，而`eu-s2-depot.classic.blizzard.com`可用；不因旧地址失效就判断地图已消失。

## 重放结果

| 样本 | 地图 | 双方视角 | 观测总数 | RAW单位命令 | 录像尾帧差值 |
| --- | --- | --- | --- | --- | --- |
| Spawning Tool 91121 | Rainfall LE | 2/2通过 | 192 | 1,296 | 双方均0 loops |
| Spawning Tool 91200 | Washout LE | 2/2通过 | 206 | 1,193 | 双方均2 loops |

步进112 loops，4个视角均读取己方单位和visibility层，开局无可见敌方单位；保留战争迷雾，关闭score、额外隐身显示等选项。终局胜负均与ReplayInfo一致，原始录像哈希不变。客户端正常退出，未强制终止。

正式通过运行共64.984秒，其中各视角加载／重放／退出累计53.609秒。此时间不包含部署、下载、复制数据和失败排查，不是训练时间或正式逐帧解码吞吐。

Washout的2 loops差异原样保留，未伪装成逐帧一致；`bc_alignment_verified=false`。动作前观测、同loop多动作处理、合法性掩码、精确动作覆盖率、跨来源去重及训练划分仍属于后续M2工作。此次通过也不能证明混合版本监督学习的效果。

## 空间及原始安装检查

结束时项目逻辑文件大小约**33.99 GB**，C盘剩余约155.52 GB，未超过100 GB项目预算。新增占用主要是27.59 GB隔离副本；引擎传输包与解包副本也计入统计。

对原游戏安装和共享地图缓存的文件清单进行前后比较：原安装无新增或大小／修改时间变化；共享缓存仅新增上述746,656字节模块。额外用SHA-256核对国服`.build.info`、原97579引擎及原不完整97563文件，三者全部不变。游戏自行生成的用户目录日志等未计入这一清单，不能将此结论扩大为整台电脑无文件变化。

## 复现与证据

已有本轮隔离环境时执行：

```powershell
.\.venv\Scripts\python.exe -m scv_star.runtime.replay_probe `
  --input artifacts/runs/m2-sources-001/spawning-97563-selected.json `
  --engine configs/engines/sc2-international-97563.json `
  --local configs/local/m2-international97563-isolated.json `
  --output artifacts/runs/<新的运行编号>
```

本机配置包含隔离安装目录`install_path`和已固定的`engine_binary`路径，不提交Git。重放工具支持独立引擎路径，继续验证其哈希；Windows仍需上述标准资源布局。可选`--download-replay-data`明确允许游戏补齐历史binary/data，默认关闭；不能保证它会解决所有地图依赖问题。本轮成功运行未启用此选项。

私有证据保存在`artifacts/runs/m2-international-97563-001/`：下载与签名报告、隔离目录记录、依赖哈希、缓存新增项、空间与安装前后比较，以及4次尝试记录。`replay-attempt-4/report.json`为成功报告，SHA-256：`a09abab5ee08bdc2866463a1e80bae239c70be88bebddca0b9b8bceb9934fde3`。报告记录源码及依赖锁哈希。引擎、地图、缓存、私有配置和原始报告不上传GitHub。

官方重放与版本要求见[暴雪SC2 API文档](https://github.com/Blizzard/s2client-proto/blob/master/docs/protocol.md)。后续应先完成精确观测—动作对齐，再扩大同版录像；91115和97364仍须独立验证。
