#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
脚本名称：03_rasterize_LUMID_classes.py

功能：
- 将带 `LUM_ID` 的 multipolygons 矢量栅格化为模型输入栅格；
- 按区域 `minx,miny,maxx,maxy` 构造矩形范围；
- 分类像元写入对应 `LUM_ID`；
- 未分类/空白像元统一赋值为 255。

说明：
- 输出栅格默认 Byte 类型；
- 若启用目标投影，将先将矢量重投影到目标 EPSG 后再栅格化；
- 为避免“区域壳层面”覆盖细粒度面，按 `is_context_polygon` 排序烧录：
  - 先烧录 context 面（1）
  - 再烧录非 context 面（0）进行覆盖
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import uuid
from pathlib import Path
from typing import Final

try:
    from osgeo import gdal, ogr
except Exception as import_exc:  # pragma: no cover
    gdal = None
    ogr = None
    GDAL_IMPORT_ERROR = import_exc
else:
    GDAL_IMPORT_ERROR = None

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_GPKG: Final[Path] = PROJECT_ROOT / "data" / "interim" / "cleaned_vectors" / "hk_multipolygons_hydro_reclass.gpkg"
DEFAULT_OUTPUT_TIF: Final[Path] = PROJECT_ROOT / "data" / "interim" / "masks_full" / "hk_landuse_LUMID.tif"
DEFAULT_TMP_DIR: Final[Path] = PROJECT_ROOT / "data" / "interim" / "temp" / "rasterize"

DEFAULT_LAYER_NAME: Final[str] = "multipolygons"
DEFAULT_LUM_FIELD: Final[str] = "LUM_ID"
DEFAULT_PIXEL_SIZE: Final[float] = 10.0
DEFAULT_TARGET_EPSG: Final[int] = 2326
DEFAULT_NODATA: Final[int] = 255


def require_gdal() -> None:
    if gdal is not None and ogr is not None:
        return
    detail = f"原始错误：{GDAL_IMPORT_ERROR}" if GDAL_IMPORT_ERROR else "未提供底层异常信息。"
    raise RuntimeError(
        "未检测到 GDAL/OGR Python 绑定（osgeo）。\n"
        "请在 GIS conda 环境中运行，例如：conda install -c conda-forge gdal\n"
        f"{detail}"
    )


def to_abs_path(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def get_layer_epsg(layer: ogr.Layer) -> int | None:
    srs = layer.GetSpatialRef()
    if srs is None:
        return None
    code = srs.GetAuthorityCode(None)
    if code is None:
        return None
    try:
        return int(code)
    except ValueError:
        return None


def cleanup_gpkg(path: Path) -> None:
    for p in [
        path,
        Path(str(path) + "-wal"),
        Path(str(path) + "-shm"),
        Path(str(path) + "-journal"),
    ]:
        if p.exists():
            p.unlink()


def prepare_vector(
    input_gpkg: Path,
    layer_name: str,
    target_epsg: int | None,
    tmp_dir: Path,
    overwrite: bool,
) -> tuple[Path, Path | None]:
    """按需重投影矢量，返回可用于栅格化的路径。"""
    require_gdal()

    src_ds = ogr.Open(str(input_gpkg), 0)
    if src_ds is None:
        raise RuntimeError(f"无法打开输入 GPKG: {input_gpkg}")

    layer = src_ds.GetLayerByName(layer_name)
    if layer is None:
        raise RuntimeError(f"输入中不存在图层: {layer_name}")

    src_epsg = get_layer_epsg(layer)
    src_ds = None

    if target_epsg is None or (src_epsg is not None and src_epsg == target_epsg):
        return input_gpkg, None

    tmp_dir.mkdir(parents=True, exist_ok=True)
    reproj_path = tmp_dir / f"{input_gpkg.stem}_epsg{target_epsg}_{uuid.uuid4().hex[:8]}.gpkg"

    if reproj_path.exists():
        if not overwrite:
            raise FileExistsError(f"临时重投影文件已存在: {reproj_path}")
        cleanup_gpkg(reproj_path)

    options = [
        "-f",
        "GPKG",
        "-t_srs",
        f"EPSG:{target_epsg}",
        "-nln",
        layer_name,
        "-sql",
        f"SELECT * FROM {layer_name}",
        "-dialect",
        "SQLITE",
        "-dsco",
        "VERSION=1.4",
        "-lco",
        "SPATIAL_INDEX=YES",
        "-progress",
    ]

    ds = gdal.VectorTranslate(
        destNameOrDestDS=str(reproj_path),
        srcDS=str(input_gpkg),
        options=gdal.VectorTranslateOptions(options=options),
    )
    if ds is None:
        raise RuntimeError("重投影失败。")
    ds = None

    return reproj_path, reproj_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将 LUM_ID 分类矢量栅格化")
    parser.add_argument("--input", "-i", default=str(DEFAULT_INPUT_GPKG), help="输入重分类 GPKG")
    parser.add_argument("--output", "-o", default=str(DEFAULT_OUTPUT_TIF), help="输出栅格路径(.tif)")
    parser.add_argument("--layer", default=DEFAULT_LAYER_NAME, help="输入图层名，默认 multipolygons")
    parser.add_argument("--lum-field", default=DEFAULT_LUM_FIELD, help="分类字段名，默认 LUM_ID")
    parser.add_argument("--pixel-size", type=float, default=DEFAULT_PIXEL_SIZE, help="像元分辨率（目标坐标单位）")
    parser.add_argument("--target-epsg", type=int, default=DEFAULT_TARGET_EPSG, help="目标 EPSG，默认 2326")
    parser.add_argument("--nodata", type=int, default=DEFAULT_NODATA, help="空白/未分类像元值，默认 255")
    parser.add_argument("--all-touched", action="store_true", help="启用 ALL_TOUCHED=TRUE")
    parser.add_argument("--tmp-dir", default=str(DEFAULT_TMP_DIR), help="临时目录")
    parser.add_argument("--keep-temp", action="store_true", help="保留重投影临时文件")
    parser.add_argument("--no-overwrite", action="store_true", help="输出存在时不覆盖")
    return parser


def main() -> None:
    require_gdal()
    gdal.UseExceptions()

    args = build_parser().parse_args()

    input_gpkg = to_abs_path(args.input)
    output_tif = to_abs_path(args.output)
    tmp_dir = to_abs_path(args.tmp_dir)
    overwrite = not args.no_overwrite

    if not input_gpkg.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_gpkg}")

    if args.pixel_size <= 0:
        raise ValueError("--pixel-size 必须大于 0")

    if not (0 <= args.nodata <= 255):
        raise ValueError("--nodata 必须位于 0~255（Byte）")

    work_gpkg, temp_gpkg = prepare_vector(
        input_gpkg=input_gpkg,
        layer_name=args.layer,
        target_epsg=args.target_epsg,
        tmp_dir=tmp_dir,
        overwrite=overwrite,
    )

    ds_vec = ogr.Open(str(work_gpkg), 0)
    if ds_vec is None:
        raise RuntimeError(f"无法打开矢量数据: {work_gpkg}")

    layer = ds_vec.GetLayerByName(args.layer)
    if layer is None:
        raise RuntimeError(f"图层不存在: {args.layer}")

    minx, maxx, miny, maxy = layer.GetExtent()

    # 以 minx/miny 与 maxx/maxy 构造矩形范围
    width = max(1, math.ceil((maxx - minx) / args.pixel_size))
    height = max(1, math.ceil((maxy - miny) / args.pixel_size))

    output_tif.parent.mkdir(parents=True, exist_ok=True)
    if output_tif.exists():
        if not overwrite:
            raise FileExistsError(f"输出文件已存在: {output_tif}")
        output_tif.unlink()

    driver = gdal.GetDriverByName("GTiff")
    if driver is None:
        raise RuntimeError("未找到 GTiff 驱动")

    raster_ds = driver.Create(
        str(output_tif),
        width,
        height,
        1,
        gdal.GDT_Byte,
        options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"],
    )
    if raster_ds is None:
        raise RuntimeError("创建输出栅格失败")

    geotransform = (minx, args.pixel_size, 0.0, maxy, 0.0, -args.pixel_size)
    raster_ds.SetGeoTransform(geotransform)

    srs = layer.GetSpatialRef()
    if srs is not None:
        raster_ds.SetProjection(srs.ExportToWkt())

    band = raster_ds.GetRasterBand(1)
    band.SetNoDataValue(args.nodata)
    band.Fill(args.nodata)

    # 按 is_context_polygon 排序：1(上下文面)先烧录，0(细粒度面)后覆盖
    sql = (
        f"SELECT * FROM {args.layer} "
        f"WHERE COALESCE({args.lum_field}, 255) <> 255 "
        f"ORDER BY COALESCE(is_context_polygon, 0) DESC, fid ASC"
    )
    ordered_layer = ds_vec.ExecuteSQL(sql, dialect="SQLITE")
    if ordered_layer is None:
        raise RuntimeError("构建排序图层失败")

    options = [f"ATTRIBUTE={args.lum_field}"]
    if args.all_touched:
        options.append("ALL_TOUCHED=TRUE")

    err = gdal.RasterizeLayer(raster_ds, [1], ordered_layer, options=options)
    ds_vec.ReleaseResultSet(ordered_layer)

    if err != 0:
        raise RuntimeError(f"栅格化失败，错误码: {err}")

    band.FlushCache()
    raster_ds.FlushCache()
    raster_ds = None
    ds_vec = None

    if temp_gpkg is not None and not args.keep_temp:
        cleanup_gpkg(temp_gpkg)

    print(f"输出栅格: {output_tif}")
    print(f"范围 minx/miny/maxx/maxy: {minx}, {miny}, {maxx}, {maxy}")
    print(f"栅格尺寸: {width} x {height}")
    print(f"像元分辨率: {args.pixel_size}")
    print(f"未分类/空白值: {args.nodata}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
