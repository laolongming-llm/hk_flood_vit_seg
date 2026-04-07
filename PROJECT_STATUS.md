# 项目阶段总结（香港地物分类与洪水模拟耦合）

更新时间：2026-04-07

## 1. 当前阶段定位
- 项目仍处于数据准备与规则迭代阶段，尚未进入 ViT 训练。
- 当前主线已从“脚本可运行”升级到“分类质量与可学习性优化”：
  - 区分细碎面与壳层面（避免大面误覆盖）；
  - 调整高影响规则（parking、mangrove）；
  - 优化栅格化性能与资源保护。

## 2. 当前脚本状态

### scripts/01_pbf_to_gpkg.py
- 状态：稳定可用。
- 功能：`.osm.pbf -> .gpkg` 主数据转换；保留 OSM 关键图层；按 `name='香港 Hong Kong'` 导出行政边界；导出 clean multipolygons。

### scripts/02_reclassify_multipolygons.py
- 状态：已升级为 V2（规则+层级门控）。
- 主要新增：
  1. 分类体系从 9 类扩展到 10 类：
     - `LUM_ID=10`：`mangrove_land`（红树林独立类）。
  2. 叶子面优先 + 强语义非叶子面准入：
     - 新增字段：`child_count/is_leaf_polygon/is_strong_non_leaf_candidate/is_non_leaf_allowed`。
     - 对非准入非叶子面执行层级门控：`rule_id=NON_LEAF_EXCLUDED`，置为 `LUM_ID=255`。
  3. 湿地细分：
     - `wetland=mangrove` -> `LUM_ID=10`（规则 `MANGROVE_TAG`）。
     - `wetland=saltmarsh/swamp/reedbed` -> `LUM_ID=9`（规则 `WETLAND_NATURAL_LAND`）。
  4. parking 规则修正：
     - `amenity=parking/parking_space/bicycle_parking/motorcycle_parking` 从交通迁入基础设施（`LUM_ID=5`）。
  5. 鱼塘规则保留并增强：
     - `FISHPOND_ONLY_WATER_POND` + `FISHPOND_OTHER_TAG` 并行保留。
  6. 手工特例保留：
     - `fid=64864` 强制归类为 `LUM_ID=8`。
- 层级判定模式：
  - `--hierarchy-mode bbox_exact`：候选 bbox + 精确 contains（需 `osgeo.ogr`）。
  - `--hierarchy-mode bbox`：仅 bbox 快速模式。
  - `--exact-parent-limit`：精确 contains 调试限流（0=全部）。

### scripts/03_rasterize_LUMID_classes.py
- 状态：已升级为 V2（烧录策略+性能优化）。
- 主要新增：
  1. 三层烧录顺序：
     - 强语义非叶子面 -> 叶子面覆盖 -> context 可选补洞。
  2. 大任务保护与性能参数：
     - 新增像元总数预估与阈值拦截（超阈值默认报错）。
     - 新增参数：`--max-pixels`、`--allow-huge-raster`、`--gdal-cache-mb`。
  3. context_fill 改为分块处理（windowed），避免整图一次性读写。
  4. 大图时 context 补洞后端可自动切换（`MEM` 或临时 `GTiff`）。
  5. 输出增加过程统计（主层参与数、强语义非叶子面参与数、叶子面参与数、像元总数等）。

## 3. 关键业务决策（已固化）
- 行政边界提取：`name='香港 Hong Kong'`。
- 主流程格式：`gpkg`（非 `shp`）。
- 未分类/空白：统一编码 `255`。
- 红树林策略（最新）：独立为 `LUM_ID=10`，用于避免训练时水体/森林混淆。
- 细碎面优先策略：叶子面优先，允许少量强语义非叶子面参与。

## 4. 最近一次验证结论（可复查）
- `02`（`--hierarchy-mode bbox`）已跑通，输出统计示例：
  - 总要素：`186,814`
  - 叶子面：`158,744`
  - 非叶子面：`28,070`
  - 强语义非叶子面准入：`4,915`
  - 层级门控排除：`12,471`
  - unknown（255）：`36,719`
- 红树林验证：
  - `wetland=mangrove` 已全部命中 `LUM_ID=10`（`MANGROVE_TAG`）。
- 栅格验证：
  - 主输出 `hk_landuse_LUMID.tif` 包含值 `10`（红树林），像元计数已出现。

## 5. 环境与已知注意事项
- `01/03` 运行依赖 `osgeo`，建议固定 GIS conda 环境。
- 在普通 Python 环境下，`03` 会报缺少 `osgeo`。
- `02` 的精确 contains（`bbox_exact`）若无 `osgeo.ogr` 会自动回退到 `bbox`。
- `conda run` 在当前机器上偶发编码噪音（GBK/Unicode 输出），不等于脚本必然失败；以目标文件是否生成和脚本日志为准。

## 6. 下次开工前检查清单
- 数据输入存在性：
  - `data/raw/osm/hong-kong-260330.osm.pbf`
  - `data/interim/gpkg/hong-kong-260330_multipolygons_clean.gpkg`
- 环境：
  - `python` 可用；
  - GIS 环境内 `osgeo` 可用。
- 推荐执行顺序：
  1. `python scripts/02_reclassify_multipolygons.py --hierarchy-mode bbox_exact`
  2. `python scripts/03_rasterize_LUMID_classes.py --pixel-size <分辨率>`
- 若需要超细分辨率（如 `0.2m`）：
  - 优先分区处理或提高像元大小；
  - 必要时显式配置 `--max-pixels` / `--allow-huge-raster` / `--gdal-cache-mb`。

## 7. 仍需推进的工作
- 用 GIS 环境完整跑一轮 `bbox_exact` 全量（非调试限流），评估与 `bbox` 差异。
- 针对 `NON_LEAF_EXCLUDED` 与 `unknown` 做重点区域复核，必要时补充白名单。
- 继续完善 10 类质量评估（面积占比、空间一致性、类别混淆风险）。
- 构建训练样本切片与 train/val/test 划分，进入模型训练前准备。

## 8. 相关文档
- `plan.md`：V2 策略与实施细化（含强语义非叶子面参与方案）。
- `GPKG_MULTIPOLYGONS_FIELDS.md`：`multipolygons` 字段字典与解释。
