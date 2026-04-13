# ViT 训练/验证/测试数据集规划（基于 1m 对齐产物）

| 项目 | 内容 |
|---|---|
| 概述 | 在现有 `03/04` 产物基础上，制定可复现的 ViT 语义分割数据集构建方案。 |
| 时间 | 2026-04-09（2026-04-13 dry-run 结果补充） |

## 1. 当前数据状态与结论
基于最新质检文件：
- `data/interim/qc/imagery_label_alignment_report.csv`
- `data/interim/qc/class_pixel_stats.csv`

已确认：
1. 两个基础配对均 `overall_pass=1`（CRS/分辨率/transform/shape/bounds 全一致）。
2. 可用于构建数据集的基础配对：
1. `11block`：`tdop_11block_1m_aligned.tif` + `lumid_11block_1m_aligned.tif`
2. `2swd`：`tdop_2swd_1m_aligned.tif` + `lumid_2swd_1m_aligned.tif`

像元统计（汇总）：
1. 总像元：`146,280,002`
2. 有效标签像元（1~10）：`92,586,844`
3. `unknown/nodata(255)`：`53,693,158`（`36.71%`）

类别分布（在有效像元中）：
1. `LUM_ID=9` 山地/自然地：`54.92%`
2. `LUM_ID=8` 水体：`24.39%`
3. `LUM_ID=1` 建筑：`9.94%`
4. `LUM_ID=7` 鱼塘：`3.45%`
5. `LUM_ID=10` 红树林：`2.65%`
6. `LUM_ID=3` 工业：`1.50%`
7. `LUM_ID=2` 商业：`1.31%`
8. `LUM_ID=5` 基础设施/公共服务：`1.24%`
9. `LUM_ID=4` 交通：`0.55%`
10. `LUM_ID=6` 农业：`0.04%`（极少）


结论：
1. 数据已具备进入深度学习数据集制作阶段的条件。
2. 需要重点处理两类问题：  
`(a)` 空白/未知像元占比高；`(b)` 类别极不平衡（特别是 6、4、2、3、5 类）。

## 2. 数据集构建目标
1. 形成可复现实验输入：`train / val / test_in_domain / test_eco_holdout`。
2. 避免空间泄漏（不能随机打散相邻像元到不同集合）。
3. 对稀有类提供足够训练样本，避免模型只学到山地和水体。
4. 利用 `2swd` 的生态特征补足鱼塘/红树林训练样本，同时保留独立生态留出测试区。
5. 本阶段不做“纯跨域对照实验”，优先构建可直接用于训练迭代的实用数据集。
6. 本阶段以“当前 AOI（11block + 2swd）可用规模”为约束，样本数量闸门按 AOI 版本单独设定，不沿用全港阶段目标。

## 3. 推荐划分策略（主方案）

## 3.1 空间划分原则
1. 严禁按像元随机分割。
2. 按空间块（block/grid）划分，再做切片，确保 train/val/test 空间隔离。
3. 任意两个不同 split 的边界之间引入缓冲带，避免空间泄漏。

## 3.2 `11block` 分区方案
1. `11block` 作为主训练域：
1. `train_11block`: 约 `70%` 空间块
2. `val_11block`: 约 `15%` 空间块
3. `test_in_domain`: 约 `15%` 空间块

## 3.3 `2swd` 纵向切分方案（按你确认的方案）
1. 在 `2swd` 范围上按 x 方向纵向切分：
1. 设 `x_split = xmin_2swd + 0.30 * (xmax_2swd - xmin_2swd)`。
2. `x <= x_split` 为左侧 `30%` 区域。
3. `x > x_split` 为右侧 `70%` 区域。
2. 左侧 `30%` 用于验证与测试：
1. 左侧再二分为 `val_eco` 与 `test_eco_holdout`（面积约 `15% + 15%`）。
2. 二分方向优先用 y 方向（上下切），减少与主切分线平行造成的相邻干扰。
3. 右侧 `70%` 作为 `train_eco_support`，并入训练集。

## 3.4 缓冲带与泄漏控制
1. 在每条 split 分界线两侧建立缓冲带，规划值为 `512m`。
2. 基于 2026-04-13 dry-run 实测，当前 AOI 的 `v1` 执行参数采用 `256m` 缓冲带（可通过当前阶段闸门）。
3. 落入缓冲带的 patch 全部丢弃，不进入任何 split。
4. 对重叠切片策略额外做“窗口去重检查”，确保跨 split 无重复窗口。

## 3.5 最终 split 组成（v1）
1. `train` = `train_11block` + `train_eco_support(2swd右70%)`
2. `val` = `val_11block` + `val_eco(2swd左侧子区)`
3. `test_in_domain` = `11block` 留出测试区
4. `test_eco_holdout` = `2swd` 左侧留出测试子区

## 3.6 命名与实验边界
1. 原 `test_cross_region` 在本方案中更名为 `test_eco_holdout`，因为 `2swd` 已部分参与训练。
2. 本阶段不追加“纯跨域对照实验”分支，避免额外实验成本。

## 4. 切片规范（ViT 输入）

## 4.1 Patch 参数建议
1. 分辨率：`1m`（保持当前）
2. Patch 尺寸：`512 x 512`
3. `v1` 执行参数（2026-04-13 冻结）：
1. `train stride = 128`
2. `eval stride (11block) = 128`
3. `eval stride (2swd 左侧) = 64`
4. `buffer = 256m`
4. 若后续扩展 AOI 后样本量显著提升，可回退到更保守的 `eval stride` 与更宽缓冲带进行对照。

## 4.2 有效性筛选规则
对每个 patch 计算：
1. `valid_ratio = (label != 255) / patch_pixels`
2. `unknown_ratio = (label == 255) / patch_pixels`

建议阈值：
1. `train`: `valid_ratio >= 0.2`（或等价 `unknown_ratio <= 0.8`）
2. `val/test`: `valid_ratio >= 0.1`（保留更真实场景）
3. 纯空白 patch（`valid_ratio=0`）全部丢弃。
4. `train_eco_support` 建议更严格：`valid_ratio >= 0.3`，减少 `255` 对生态类训练的干扰。

## 4.3 稀有类增强采样
稀有类集合建议：`{2,3,4,5,6,7,10}`。  
采样策略建议：
1. 正常采样基础上，额外保留“包含稀有类像元 >= N（如 200）”的 patch。
2. 训练加载阶段按类别权重或样本重采样（不改标签）。

## 4.4 2026-04-13 dry-run 实测结论
使用脚本：`scripts/05_vit_dataset_dry_run.py`

1. 方案 A（`patch=512, train=128, eval11=256, eval2left=128, buffer=512`）：
1. `train=5045`（通过）
2. `val=49`（未通过）
3. `test_in_domain=72`（未通过）
4. `test_eco_holdout=4`（未通过）
2. 调参后方案（`patch=512, train=128, eval11=128, eval2left=64, buffer=256`）：
1. `train=5264`
2. `val=516`
3. `test_in_domain=519`
4. `test_eco_holdout=72`
3. 结论：调参后方案满足当前 AOI 阶段闸门（`gate_pass=1`），作为 `v1` 生产切片参数。

## 5. 标签语义与训练约定
1. 标签值保持 `1~10` 与 `255` 不变。
2. 训练时将 `255` 作为 `ignore_index`。
3. 不建议在数据准备阶段把 `255` 强行映射到某个有效类。

## 6. 数据组织结构（建议）
建议新建版本化目录：
`data/processed/vit_dataset/v1/`

包含：
1. `images/train|val|test_in_domain|test_eco_holdout/*.tif`
2. `labels/train|val|test_in_domain|test_eco_holdout/*.tif`
3. `manifests/tiles_manifest.csv`（每个 tile 的来源、分区、坐标窗口、类占比）
4. `manifests/split_summary.csv`（每个 split 的样本数、类别像元统计）
5. `manifests/eco_split_geometry.csv`（`2swd` 切分线、左右子区、缓冲带参数）
6. `manifests/eco_split_class_stats.csv`（`train_eco_support/val_eco/test_eco_holdout` 类别统计）
7. `README.md`（参数、阈值、时间戳、输入来源版本）

命名建议：
`{pair}_{split}_{row}_{col}.tif`  
示例：`11block_train_r023_c117.tif`

## 7. 质控与验收标准

## 7.1 切片前
1. 再次确认 `alignment_report` 中所有基础配对 `overall_pass=1`。
2. 确认标签 palette 已写入（便于人工抽查）。

## 7.2 切片后
1. 每个 split 至少包含主要类别（1/8/9）；
2. `train` 必须覆盖 `7` 与 `10`；
3. `val` 中应包含 `7` 与 `10` 的有效样本（来自 `val_eco`）；
4. 若 `6` 农业在 `train` 样本过少，需单独记录“极低样本风险”；
5. 检查是否有重复 tile（同源同窗口）跨 split；
6. 每个 split 输出类别像元统计与 unknown 比例统计。

## 7.3 训练前最终闸门
1. 当前 AOI（`11block + 2swd`）`v1` 最低闸门：
1. `train >= 2k`
2. `val >= 200`
3. `test_in_domain >= 200`
4. `test_eco_holdout >= 30`
2. 当前 AOI（`11block + 2swd`）`v1` 推荐目标：
1. `train >= 4k`
2. `val >= 400`
3. `test_in_domain >= 300`
4. `test_eco_holdout >= 50`
3. 旧目标 `train >= 8k`、`val >= 1k` 调整为“全港扩展阶段参考目标”，不作为当前 AOI 阶段硬闸门。
4. 关键类别（7/10）在 `val` 与 `test_eco_holdout` 的 IoU/F1 可稳定计算（样本非零且统计波动可接受）。

## 7.4 评估口径补充（验证/测试步长不一致）
当前 `v1` 中 `eval_stride_11block` 与 `eval_stride_2swd_left` 不一致，允许执行，但需统一结果解释口径：

1. 验证集至少同时报告三组指标：`val_11block`、`val_eco`、`val_total`。
2. `val_total` 优先采用像元级全局汇总（基于整体混淆矩阵）计算 IoU/F1，不采用简单 patch 均值。
3. 最终模型结论优先基于两个测试集分别汇报：`test_in_domain` 与 `test_eco_holdout`，避免单一合并指标掩盖域差异。
4. 若后续需要做严格对照实验，可追加“统一 eval stride”的对照组用于敏感性分析。

## 8. 实施步骤（下一阶段）
1. 固化 `11block` 网格划分与 split 映射表（70/15/15）。
2. 在 `2swd` 上计算纵向切分线 `x_split`，生成左右子区。
3. 对左侧 `30%` 再切分为 `val_eco` 与 `test_eco_holdout`。
4. 生成所有分界线缓冲带（`v1` 采用 `256m`），并从候选窗口中排除。
5. 运行 `dry-run`（不落盘）并检查闸门：
1. `python scripts/05_vit_dataset_dry_run.py --output-dir data/processed/vit_dataset/v1/manifests_dryrun_tuned_b256 --train-stride 128 --eval-stride-11block 128 --eval-stride-2swd-left 64 --buffer-m 256`
2. 检查 `split_summary.csv` 中 `gate_pass` 列。
6. 按清单导出影像/标签 patch，写入 `tiles_manifest.csv`。
7. 输出 split 汇总、生态分区几何参数、生态子集统计。
8. 抽样可视化检查并锁定 `v1` 数据集，进入 ViT 训练。

## 9. 风险与应对
1. 风险：`255` 比例偏高导致有效监督不足。  
应对：提高 `train` 有效像元阈值、减少纯背景 patch。
2. 风险：类别极不平衡导致小类学习失败。  
应对：`2swd` 右侧并入训练 + 稀有类优先采样 + loss 加权（后续训练阶段处理）。
3. 风险：空间泄漏导致验证分数虚高。  
应对：切分缓冲带 + 先分区后切片 + 重复窗口检查。
4. 风险：`2swd` 生态子区切分后，`val_eco` 或 `test_eco_holdout` 有效样本不足。  
应对：在不改变左30/右70原则下，微调左侧内部二分位置，保证样本数量与类别覆盖。

## 10. 本文边界
1. 已完成 `dry-run` 自动化脚本：`scripts/05_vit_dataset_dry_run.py`（仅统计，不落盘 patch）。
2. 下一步实现“落盘切片脚本”，将 `dry-run` 清单转换为 `images/labels` 实体 patch。
