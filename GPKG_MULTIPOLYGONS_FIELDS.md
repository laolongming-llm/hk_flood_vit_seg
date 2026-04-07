# GPKG `multipolygons` 字段说明

本文档用于说明本项目中 GeoPackage 的 `multipolygons` 图层属性字段含义。  
检查时间：2026-04-07  
检查文件：
- `data/interim/gpkg/hong-kong-260330_multipolygons_clean.gpkg`（27 列）
- `data/interim/cleaned_vectors/hk_multipolygons_hydro_reclass.gpkg`（33 列，含重分类新增字段）

## 1. 图层级信息
- 图层名：`multipolygons`
- 几何列：`geom`
- 几何类型：`MULTIPOLYGON`
- CRS：`EPSG:4326`

## 2. 原始 OSM/GDAL 字段（clean 与 reclass 都有）

| 字段名 | 类型 | 含义 |
|---|---|---|
| `fid` | INTEGER | GeoPackage 内部要素主键（本地唯一 ID，不等于 OSM 原始 ID）。 |
| `geom` | MULTIPOLYGON | 面几何。 |
| `osm_id` | TEXT | OSM 对象 ID（多用于 relation）。在 multipolygons 中通常只在部分要素上有值。 |
| `osm_way_id` | TEXT | 参与构面/主 way 的 OSM way ID；在当前数据中覆盖率很高。 |
| `name` | TEXT | OSM `name` 标签（地名、建筑名、场馆名等）。 |
| `type` | TEXT | OSM `type` 标签（常见如 `multipolygon`、`boundary`、`site` 等）。 |
| `aeroway` | TEXT | 航空相关标签（如 `apron`、`helipad`）。 |
| `amenity` | TEXT | 公共服务设施标签（如学校、医院、停车等）。 |
| `admin_level` | TEXT | 行政层级标签（边界对象常见）。 |
| `barrier` | TEXT | 障碍物/隔离设施标签（墙、围栏等）。 |
| `boundary` | TEXT | 边界类型标签（行政边界、保护区等）。 |
| `building` | TEXT | 建筑标签（如 `yes`、`house`、`apartments`）。 |
| `craft` | TEXT | 手工业/作坊类标签。 |
| `geological` | TEXT | 地质对象标签。 |
| `historic` | TEXT | 历史文化对象标签（遗址、纪念物等）。 |
| `land_area` | TEXT | 土地面积属性标签（OSM 中较少见，按原始导出保留）。 |
| `landuse` | TEXT | 土地利用标签（居住、工业、农地等）。 |
| `leisure` | TEXT | 休闲娱乐对象标签（公园、球场、泳池等）。 |
| `man_made` | TEXT | 人工构筑物标签（桥、码头、储罐、泵站等）。 |
| `military` | TEXT | 军事相关标签。 |
| `natural` | TEXT | 自然地物标签（`water`、`wood`、`scrub` 等）。 |
| `office` | TEXT | 办公机构标签。 |
| `place` | TEXT | 地名/聚落等级标签（`island`、`islet` 等）。 |
| `shop` | TEXT | 商业零售标签。 |
| `sport` | TEXT | 体育项目标签。 |
| `tourism` | TEXT | 旅游相关标签（`hotel`、`museum` 等）。 |
| `other_tags` | TEXT | 未单独展开的 OSM 标签集合，序列化为键值串（例如 `"water"=>"pond"`）。 |

## 3. 重分类新增字段（仅 `hk_multipolygons_hydro_reclass.gpkg`）

| 字段名 | 类型 | 含义 |
|---|---|---|
| `LUM_ID` | INTEGER | 最终分类编码：`1~9` 为已分类，`255` 为 unknown/空白。 |
| `LUM_NAME` | TEXT | `LUM_ID` 对应分类名称。 |
| `rule_id` | TEXT | 命中的具体规则 ID（如 `BUILDING_TAG`、`WATER_NATURAL`）。 |
| `rule_level` | TEXT | 规则层级/来源（`tag_direct`、`tag_indirect`、`name_fallback`、`context_fill`、`unmapped`）。 |
| `is_context_polygon` | INTEGER | 上下文面标识：`1`=上下文壳层面，`0`=普通细粒度面。用于栅格化时控制烧录顺序。 |
| `confidence` | TEXT | 分类置信等级（`high` / `medium` / `low`）。 |

## 4. `LUM_ID` 对照表

| LUM_ID | LUM_NAME | 语义 |
|---|---|---|
| 1 | `building_land` | 建筑用地 |
| 2 | `business_land` | 商业用地 |
| 3 | `industrial_land` | 工业用地 |
| 4 | `transport_land` | 交通用地 |
| 5 | `infrastructure_land` | 基础设施/公共服务用地 |
| 6 | `agricultural_land` | 农业用地 |
| 7 | `fish_pond_land` | 鱼塘用地 |
| 8 | `water_body` | 水体 |
| 9 | `mountainous_land` | 山地/自然地 |
| 255 | `unknown` | 未分类或空白 |

## 5. 使用提醒
- OSM 标签字段大量允许 `NULL`，这是正常现象（同一要素通常只携带少量标签）。
- `osm_id` 与 `osm_way_id` 都是字符串存储，做关联时建议显式按文本处理。
- 规则迭代时优先检查 `other_tags`，很多未展开标签（如 `water=pond`、`building:levels`）都在该列里。
