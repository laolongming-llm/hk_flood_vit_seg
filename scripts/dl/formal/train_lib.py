"""Formal 训练公共工具库。

功能与作用：
1. 统一提供配置加载（含 base_config 递归合并）与项目路径解析。
2. 提供数据指纹校验、环境信息采集、日志与输出目录初始化。
3. 提供遥感分割数据集加载、ViT baseline 模型构建、指标计算与 checkpoint 管理。
4. 供 01/02/03/04/05 脚本复用，避免重复实现。
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import platform
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import rasterio
import torch
import torch.nn as nn
import yaml
from torch.utils.data import Dataset


def _detect_project_root() -> Path:
    """自动定位项目根目录（优先使用 .git 所在目录）。"""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / ".git").exists():
            return parent
    # 兜底：scripts/dl/formal/train_lib.py -> 项目根目录通常是上 4 级。
    return current.parents[4]


PROJECT_ROOT = _detect_project_root()


def resolve_ref_path(path_ref: str | Path, config_base_dir: Path) -> Path:
    """解析路径引用：绝对路径直返；相对路径优先按项目根解析。"""
    path_ref = Path(path_ref)
    if path_ref.is_absolute():
        return path_ref
    candidate = (PROJECT_ROOT / path_ref).resolve()
    if candidate.exists():
        return candidate
    return (config_base_dir / path_ref).resolve()


def deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并两个字典，override 同名键覆盖 base。"""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_config_recursive(config_path: Path, stack: Optional[List[Path]] = None) -> Dict[str, Any]:
    """递归加载配置文件并处理 base_config 继承链。"""
    stack = stack or []
    resolved = config_path.resolve()
    if resolved in stack:
        chain = " -> ".join(str(p) for p in stack + [resolved])
        raise ValueError(f"Detected recursive base_config chain: {chain}")
    stack = stack + [resolved]

    with resolved.open("r", encoding="utf-8") as f:
        current_cfg = yaml.safe_load(f) or {}

    base_ref = current_cfg.get("base_config")
    if base_ref:
        base_path = resolve_ref_path(base_ref, resolved.parent)
        base_cfg = _load_config_recursive(base_path, stack=stack)
        current_cfg = dict(current_cfg)
        current_cfg.pop("base_config", None)
        merged = deep_merge_dict(base_cfg, current_cfg)
    else:
        merged = current_cfg

    merged.setdefault("_meta", {})
    merged["_meta"]["resolved_config_path"] = str(resolved)
    return merged


def load_config(config_path: str | Path) -> Dict[str, Any]:
    """加载正式训练配置（支持 base_config 继承与递归合并）。"""
    resolved = resolve_ref_path(config_path, PROJECT_ROOT)
    return _load_config_recursive(resolved)


def resolve_project_path(path_ref: str | Path, config_path: str | Path) -> Path:
    """基于项目根与配置目录解析路径，供数据/输出路径统一调用。"""
    config_path = Path(config_path).resolve()
    return resolve_ref_path(path_ref, config_path.parent)


def get_run_paths(cfg: Dict[str, Any], config_path: str | Path) -> Dict[str, Path]:
    """按配置生成 run 目录结构。"""
    output_cfg = cfg.get("output", {})
    experiment_cfg = cfg.get("experiment", {})
    root_dir = resolve_project_path(output_cfg.get("root_dir", "outputs/train_runs/formal"), config_path)
    run_subdir = output_cfg.get("run_subdir") or experiment_cfg.get("run_name") or "formal_run"
    run_dir = root_dir / run_subdir
    return {
        "root_dir": root_dir,
        "run_dir": run_dir,
        "logs_dir": run_dir / "logs",
        "metrics_dir": run_dir / "metrics",
        "checkpoints_dir": run_dir / "checkpoints",
        "figures_dir": run_dir / "figures",
    }


def ensure_run_dirs(paths: Dict[str, Path]) -> None:
    """创建 run 目录。"""
    for path in paths.values():
        if path.suffix:
            continue
        path.mkdir(parents=True, exist_ok=True)


def setup_logger(log_path: Path, logger_name: str) -> logging.Logger:
    """初始化日志器，同时输出到文件与终端。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def set_global_seed(seed: int, deterministic: bool = True, cudnn_benchmark: bool = False) -> None:
    """设置随机种子与 cuDNN 策略，增强训练复现性。"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if deterministic:
        torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = bool(cudnn_benchmark)


def read_manifest(manifest_path: Path) -> pd.DataFrame:
    """读取 tiles manifest 并校验关键列。"""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    df = pd.read_csv(manifest_path)
    required = {"tile_id", "final_split"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Manifest missing required columns: {sorted(missing)}")
    return df


def build_tile_paths(
    dataset_root: Path,
    image_dirname: str,
    label_dirname: str,
    image_suffix: str,
    label_suffix: str,
    split_name: str,
    tile_id: str,
) -> Tuple[Path, Path]:
    """根据 split 与 tile_id 构建影像/标签文件路径。"""
    image_path = dataset_root / image_dirname / split_name / f"{tile_id}{image_suffix}"
    label_path = dataset_root / label_dirname / split_name / f"{tile_id}{label_suffix}"
    return image_path, label_path


def validate_manifest_files(
    df: pd.DataFrame,
    dataset_root: Path,
    image_dirname: str,
    label_dirname: str,
    image_suffix: str,
    label_suffix: str,
) -> List[str]:
    """检查 manifest 对应样本文件是否存在，返回缺失列表。"""
    errors: List[str] = []
    for row in df.itertuples(index=False):
        split_name = str(getattr(row, "final_split"))
        tile_id = str(getattr(row, "tile_id"))
        image_path, label_path = build_tile_paths(
            dataset_root=dataset_root,
            image_dirname=image_dirname,
            label_dirname=label_dirname,
            image_suffix=image_suffix,
            label_suffix=label_suffix,
            split_name=split_name,
            tile_id=tile_id,
        )
        if not image_path.exists():
            errors.append(f"Missing image: {image_path}")
        if not label_path.exists():
            errors.append(f"Missing label: {label_path}")
    return errors


def compute_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """计算文件 SHA256，用于数据指纹一致性校验。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_dataset_fingerprint(cfg: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    """校验数据冻结指纹关键目标是否完整且与当前文件哈希一致。"""
    freeze_cfg = cfg.get("freeze", {})
    result: Dict[str, Any] = {
        "enabled": bool(freeze_cfg.get("verify_fingerprint_on_start", False)),
        "status": "SKIPPED",
        "fingerprint_path": None,
        "checks": [],
        "errors": [],
    }
    if not result["enabled"]:
        return result

    fingerprint_path = resolve_project_path(freeze_cfg["dataset_fingerprint_path"], config_path)
    result["fingerprint_path"] = str(fingerprint_path)
    if not fingerprint_path.exists():
        result["status"] = "FAILED"
        result["errors"].append(f"Fingerprint file missing: {fingerprint_path}")
        return result

    df = pd.read_csv(fingerprint_path)
    required_targets = list(freeze_cfg.get("required_fingerprint_targets", []))
    for target in required_targets:
        row = df[df["target_path"] == target]
        if row.empty:
            result["errors"].append(f"Target missing in fingerprint: {target}")
            continue
        record = row.iloc[0].to_dict()
        path_obj = resolve_project_path(target, config_path)
        check_item = {
            "target_path": target,
            "path_exists": bool(path_obj.exists()),
            "file_exists_field": record.get("file_exists"),
            "sha256_match": True,
        }
        if not path_obj.exists():
            check_item["sha256_match"] = False
            result["checks"].append(check_item)
            result["errors"].append(f"Target file missing on disk: {path_obj}")
            continue
        expected_sha = str(record.get("sha256", "")).strip()
        if expected_sha and expected_sha.lower() != "nan":
            actual_sha = compute_sha256(path_obj)
            check_item["sha256_match"] = actual_sha == expected_sha
            if not check_item["sha256_match"]:
                result["errors"].append(f"SHA256 mismatch: {target}")
        result["checks"].append(check_item)

    result["status"] = "PASSED" if not result["errors"] else "FAILED"
    return result


def gather_env_info() -> Dict[str, Any]:
    """收集运行环境摘要信息，用于实验追溯。"""
    info: Dict[str, Any] = {
        "python_version": sys.version,
        "platform": platform.platform(),
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "cuda_device_count": int(torch.cuda.device_count()),
        "cwd": str(Path.cwd()),
    }
    if torch.cuda.is_available():
        info["gpu_name"] = torch.cuda.get_device_name(0)
        info["torch_cuda_version"] = torch.version.cuda
    try:
        info["git_commit"] = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT)
            .decode("utf-8")
            .strip()
        )
    except Exception:
        info["git_commit"] = None
    return info


class ViTSegBaseline(nn.Module):
    """基于 timm ViT backbone + 简单上采样解码头的 baseline 分割模型。"""

    def __init__(self, backbone_name: str, num_classes: int, pretrained: bool, dropout: float, input_size: int):
        super().__init__()
        try:
            import timm
        except Exception as exc:  # pragma: no cover - 依赖缺失时给清晰报错
            raise RuntimeError("timm is required for ViT baseline model.") from exc

        create_kwargs: Dict[str, Any] = {
            "pretrained": pretrained,
            "num_classes": 0,
            "global_pool": "",
        }
        if "vit" in backbone_name:
            create_kwargs["img_size"] = int(input_size)
            create_kwargs["dynamic_img_size"] = True

        self.backbone = timm.create_model(backbone_name, **create_kwargs)
        embed_dim = int(getattr(self.backbone, "num_features", 768))
        self.dropout = nn.Dropout(p=float(dropout))
        self.decoder = nn.Sequential(
            nn.Conv2d(embed_dim, 256, kernel_size=1, bias=False),
            nn.BatchNorm2d(256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 128, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.Conv2d(128, num_classes, kernel_size=1),
        )

    def _tokens_to_map(self, tokens: torch.Tensor, height: int, width: int) -> torch.Tensor:
        """将 ViT token 序列转换为二维特征图。"""
        if tokens.ndim == 4:
            return tokens
        if tokens.ndim != 3:
            raise ValueError(f"Unsupported token shape: {tuple(tokens.shape)}")
        bsz, token_count, channels = tokens.shape
        expected = height * width
        if token_count == expected + 1:
            tokens = tokens[:, 1:, :]
        elif token_count != expected:
            raise ValueError(
                f"Token count mismatch: got {token_count}, expected {expected} or {expected + 1}."
            )
        return tokens.transpose(1, 2).reshape(bsz, channels, height, width)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """执行前向推理并上采样回输入空间分辨率。"""
        input_h, input_w = x.shape[-2:]
        features = self.backbone.forward_features(x)
        patch_embed = getattr(self.backbone, "patch_embed", None)
        patch_size = getattr(patch_embed, "patch_size", 16)
        if isinstance(patch_size, tuple):
            patch_h, patch_w = int(patch_size[0]), int(patch_size[1])
        else:
            patch_h = patch_w = int(patch_size)
        grid_h = max(1, input_h // patch_h)
        grid_w = max(1, input_w // patch_w)
        feat_map = self._tokens_to_map(features, grid_h, grid_w)
        feat_map = self.dropout(feat_map)
        logits = self.decoder(feat_map)
        logits = torch.nn.functional.interpolate(logits, size=(input_h, input_w), mode="bilinear", align_corners=False)
        return logits


def build_model_from_config(cfg: Dict[str, Any]) -> nn.Module:
    """根据配置构建正式训练模型。"""
    model_cfg = cfg.get("model", {})
    data_cfg = cfg.get("data", {})
    architecture = str(model_cfg.get("architecture", "vit_seg_baseline")).lower()
    if architecture != "vit_seg_baseline":
        raise ValueError(f"Unsupported architecture: {architecture}")
    return ViTSegBaseline(
        backbone_name=str(model_cfg.get("backbone", "vit_base_patch16_224")),
        num_classes=int(data_cfg.get("num_classes", 10)),
        pretrained=bool(model_cfg.get("pretrained", True)),
        dropout=float(model_cfg.get("dropout", 0.1)),
        input_size=int(data_cfg.get("patch_size", 512)),
    )


class SegmentationTileDataset(Dataset):
    """基于 tile manifest 的遥感语义分割数据集。"""

    def __init__(
        self,
        manifest_df: pd.DataFrame,
        dataset_root: Path,
        image_dirname: str,
        label_dirname: str,
        image_suffix: str,
        label_suffix: str,
        num_classes: int,
        ignore_index: int,
        enable_augment: bool = False,
        augment_cfg: Optional[Dict[str, Any]] = None,
    ):
        self.df = manifest_df.reset_index(drop=True)
        self.dataset_root = dataset_root
        self.image_dirname = image_dirname
        self.label_dirname = label_dirname
        self.image_suffix = image_suffix
        self.label_suffix = label_suffix
        self.num_classes = num_classes
        self.ignore_index = ignore_index
        self.enable_augment = bool(enable_augment)
        self.augment_cfg = augment_cfg or {}

    def __len__(self) -> int:
        return len(self.df)

    def _apply_train_augment(self, image: torch.Tensor, label: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """训练期增强（仅 train 启用）。

        说明：
        1. 空间变换同步作用于影像与标签，保持像元一一对应。
        2. 光谱/噪声增强仅作用于影像，不改变标签语义。
        3. 最终将影像裁剪到 [0, 1]，保持输入范围稳定。
        """
        cfg = self.augment_cfg

        if torch.rand(1).item() < float(cfg.get("hflip_prob", 0.5)):
            image = torch.flip(image, dims=[2])
            label = torch.flip(label, dims=[1])

        if torch.rand(1).item() < float(cfg.get("vflip_prob", 0.5)):
            image = torch.flip(image, dims=[1])
            label = torch.flip(label, dims=[0])

        if torch.rand(1).item() < float(cfg.get("rot90_prob", 0.5)):
            k = int(torch.randint(low=1, high=4, size=(1,)).item())
            image = torch.rot90(image, k=k, dims=[1, 2])
            label = torch.rot90(label, k=k, dims=[0, 1])

        if torch.rand(1).item() < float(cfg.get("color_jitter_prob", 0.5)):
            brightness_delta = float(cfg.get("brightness_delta", 0.10))
            contrast_delta = float(cfg.get("contrast_delta", 0.10))
            brightness_factor = 1.0 + (2.0 * torch.rand(1).item() - 1.0) * brightness_delta
            contrast_factor = 1.0 + (2.0 * torch.rand(1).item() - 1.0) * contrast_delta

            channel_mean = image.mean(dim=(1, 2), keepdim=True)
            image = (image - channel_mean) * contrast_factor + channel_mean
            image = image * brightness_factor

        if torch.rand(1).item() < float(cfg.get("channel_scale_prob", 0.25)):
            channel_scale_delta = float(cfg.get("channel_scale_delta", 0.08))
            scale = 1.0 + (torch.rand((image.shape[0], 1, 1)) * 2.0 - 1.0) * channel_scale_delta
            image = image * scale

        if torch.rand(1).item() < float(cfg.get("gaussian_noise_prob", 0.20)):
            noise_std = float(cfg.get("gaussian_noise_std", 0.02))
            image = image + torch.randn_like(image) * noise_std

        image = torch.clamp(image, 0.0, 1.0)
        return image, label

    def __getitem__(self, idx: int) -> Dict[str, Any]:
        """读取单个 tile 样本并完成标签映射。"""
        row = self.df.iloc[idx]
        tile_id = str(row["tile_id"])
        split_name = str(row["final_split"])
        image_path, label_path = build_tile_paths(
            dataset_root=self.dataset_root,
            image_dirname=self.image_dirname,
            label_dirname=self.label_dirname,
            image_suffix=self.image_suffix,
            label_suffix=self.label_suffix,
            split_name=split_name,
            tile_id=tile_id,
        )

        with rasterio.open(image_path) as src:
            image = src.read().astype(np.float32)
        with rasterio.open(label_path) as src:
            label = src.read(1).astype(np.int64)

        if image.max() > 1.0:
            image = image / 255.0

        mapped_label = np.full_like(label, fill_value=self.ignore_index, dtype=np.int64)
        valid_mask = (label >= 1) & (label <= self.num_classes)
        mapped_label[valid_mask] = label[valid_mask] - 1

        image_tensor = torch.from_numpy(image)
        label_tensor = torch.from_numpy(mapped_label)
        if self.enable_augment:
            image_tensor, label_tensor = self._apply_train_augment(image=image_tensor, label=label_tensor)

        return {
            "image": image_tensor,
            "label": label_tensor,
            "tile_id": tile_id,
            "split": split_name,
        }


def update_confusion_matrix(
    confusion: torch.Tensor,
    logits: torch.Tensor,
    targets: torch.Tensor,
    num_classes: int,
    ignore_index: int,
) -> torch.Tensor:
    """根据当前 batch 预测更新混淆矩阵。"""
    preds = logits.argmax(dim=1)
    valid_mask = targets != ignore_index
    if valid_mask.sum() == 0:
        return confusion
    target_valid = targets[valid_mask]
    pred_valid = preds[valid_mask]
    encoded = target_valid * num_classes + pred_valid
    bincount = torch.bincount(encoded, minlength=num_classes * num_classes)
    confusion += bincount.reshape(num_classes, num_classes).cpu()
    return confusion


def compute_metrics_from_confusion(confusion: torch.Tensor) -> Dict[str, Any]:
    """由混淆矩阵计算 mIoU、mF1、OA 与每类指标。"""
    confusion = confusion.to(torch.float64)
    tp = torch.diag(confusion)
    pos_gt = confusion.sum(dim=1)
    pos_pred = confusion.sum(dim=0)
    union = pos_gt + pos_pred - tp
    denom_f1 = 2.0 * tp + (pos_pred - tp) + (pos_gt - tp)

    iou = torch.where(union > 0, tp / union, torch.nan)
    f1 = torch.where(denom_f1 > 0, 2.0 * tp / denom_f1, torch.nan)
    present_mask = pos_gt > 0

    miou = torch.nanmean(iou[present_mask]).item() if present_mask.any() else float("nan")
    mf1 = torch.nanmean(f1[present_mask]).item() if present_mask.any() else float("nan")
    overall_acc = (tp.sum() / confusion.sum()).item() if confusion.sum() > 0 else 0.0

    return {
        "miou": float(miou),
        "mf1": float(mf1),
        "overall_accuracy": float(overall_acc),
        "per_class_iou": [float(x) for x in iou.tolist()],
        "per_class_f1": [float(x) for x in f1.tolist()],
        "gt_pixels_per_class": [int(x) for x in pos_gt.tolist()],
        "pred_pixels_per_class": [int(x) for x in pos_pred.tolist()],
        "tp_pixels_per_class": [int(x) for x in tp.tolist()],
        "present_mask": [bool(x) for x in present_mask.tolist()],
    }


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> None:
    """写入 CSV 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    """写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def save_yaml(path: Path, payload: Dict[str, Any]) -> None:
    """写入 YAML 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)


@dataclass
class TrainState:
    """训练状态快照。"""

    epoch: int
    global_step: int
    best_val_miou: float
    best_epoch: int


def save_checkpoint(
    checkpoint_path: Path,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    train_state: TrainState,
    config: Dict[str, Any],
    scheduler: Optional[torch.optim.lr_scheduler._LRScheduler] = None,
    scaler: Optional[torch.cuda.amp.GradScaler] = None,
) -> None:
    """保存模型训练 checkpoint。"""
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "epoch": train_state.epoch,
        "global_step": train_state.global_step,
        "best_val_miou": train_state.best_val_miou,
        "best_epoch": train_state.best_epoch,
        "config": config,
    }
    if scheduler is not None:
        payload["scheduler_state_dict"] = scheduler.state_dict()
    if scaler is not None:
        payload["scaler_state_dict"] = scaler.state_dict()
    torch.save(payload, checkpoint_path)


def load_checkpoint(checkpoint_path: Path, device: torch.device) -> Dict[str, Any]:
    """加载 checkpoint 并返回原始 payload。"""
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
    payload = torch.load(checkpoint_path, map_location=device)
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid checkpoint payload type: {type(payload)}")
    return payload
