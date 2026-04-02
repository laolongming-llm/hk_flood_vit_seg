#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
脚本名称：02_reclassify_multipolygons.py

功能：
1) 读取 hong-kong-260330_multipolygons_clean.gpkg；
2) 按规则将 multipolygons 重分类到水动力模型 9 类；
3) 输出带 LUM_ID 的重分类 GPKG；
4) 输出 unknown(=255) 子集 GPKG；
5) 输出分类审计统计 CSV。

说明：
- LUM_ID: 1~9 为有效地物分类；255 为未分类/空白。
- 本脚本仅处理矢量重分类，不进行栅格化。
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_GPKG: Final[Path] = PROJECT_ROOT / "data" / "interim" / "gpkg" / "hong-kong-260330_multipolygons_clean.gpkg"
DEFAULT_RECLASS_GPKG: Final[Path] = PROJECT_ROOT / "data" / "interim" / "cleaned_vectors" / "hk_multipolygons_hydro_reclass.gpkg"
DEFAULT_UNKNOWN_GPKG: Final[Path] = PROJECT_ROOT / "data" / "interim" / "cleaned_vectors" / "hk_multipolygons_unknown.gpkg"
DEFAULT_AUDIT_CSV: Final[Path] = PROJECT_ROOT / "data" / "interim" / "cleaned_vectors" / "hk_mapping_audit.csv"

TABLE_NAME: Final[str] = "multipolygons"

CORE_EMPTY_EXPR: Final[str] = (
    "COALESCE(landuse,'')='' AND COALESCE(natural,'')='' AND COALESCE(building,'')='' "
    "AND COALESCE(amenity,'')='' AND COALESCE(leisure,'')='' AND COALESCE(man_made,'')=''"
)


def to_abs_path(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def copy_file(src: Path, dst: Path, overwrite: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if not overwrite:
            raise FileExistsError(f"输出文件已存在: {dst}")
        dst.unlink()
    shutil.copy2(src, dst)


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r[1] == column for r in rows)


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = [
        ("LUM_ID", "INTEGER"),
        ("LUM_NAME", "TEXT"),
        ("rule_id", "TEXT"),
        ("rule_level", "TEXT"),
        ("is_context_polygon", "INTEGER"),
        ("confidence", "TEXT"),
    ]
    for col_name, col_type in cols:
        if not column_exists(conn, TABLE_NAME, col_name):
            conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {col_name} {col_type};")


def init_classification_fields(conn: sqlite3.Connection) -> None:
    # 统一初始化为 unknown(255)
    conn.execute(
        f"""
        UPDATE {TABLE_NAME}
        SET LUM_ID = 255,
            LUM_NAME = 'unknown',
            rule_id = 'UNMAPPED',
            rule_level = 'unmapped',
            confidence = 'low';
        """
    )

    # 标记上下文面（避免粗面覆盖细面）
    conn.execute(
        f"""
        UPDATE {TABLE_NAME}
        SET is_context_polygon = CASE
            WHEN COALESCE(place,'') <> '' THEN 1
            WHEN COALESCE(boundary,'') <> '' OR COALESCE(admin_level,'') <> '' THEN 1
            WHEN COALESCE(name,'') <> '' AND {CORE_EMPTY_EXPR} THEN 1
            ELSE 0
        END;
        """
    )


def apply_rule(
    conn: sqlite3.Connection,
    lum_id: int,
    lum_name: str,
    rule_id: str,
    rule_level: str,
    confidence: str,
    condition_sql: str,
) -> int:
    sql = f"""
    UPDATE {TABLE_NAME}
    SET LUM_ID = ?,
        LUM_NAME = ?,
        rule_id = ?,
        rule_level = ?,
        confidence = ?
    WHERE COALESCE(LUM_ID, 255) = 255
      AND ({condition_sql});
    """
    cur = conn.execute(sql, (lum_id, lum_name, rule_id, rule_level, confidence))
    return cur.rowcount if cur.rowcount is not None else 0


def run_rules(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    stats: list[tuple[str, int]] = []

    fish_pond_cond = (
        "COALESCE(natural,'')='water' AND ("
        "(instr(COALESCE(other_tags,''), '\"aquaculture\"=>\"yes\"') > 0 "
        "AND instr(COALESCE(other_tags,''), '\"water\"=>\"pond\"') > 0) "
        "OR instr(COALESCE(other_tags,''), '\"water\"=>\"pond\"') > 0"
        ")"
    )
    stats.append(
        (
            "FISHPOND_OTHER_TAG",
            apply_rule(
                conn,
                7,
                "fish_pond_land",
                "FISHPOND_OTHER_TAG",
                "tag_indirect",
                "high",
                fish_pond_cond,
            ),
        )
    )

    water_cond = (
        "COALESCE(natural,'') IN ('water','bay','strait','wetland','reef','shoal','mud')"
    )
    stats.append(
        (
            "WATER_NATURAL",
            apply_rule(
                conn,
                8,
                "water_body",
                "WATER_NATURAL",
                "tag_direct",
                "high",
                water_cond,
            ),
        )
    )

    industrial_cond = (
        "COALESCE(landuse,'') IN ('industrial','brownfield','landfill') "
        "OR COALESCE(building,'') IN ('industrial','warehouse')"
    )
    stats.append(
        (
            "INDUSTRIAL_TAG",
            apply_rule(
                conn,
                3,
                "industrial_land",
                "INDUSTRIAL_TAG",
                "tag_direct",
                "high",
                industrial_cond,
            ),
        )
    )

    business_cond = (
        "COALESCE(landuse,'') IN ('commercial','retail') "
        "OR COALESCE(shop,'')<>'' "
        "OR COALESCE(office,'')<>'' "
        "OR COALESCE(tourism,'') IN ('hotel','hostel','guest_house','motel','apartment') "
        "OR COALESCE(building,'') IN ('commercial','retail','office','hotel')"
    )
    stats.append(
        (
            "BUSINESS_TAG",
            apply_rule(
                conn,
                2,
                "business_land",
                "BUSINESS_TAG",
                "tag_direct",
                "high",
                business_cond,
            ),
        )
    )

    transport_cond = (
        "COALESCE(aeroway,'')<>'' "
        "OR COALESCE(landuse,'') IN ('highway','railway') "
        "OR COALESCE(amenity,'') IN ('parking','parking_space','bus_station','ferry_terminal','bicycle_parking','motorcycle_parking') "
        "OR COALESCE(man_made,'') IN ('bridge','pier','quay','container_terminal')"
    )
    stats.append(
        (
            "TRANSPORT_TAG",
            apply_rule(
                conn,
                4,
                "transport_land",
                "TRANSPORT_TAG",
                "tag_direct",
                "high",
                transport_cond,
            ),
        )
    )

    infrastructure_cond = (
        "COALESCE(man_made,'') IN ('reservoir_covered','pumping_station','wastewater_plant','water_works','storage_tank','water_tower') "
        "OR COALESCE(amenity,'') IN ('waste_transfer_station','recycling','fuel','fire_station','police','grave_yard','school','college','university','hospital','clinic','prison','courthouse','townhall','library') "
        "OR COALESCE(landuse,'') IN ('cemetery','military','institutional','religious','education')"
    )
    stats.append(
        (
            "INFRA_TAG",
            apply_rule(
                conn,
                5,
                "infrastructure_land",
                "INFRA_TAG",
                "tag_direct",
                "high",
                infrastructure_cond,
            ),
        )
    )

    agricultural_cond = (
        "COALESCE(landuse,'') IN ('farmland','farmyard','orchard','greenhouse_horticulture','plant_nursery','allotments')"
    )
    stats.append(
        (
            "AGRI_TAG",
            apply_rule(
                conn,
                6,
                "agricultural_land",
                "AGRI_TAG",
                "tag_direct",
                "high",
                agricultural_cond,
            ),
        )
    )

    mountainous_cond = (
        "COALESCE(natural,'') IN ('wood','grassland','scrub','bare_rock','heath','scree','rock','cliff','shingle','slope','fell','landslide') "
        "OR COALESCE(landuse,'') IN ('forest','grass','greenfield')"
    )
    stats.append(
        (
            "MOUNTAIN_TAG",
            apply_rule(
                conn,
                9,
                "mountainous_land",
                "MOUNTAIN_TAG",
                "tag_direct",
                "high",
                mountainous_cond,
            ),
        )
    )

    # 利用 other_tags 补识别 building:part
    building_part_cond = "instr(COALESCE(other_tags,'') , 'building:part') > 0"
    stats.append(
        (
            "BUILDING_PART_FALLBACK",
            apply_rule(
                conn,
                1,
                "building_land",
                "BUILDING_PART_FALLBACK",
                "tag_indirect",
                "medium",
                building_part_cond,
            ),
        )
    )

    building_cond = "COALESCE(building,'')<>''"
    stats.append(
        (
            "BUILDING_TAG",
            apply_rule(
                conn,
                1,
                "building_land",
                "BUILDING_TAG",
                "tag_direct",
                "high",
                building_cond,
            ),
        )
    )

    # name 兜底规则（仅处理仍 unknown 的对象）
    business_name_cond = (
        "LOWER(COALESCE(name,'')) LIKE '%mall%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%shopping%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%plaza%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%galleria%' "
        "OR COALESCE(name,'') LIKE '%商場%' "
        "OR COALESCE(name,'') LIKE '%廣場%'"
    )
    stats.append(
        (
            "BUSINESS_NAME",
            apply_rule(
                conn,
                2,
                "business_land",
                "BUSINESS_NAME",
                "name_fallback",
                "medium",
                business_name_cond,
            ),
        )
    )

    transport_name_cond = (
        "LOWER(COALESCE(name,'')) LIKE '%terminal%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%station%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%apron%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%airport%' "
        "OR COALESCE(name,'') LIKE '%碼頭%' "
        "OR COALESCE(name,'') LIKE '%站%' "
        "OR COALESCE(name,'') LIKE '%停機坪%' "
        "OR COALESCE(name,'') LIKE '%機場%'"
    )
    stats.append(
        (
            "TRANSPORT_NAME",
            apply_rule(
                conn,
                4,
                "transport_land",
                "TRANSPORT_NAME",
                "name_fallback",
                "medium",
                transport_name_cond,
            ),
        )
    )

    water_name_cond = (
        "LOWER(COALESCE(name,'')) LIKE '%harbour%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%bay%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%channel%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%strait%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%reservoir%' "
        "OR COALESCE(name,'') LIKE '%港%' "
        "OR COALESCE(name,'') LIKE '%灣%' "
        "OR COALESCE(name,'') LIKE '%海峽%' "
        "OR COALESCE(name,'') LIKE '%水庫%'"
    )
    stats.append(
        (
            "WATER_NAME",
            apply_rule(
                conn,
                8,
                "water_body",
                "WATER_NAME",
                "name_fallback",
                "low",
                water_name_cond,
            ),
        )
    )

    mountain_name_cond = (
        "LOWER(COALESCE(name,'')) LIKE '%island%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%islet%' "
        "OR LOWER(COALESCE(name,'')) LIKE '%archipelago%' "
        "OR COALESCE(name,'') LIKE '%島%' "
        "OR COALESCE(name,'') LIKE '%洲%' "
        "OR COALESCE(place,'') IN ('island','islet','archipelago','peninsula')"
    )
    stats.append(
        (
            "MOUNTAIN_NAME_PLACE",
            apply_rule(
                conn,
                9,
                "mountainous_land",
                "MOUNTAIN_NAME_PLACE",
                "context_fill",
                "low",
                mountain_name_cond,
            ),
        )
    )

    return stats


def write_audit_csv(conn: sqlite3.Connection, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    total = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME};").fetchone()[0]

    class_rows = conn.execute(
        f"""
        SELECT LUM_ID, LUM_NAME, COUNT(*) AS n
        FROM {TABLE_NAME}
        GROUP BY LUM_ID, LUM_NAME
        ORDER BY LUM_ID;
        """
    ).fetchall()

    rule_rows = conn.execute(
        f"""
        SELECT rule_id, rule_level, confidence, COUNT(*) AS n
        FROM {TABLE_NAME}
        GROUP BY rule_id, rule_level, confidence
        ORDER BY n DESC;
        """
    ).fetchall()

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["section", "key1", "key2", "key3", "count", "ratio"])
        for lum_id, lum_name, n in class_rows:
            ratio = (n / total) if total else 0.0
            w.writerow(["class", lum_id, lum_name, "", n, f"{ratio:.6f}"])

        for rule_id, rule_level, confidence, n in rule_rows:
            ratio = (n / total) if total else 0.0
            w.writerow(["rule", rule_id, rule_level, confidence, n, f"{ratio:.6f}"])


def export_unknown_subset(reclass_gpkg: Path, unknown_gpkg: Path, overwrite: bool) -> None:
    copy_file(reclass_gpkg, unknown_gpkg, overwrite=overwrite)
    conn = sqlite3.connect(str(unknown_gpkg))
    try:
        conn.execute(f"DELETE FROM {TABLE_NAME} WHERE COALESCE(LUM_ID, 255) <> 255;")
        conn.commit()
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="重分类 multipolygons 并输出 LUM_ID 结果")
    parser.add_argument("--input", "-i", default=str(DEFAULT_INPUT_GPKG), help="输入 clean multipolygons gpkg")
    parser.add_argument("--output", "-o", default=str(DEFAULT_RECLASS_GPKG), help="输出重分类 gpkg")
    parser.add_argument("--unknown-output", default=str(DEFAULT_UNKNOWN_GPKG), help="输出 unknown 子集 gpkg")
    parser.add_argument("--audit-csv", default=str(DEFAULT_AUDIT_CSV), help="输出审计统计 csv")
    parser.add_argument("--no-unknown-output", action="store_true", help="不导出 unknown 子集 gpkg")
    parser.add_argument("--no-audit-csv", action="store_true", help="不输出审计 csv")
    parser.add_argument("--no-overwrite", action="store_true", help="若输出存在则报错，不覆盖")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    input_gpkg = to_abs_path(args.input)
    output_gpkg = to_abs_path(args.output)
    unknown_gpkg = to_abs_path(args.unknown_output)
    audit_csv = to_abs_path(args.audit_csv)
    overwrite = not args.no_overwrite

    if not input_gpkg.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_gpkg}")

    copy_file(input_gpkg, output_gpkg, overwrite=overwrite)

    conn = sqlite3.connect(str(output_gpkg))
    try:
        ensure_columns(conn)
        init_classification_fields(conn)
        stats = run_rules(conn)
        conn.commit()

        total = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME};").fetchone()[0]
        unknown = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE COALESCE(LUM_ID,255)=255;").fetchone()[0]

        print(f"总要素数: {total}")
        print(f"未分类要素数(LUM_ID=255): {unknown}")
        print("规则命中统计(增量):")
        for rule_name, n in stats:
            print(f"  - {rule_name:<24} {n}")

        if not args.no_audit_csv:
            write_audit_csv(conn, audit_csv)
            print(f"已输出审计 CSV: {audit_csv}")

    finally:
        conn.close()

    if not args.no_unknown_output:
        export_unknown_subset(output_gpkg, unknown_gpkg, overwrite=overwrite)
        print(f"已输出 unknown 子集: {unknown_gpkg}")

    print(f"已输出重分类 GPKG: {output_gpkg}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

