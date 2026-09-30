# M1：GPU 训练兼容性验证

本阶段使用合成数据、小型随机初始化网络验证本机 GPU 计算和检查点恢复。不会启动 SC2、下载录像或模型，也不运行 BC／RL 正式训练。实测结论见 [M1 记录](experiments/M1_2026-09-30.md)。

## 固定环境与安装

沿用项目内 Python 3.11.16，选用官方 Windows `torch==2.13.0+cu130`，配套 CUDA runtime 13.0。驱动实测为592.01；`nvidia-smi` 显示的 CUDA 13.1 是驱动支持信息，不代表已安装同版本 Toolkit。无需为本次 wheel 验证安装系统 CUDA Toolkit 或更新驱动。

本机另有 Intel 核显。本轮明确通过 CUDA 在 **NVIDIA GeForce RTX 5070 Ti Laptop GPU** 上执行，报告记录12.0计算能力和设备名称；Intel核显未参与。CUDA设备编号属于CUDA枚举，不应与Windows任务管理器中的GPU编号混用。

PyTorch 官网列出该 [Windows/CUDA 13.0 安装组合](https://pytorch.org/get-started/previous-versions/)。它是本轮固定组合，不声称是最新 PyTorch。驱动初筛参考 [NVIDIA CUDA 兼容说明](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)，最终以实际执行结果为准。

安装在已有项目 `.venv` 中，下载缓存使用 `cache/uv`。按约10GB预留安装与缓存空间，安装前确认项目仍低于100GB总上限：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'cache/uv'
.\downloads\uv-0.12.20\uv.exe pip install --python .venv/Scripts/python.exe --index-strategy unsafe-best-match --require-hashes -r requirements/m1-windows-py311-cu130.txt
.\downloads\uv-0.12.20\uv.exe pip install --python .venv/Scripts/python.exe --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m scv_star.runtime.m1 --output artifacts/runs/m1-gpu-next
```

输出目录必须不存在。`--steps` 默认20，限制为2至100；每种精度额外运行两次更新比较恢复结果。已安装入口也可用 `scv-m1`。无 GPU 或固定版本不符时明确失败，不回退 CPU。

上面的索引策略用于在官方 PyTorch/PyPI 两个索引中匹配锁定版本，保留 setuptools 84.0.0；全部依赖仍须满足锁文件哈希。重建锁文件的完整命令保存在锁文件头部。不要并发执行使用同一缓存包的安装和解析，以免等待 uv 包锁。

## 验收内容

1. 记录 GPU、compute capability、wheel 架构列表、驱动、CUDA runtime、cuDNN、依赖和源码哈希。
2. GPU FP32 矩阵乘法与 CPU FP64 参考比较，绝对及相对容差均为 `1e-4`。
3. 使用卷积、实体投影、注意力、LSTM、Dropout 和32维输出头；固定小批次合成输入，分别执行 FP32、FP16 AMP、BF16 AMP。
4. 检查输出、损失、所有参数梯度和更新权重为有限值，权重确实变化；动作掩码排除的选项不能被采样。
5. 保存模型、AdamW、FP16 GradScaler、CPU/CUDA 随机数状态。恢复后逐项检查，再比较下一步损失和完整状态与未中断分支是否逐位一致。
6. 记录首步和后续平均耗时、PyTorch 已分配/保留显存峰值。使用 CUDA 同步计时；包含校验开销，不能冒充正式训练吞吐。

启用确定性算法、固定随机种子和 `CUBLAS_WORKSPACE_CONFIG`，关闭 cuDNN benchmark 与 TF32；检查点仅加载本次生成的文件并使用 `weights_only=True`。这保证的是该设备和固定环境中的本次验证，不承诺跨版本、跨设备逐位复现。实现原则参考 [PyTorch 随机性说明](https://docs.pytorch.org/docs/stable/notes/randomness.html)和[AMP 示例](https://docs.pytorch.org/tutorials/recipes/recipes/amp_recipe.html)。

## 输出与边界

报告和小型检查点位于 `artifacts/runs/<RunId>/`，不提交 Git；失败报告保留。源码、命令与带哈希的依赖锁进入 Git。存储检查在项目逻辑体积达到80GB或分区可用空间低于20GB时拒绝运行。

通过仅代表基础 eager 运算和恢复链路可用。DI-star 完整模型、真实数据、批量大小上限、长时稳定性、`torch.compile`、自定义 CUDA 扩展、多进程采样和 SC2/GPU 同时运行仍需后续验证。

本轮未安装 NumPy，导入 torch 会提示 NumPy 初始化不可用；纯张量检查已通过，NumPy 与 Tensor 的转换未验收，留待数据阶段固定其依赖并验证。
