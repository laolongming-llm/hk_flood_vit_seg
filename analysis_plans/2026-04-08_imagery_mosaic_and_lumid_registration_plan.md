# 已下载 TDOP 影像拼接、下采样与 LUM_ID 配准方案

| 项目 | 内容 |
|---|---|
| 概述 | 13 幅 TDOP 影像按两个连通块分开处理（主块 12 幅 + 离散块 2SWD），不使用 VRT；先分别拼接，再将影像下采样到 1m，并严格对齐 `LUM_ID 1m` 网格，确保像素一一对应。 |
| 时间 | 2026-04-08 |

## 1. 当前数据事实（已核对）
1. 影像位置：`data/raw/imagery/TDOP_TIFF_*/*.tif`，共 13 幅。
2. 影像分辨率：统一为 `0.25m`，3 波段 `uint8`。
3. 连通关系：`11*` 12 幅为空间主块，`2SWD` 为离散块。
4. 标签现状：`LUM_ID` 已有 `1m` 栅格（HK1980 网格），适合作为训练阶段统一标签基准。

## 2. 核心判断
1. 不能把 13 幅直接做成一个实体大图。
原因：`2SWD` 与主块相隔，强行实体融合会引入大面积空白像元，造成存储与 IO 浪费。
2. 训练用“影像-标签”应统一到 `1m`。
原因：你当前标签是 `1m`，将影像下采样到 `1m` 比把标签升采样到 `0.25m` 更稳妥。
3. 下采样后必须锁定到标签网格。
原因：仅分辨率一致还不够，`transform` 和像元起点不一致也会导致错位。
4. 影像 CRS 需显式标准化为 `EPSG:2326`。
原因：当前读取为 `LOCAL_CS`，坐标值虽匹配 HK1980，但缺标准 EPSG 标识。

## 3. 推荐技术路线（先规划，后实施）
1. CRS 标准化。
动作：为 13 幅影像写入/确认 `EPSG:2326`，保持原像元值不变。
2. 按连通块拼接影像（保留原始 0.25m）。
动作：
1. 生成主块拼接（12 幅）。
2. 生成 `2SWD` 独立拼接（1 幅可直用或单图输出）。
3. 影像统一下采样到 1m，并以 `LUM_ID 1m` 网格为参考对齐。
动作：
1. 影像重采样固定使用 `average`（4x4 聚合）。
2. 输出影像的 `CRS/transform/分辨率/宽高` 必须与对应标签完全一致。
4. 标签处理。
动作：
1. 优先直接裁剪现有 `LUM_ID 1m`（不改值）。
2. 若范围不同，按参考网格窗口导出对应标签块，保持最近邻语义。
5. AOI 导出。
动作：按你已选 AOI（九龙主区 + 尖鼻咀）分别导出影像/标签对，减少无关区域。

## 4. 具体产物规划
1. 0.25m 拼接影像（归档层）：
1. `data/interim/imagery_mosaic/tdop_11block_0p25m.tif`
2. `data/interim/imagery_mosaic/tdop_2swd_0p25m.tif`
2. 1m 对齐影像（训练输入层）：
1. `data/interim/imagery_1m_aligned/tdop_11block_1m_aligned.tif`
2. `data/interim/imagery_1m_aligned/tdop_2swd_1m_aligned.tif`
3. 1m 对齐标签（训练标签层）：
1. `data/interim/labels_1m_aligned/lumid_11block_1m_aligned.tif`
2. `data/interim/labels_1m_aligned/lumid_2swd_1m_aligned.tif`
4. 质检产物：
1. `data/interim/qc/imagery_label_alignment_report.csv`
2. `data/interim/qc/class_pixel_stats.csv`

## 5. 离散图幅 `2SWD` 的处理策略
1. 不与主块做单实体融合。
2. 独立参与训练/验证，或作为跨区泛化测试块。
3. 不采用 VRT，保持“主块一套文件 + 离散块一套文件”的双流程管理。
4. 如需统一调度，在清单层维护索引表（CSV/JSON）而非虚拟镶嵌。

## 6. 下采样与配准参数建议
1. 影像下采样：`0.25m -> 1m`，固定使用 `average`。
2. 影像配准：目标网格直接引用 `LUM_ID 1m` 的 `transform + width/height + bounds`。
3. 标签处理：保持类别值不变，涉及重采样时仅可用最近邻。
4. NoData 建议：
1. 影像显式设置 `nodata=0`（或保持无 nodata 但统一掩膜策略）。
2. 标签维持 `nodata=255`。

## 7. 质量控制清单
1. 元数据一致性：每对影像/标签的 `CRS`、`resolution`、`transform`、`shape` 完全一致。
2. 叠加目视：重点查海岸线、道路边缘、鱼塘边界、红树林边缘。
3. 统计检查：确认 `LUM_ID=10`、鱼塘、水体像元数均为非零且空间分布合理。

## 8. 执行顺序建议
1. 标准化影像 CRS。
2. 按连通块做 0.25m 拼接。
3. 以 `LUM_ID 1m` 为参考网格，将影像下采样并对齐到 1m。
4. 裁剪导出 AOI 的影像-标签配对。
5. 生成质检报告与类别统计。

## 9. 下一步落地（你确认后实施）
1. 新增 `scripts/04_prepare_imagery_and_labels.py`。
2. 脚本参数包含：
1. 影像根目录
2. 图幅分组（主块/离散块）
3. 参考标签栅格路径（`LUM_ID 1m`）
4. 影像下采样方法（`average`）
5. AOI 边界路径
6. 输出目录
3. 自动输出配准质检 CSV，确保数据可直接进入 train/val/test 流程。
