# 项目阶段总结（香港地物分类与洪水模拟耦合）

更新时间：2026-04-13

## 1. 当前阶段定位
- 项目已完成 OSM 重分类、LUM_ID 栅格化、TDOP 影像拼接与 1m 对齐，进入 ViT 数据集制作规划阶段。
- 当前重点从“标签构建”转向“训练数据集工程化”（切片、split、防泄漏、类别不平衡控制）。

## 2. 当前脚本状态

### scripts/01_pbf_to_gpkg.py
- 状态：稳定可用。
- 功能：`.osm.pbf -> .gpkg` 主数据转换；导出香港边界和 clean multipolygons。

### scripts/02_reclassify_multipolygons.py
- 状态：V2（规则 + 层级门控）稳定。
- 核心点：10 类体系（含红树林 `LUM_ID=10`）、叶子面优先、强语义非叶子面准入、`unknown=255`。

### scripts/03_rasterize_LUMID_classes.py
- 状态：V2+（烧录策略、性能保护、样式输出）。
- 主要能力：
1. 三层烧录顺序：强语义非叶子面 -> 叶子面 -> 可选 context 补洞。
2. 性能与安全参数：`--max-pixels`、`--allow-huge-raster`、`--gdal-cache-mb`。
3. 新增样式能力：默认给输出标签写入 LUM_ID 色表，并导出同名 `.qml/.clr`（可用 `--no-lumid-style` 关闭）。

### scripts/04_prepare_imagery_and_labels.py
- 状态：新增并已可用（2026-04-09）。
- 功能：
1. 按连通块拼接 TDOP（`11*` 主块 + `2SWD` 离散块），不使用 VRT。
2. 可选补写源影像 CRS 为 `EPSG:2326`。
3. 以 `LUM_ID 1m` 网格为基准，导出 1m 对齐影像与 1m 对齐标签。
4. 输出 QC：配准报告和类别像元统计。
5. 对标签输出写入色表并导出 `.qml/.clr`。

### scripts/lumid_style.py
- 状态：新增并可复用。
- 功能：统一管理 LUM_ID 颜色映射，支持写入栅格色表、导出 QGIS 样式和 CLR 文件。

## 3. 数据与质检现状（截至 2026-04-09）

当前关键产物：
1. `data/interim/imagery_1m_aligned/tdop_11block_1m_aligned.tif`
2. `data/interim/imagery_1m_aligned/tdop_2swd_1m_aligned.tif`
3. `data/interim/labels_1m_aligned/lumid_11block_1m_aligned.tif`
4. `data/interim/labels_1m_aligned/lumid_2swd_1m_aligned.tif`
5. `data/interim/qc/imagery_label_alignment_report.csv`
6. `data/interim/qc/class_pixel_stats.csv`

配准结果（alignment report）：
1. `11block` 和 `2swd` 均 `overall_pass=1`。
2. `crs/resolution/transform/shape/bounds` 全部匹配。

类别统计（class stats，汇总）：
1. 总像元：`146,280,002`
2. 有效像元（1~10）：`92,586,844`
3. `255` 像元：`53,693,158`（约 `36.71%`）
4. 主导类别：`9`（约 `54.92%`）和 `8`（约 `24.39%`）。
5. `2swd` 生态特征显著：鱼塘/红树林/水体占比高；`11block` 以城市+山地为主。

## 4. 已固化关键决策（最新）
1. 标签编码：`1~10` 有效类，`255` 为 unknown/nodata。
2. 红树林保留独立类别：`LUM_ID=10`。
3. ViT 数据集 split 采用：`train / val / test_in_domain / test_eco_holdout`。
4. `2swd` 切分策略：纵向 `30%/70%`，左侧用于 `val_eco + test_eco_holdout`，右侧用于 `train_eco_support`。
5. 空间防泄漏：split 边界两侧设置缓冲带（规划值 `512m`），缓冲带内 patch 丢弃。
6. 本阶段不做纯跨域对照实验，优先构建实用训练数据集。
7. 当前 AOI（`11block + 2swd`）`v1` 验收闸门调整为：`train>=2k`、`val>=200`、`test_in_domain>=200`、`test_eco_holdout>=30`。
8. 旧闸门 `train>=8k`、`val>=1k` 调整为全港扩展阶段参考目标，不作为当前 AOI 阶段硬性要求。

## 5. 环境与已知注意事项
1. `01/03/04` 运行依赖 `osgeo`（GDAL/OGR），建议固定 GIS conda 环境。
2. 普通 Python 环境常见错误：`ModuleNotFoundError: osgeo`。
3. 终端编码偶发噪音不代表脚本失败，以目标文件与 QC 结果为准。

## 6. 下次开工前检查清单
1. 输入数据存在：
1. `data/raw/osm/hong-kong-260330.osm.pbf`
2. `data/raw/imagery/TDOP_TIFF_*/**/*.tif`
2. 中间数据存在：
1. `data/interim/cleaned_vectors/hk_multipolygons_hydro_reclass.gpkg`
2. `data/interim/masks_full/hk_landuse_LUMID.tif`（或准备重跑 03）
3. 环境：
1. GIS 环境 `python` 可用；
2. `import osgeo` 成功。

## 7. 下一阶段（未编码）
1. 按 `analysis_plans/2026-04-09_vit_train_val_test_dataset_planning.md` 实现切片与 split 脚本。
2. 先执行切片 `dry-run`（不落盘），输出并核对各 split 数量与类别统计，必要时按计划文档进行 stride 调整。
3. 输出 `tiles_manifest.csv`、`split_summary.csv`、`eco_split_geometry.csv`、`eco_split_class_stats.csv`。
4. 完成 `v1` 数据集封版后进入 ViT 训练。

## 8. 相关文档
1. `analysis_plans/2026-04-08_imagery_mosaic_and_lumid_registration_plan.md`
2. `analysis_plans/2026-04-09_vit_train_val_test_dataset_planning.md`
3. `analysis_plans/2026-04-08_HK_TDOP_AOI_selection_strategy.md`
4. `plan.md`
5. `GPKG_MULTIPOLYGONS_FIELDS.md`
