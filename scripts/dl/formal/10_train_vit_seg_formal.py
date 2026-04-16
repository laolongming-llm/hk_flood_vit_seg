"""Formal 训练入口脚本（单 seed / 单 run）。

功能与作用：
1. 按 baseline 配置执行完整训练流程（train + val + checkpoint）。
2. 启动时做数据指纹校验与环境信息落盘，保证结果可追溯。
3. 输出正式训练产物到 outputs/train_runs/formal/<run_subdir>/。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from train_lib import (
    SegmentationTileDataset,
    TrainState,
    build_model_from_config,
    compute_metrics_from_confusion,
    ensure_run_dirs,
    gather_env_info,
    get_run_paths,
    load_checkpoint,
    load_config,
    read_manifest,
    resolve_project_path,
    save_checkpoint,
    save_yaml,
    set_global_seed,
    setup_logger,
    update_confusion_matrix,
    validate_manifest_files,
    verify_dataset_fingerprint,
    write_csv,
    write_json,
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Run formal ViT baseline training.")
    parser.add_argument("--config", required=True, help="Path to formal training config YAML.")
    return parser.parse_args()


def build_dataloader(
    dataset: SegmentationTileDataset,
    batch_size: int,
    shuffle: bool,
    loader_cfg: Dict[str, Any],
    is_train: bool,
) -> DataLoader:
    """根据配置创建训练/验证 DataLoader。"""
    num_workers = int(loader_cfg.get("num_workers", 0))
    persistent_workers = bool(loader_cfg.get("persistent_workers", False)) and num_workers > 0
    pin_memory = bool(loader_cfg.get("pin_memory", True))
    drop_last = bool(loader_cfg.get("drop_last_train", False)) if is_train else False
    prefetch_factor = loader_cfg.get("prefetch_factor", 2)
    kwargs: Dict[str, Any] = {
        "dataset": dataset,
        "batch_size": batch_size,
        "shuffle": shuffle,
        "num_workers": num_workers,
        "pin_memory": pin_memory,
        "drop_last": drop_last,
        "persistent_workers": persistent_workers,
    }
    if num_workers > 0:
        kwargs["prefetch_factor"] = int(prefetch_factor)
    return DataLoader(**kwargs)


def build_poly_scheduler(
    optimizer: torch.optim.Optimizer,
    max_epochs: int,
    warmup_epochs: int,
    power: float,
    min_lr: float,
    base_lr: float,
) -> torch.optim.lr_scheduler.LambdaLR:
    """构建按 epoch 更新的 Poly 学习率调度器（含 warmup）。"""
    max_epochs = max(1, int(max_epochs))
    warmup_epochs = max(0, int(warmup_epochs))
    power = float(power)
    min_lr = float(min_lr)
    base_lr = float(base_lr)

    def lr_lambda(epoch: int) -> float:
        if warmup_epochs > 0 and epoch < warmup_epochs:
            warmup_ratio = float(epoch + 1) / float(max(1, warmup_epochs))
            return max(min_lr / base_lr, warmup_ratio)
        progress_num = max(0, epoch - warmup_epochs + 1)
        progress_den = max(1, max_epochs - warmup_epochs)
        progress = min(1.0, float(progress_num) / float(progress_den))
        poly = (1.0 - progress) ** power
        lr = min_lr + (base_lr - min_lr) * poly
        return max(min_lr / base_lr, lr / base_lr)

    return torch.optim.lr_scheduler.LambdaLR(optimizer=optimizer, lr_lambda=lr_lambda)


@torch.no_grad()
def evaluate(
    model: nn.Module,
    data_loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    num_classes: int,
    ignore_index: int,
) -> Dict[str, Any]:
    """在验证集上评估并返回 loss/IoU/F1/OA 与混淆矩阵。"""
    model.eval()
    losses: List[float] = []
    confusion = torch.zeros((num_classes, num_classes), dtype=torch.int64)

    for batch in data_loader:
        images = batch["image"].to(device, non_blocking=True)
        labels = batch["label"].to(device, non_blocking=True)
        logits = model(images)
        loss = criterion(logits, labels)
        if not torch.isfinite(loss):
            raise RuntimeError("Validation loss is NaN/Inf.")
        losses.append(float(loss.detach().cpu().item()))
        confusion = update_confusion_matrix(
            confusion=confusion,
            logits=logits.detach().cpu(),
            targets=labels.detach().cpu(),
            num_classes=num_classes,
            ignore_index=ignore_index,
        )

    metric_dict = compute_metrics_from_confusion(confusion)
    metric_dict["val_loss"] = float(sum(losses) / max(1, len(losses)))
    metric_dict["confusion_matrix"] = confusion.tolist()
    return metric_dict


def maybe_resume(
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LambdaLR,
    scaler: torch.cuda.amp.GradScaler,
    resume_path: Optional[Path],
    device: torch.device,
    logger: Any,
) -> TrainState:
    """如配置了 resume_from，则从 checkpoint 恢复训练状态。"""
    if resume_path is None:
        return TrainState(epoch=0, global_step=0, best_val_miou=-1.0, best_epoch=0)
    payload = load_checkpoint(resume_path, device=device)
    model.load_state_dict(payload["model_state_dict"], strict=True)
    if "optimizer_state_dict" in payload:
        optimizer.load_state_dict(payload["optimizer_state_dict"])
    if "scheduler_state_dict" in payload:
        scheduler.load_state_dict(payload["scheduler_state_dict"])
    if "scaler_state_dict" in payload:
        scaler.load_state_dict(payload["scaler_state_dict"])
    state = TrainState(
        epoch=int(payload.get("epoch", 0)),
        global_step=int(payload.get("global_step", 0)),
        best_val_miou=float(payload.get("best_val_miou", -1.0)),
        best_epoch=int(payload.get("best_epoch", 0)),
    )
    logger.info("Resume from checkpoint: %s (epoch=%d)", resume_path, state.epoch)
    return state


def main() -> None:
    """执行正式训练主流程。"""
    args = parse_args()
    cfg = load_config(args.config)
    config_path = Path(cfg["_meta"]["resolved_config_path"])

    experiment_cfg = cfg.get("experiment", {})
    data_cfg = cfg.get("data", {})
    loader_cfg = cfg.get("loader", {})
    model_cfg = cfg.get("model", {})
    train_cfg = cfg.get("train", {})
    optimizer_cfg = cfg.get("optimizer", {})
    scheduler_cfg = cfg.get("scheduler", {})
    loss_cfg = cfg.get("loss", {})

    run_paths = get_run_paths(cfg, config_path)
    ensure_run_dirs(run_paths)
    logger = setup_logger(run_paths["logs_dir"] / "train.log", logger_name="formal_train")

    config_snapshot_path = run_paths["run_dir"] / "config_snapshot.yaml"
    save_yaml(config_snapshot_path, cfg)
    logger.info("Config snapshot saved: %s", config_snapshot_path)

    env_info = gather_env_info()
    env_info_path = run_paths["run_dir"] / "env_info.json"
    write_json(env_info_path, env_info)
    logger.info("Environment info saved: %s", env_info_path)

    fingerprint_result = verify_dataset_fingerprint(cfg, config_path)
    fingerprint_path = run_paths["run_dir"] / "fingerprint_check.json"
    write_json(fingerprint_path, fingerprint_result)
    logger.info("Fingerprint check result saved: %s", fingerprint_path)
    if fingerprint_result.get("enabled") and fingerprint_result.get("status") != "PASSED":
        raise RuntimeError(f"Fingerprint verification failed: {fingerprint_result.get('errors', [])}")

    seed = int(experiment_cfg.get("seed", 42))
    set_global_seed(
        seed=seed,
        deterministic=bool(experiment_cfg.get("deterministic", True)),
        cudnn_benchmark=bool(experiment_cfg.get("cudnn_benchmark", False)),
    )
    logger.info("Seed initialized: %d", seed)

    dataset_root = resolve_project_path(data_cfg["dataset_root"], config_path)
    manifest_path = resolve_project_path(data_cfg["manifest_path"], config_path)
    manifest_df = read_manifest(manifest_path)
    logger.info("Loaded manifest rows: %d | path=%s", len(manifest_df), manifest_path)

    train_df = manifest_df[manifest_df["final_split"] == "train"].copy().reset_index(drop=True)
    val_df = manifest_df[manifest_df["final_split"] == "val"].copy().reset_index(drop=True)
    if train_df.empty or val_df.empty:
        raise ValueError("Train or val split is empty in manifest.")

    if bool(data_cfg.get("validate_files_on_start", True)):
        missing_errors = validate_manifest_files(
            df=pd.concat([train_df, val_df], axis=0, ignore_index=True),
            dataset_root=dataset_root,
            image_dirname=data_cfg["image_dirname"],
            label_dirname=data_cfg["label_dirname"],
            image_suffix=data_cfg["image_suffix"],
            label_suffix=data_cfg["label_suffix"],
        )
        if missing_errors:
            preview = "\n".join(missing_errors[:10])
            raise FileNotFoundError(f"Missing dataset files ({len(missing_errors)}). First errors:\n{preview}")

    num_classes = int(data_cfg["num_classes"])
    ignore_index = int(data_cfg.get("ignore_index", 255))
    train_dataset = SegmentationTileDataset(
        manifest_df=train_df,
        dataset_root=dataset_root,
        image_dirname=data_cfg["image_dirname"],
        label_dirname=data_cfg["label_dirname"],
        image_suffix=data_cfg["image_suffix"],
        label_suffix=data_cfg["label_suffix"],
        num_classes=num_classes,
        ignore_index=ignore_index,
    )
    val_dataset = SegmentationTileDataset(
        manifest_df=val_df,
        dataset_root=dataset_root,
        image_dirname=data_cfg["image_dirname"],
        label_dirname=data_cfg["label_dirname"],
        image_suffix=data_cfg["image_suffix"],
        label_suffix=data_cfg["label_suffix"],
        num_classes=num_classes,
        ignore_index=ignore_index,
    )
    train_loader = build_dataloader(
        dataset=train_dataset,
        batch_size=int(loader_cfg.get("batch_size", 2)),
        shuffle=True,
        loader_cfg=loader_cfg,
        is_train=True,
    )
    val_loader = build_dataloader(
        dataset=val_dataset,
        batch_size=int(loader_cfg.get("eval_batch_size", loader_cfg.get("batch_size", 2))),
        shuffle=False,
        loader_cfg=loader_cfg,
        is_train=False,
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    amp_enabled = bool(train_cfg.get("amp", True)) and device.type == "cuda"
    logger.info("Device=%s | AMP=%s | backbone=%s", device, amp_enabled, model_cfg.get("backbone"))

    model = build_model_from_config(cfg).to(device)
    criterion = nn.CrossEntropyLoss(ignore_index=int(loss_cfg.get("ignore_index", ignore_index)))
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(optimizer_cfg.get("lr", 1e-4)),
        weight_decay=float(optimizer_cfg.get("weight_decay", 1e-2)),
        betas=tuple(optimizer_cfg.get("betas", [0.9, 0.999])),
        eps=float(optimizer_cfg.get("eps", 1e-8)),
    )
    max_epochs = int(train_cfg.get("max_epochs", 80))
    scheduler = build_poly_scheduler(
        optimizer=optimizer,
        max_epochs=max_epochs,
        warmup_epochs=int(scheduler_cfg.get("warmup_epochs", 3)),
        power=float(scheduler_cfg.get("power", 0.9)),
        min_lr=float(scheduler_cfg.get("min_lr", 1e-6)),
        base_lr=float(optimizer_cfg.get("lr", 1e-4)),
    )
    scaler = torch.cuda.amp.GradScaler(enabled=amp_enabled)

    resume_from = experiment_cfg.get("resume_from")
    resume_path = resolve_project_path(resume_from, config_path) if resume_from else None
    state = maybe_resume(
        model=model,
        optimizer=optimizer,
        scheduler=scheduler,
        scaler=scaler,
        resume_path=resume_path,
        device=device,
        logger=logger,
    )

    grad_clip = float(train_cfg.get("grad_clip_norm", 1.0))
    grad_accum_steps = max(1, int(train_cfg.get("grad_accum_steps", 1)))
    log_every = max(1, int(train_cfg.get("log_every_n_steps", 20)))
    val_every = max(1, int(train_cfg.get("val_every_n_epochs", 1)))
    early_patience = max(1, int(train_cfg.get("early_stopping_patience", 12)))

    val_rows: List[Dict[str, Any]] = []
    best_confusion: Optional[List[List[int]]] = None
    non_improve_epochs = 0

    logger.info("Training started: epochs=%d, train_batches=%d", max_epochs, len(train_loader))
    start_epoch = state.epoch + 1
    for epoch in range(start_epoch, max_epochs + 1):
        model.train()
        epoch_losses: List[float] = []
        optimizer.zero_grad(set_to_none=True)

        for batch_idx, batch in enumerate(train_loader, start=1):
            images = batch["image"].to(device, non_blocking=True)
            labels = batch["label"].to(device, non_blocking=True)
            with torch.autocast(device_type=device.type, enabled=amp_enabled):
                logits = model(images)
                loss = criterion(logits, labels) / grad_accum_steps
            if not torch.isfinite(loss):
                raise RuntimeError(f"Encountered NaN/Inf loss at epoch={epoch}, batch={batch_idx}")

            scaler.scale(loss).backward()
            epoch_losses.append(float(loss.detach().cpu().item() * grad_accum_steps))

            if batch_idx % grad_accum_steps == 0 or batch_idx == len(train_loader):
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=grad_clip)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                state.global_step += 1

                if state.global_step % log_every == 0:
                    logger.info(
                        "epoch=%d step=%d train_loss=%.6f lr=%.6e",
                        epoch,
                        state.global_step,
                        epoch_losses[-1],
                        optimizer.param_groups[0]["lr"],
                    )

        scheduler.step()
        train_loss = float(sum(epoch_losses) / max(1, len(epoch_losses)))

        do_val = (epoch % val_every == 0) or (epoch == max_epochs)
        current_val_loss = float("nan")
        current_val_miou = float("nan")
        current_val_mf1 = float("nan")
        current_val_oa = float("nan")
        if do_val:
            val_metrics = evaluate(
                model=model,
                data_loader=val_loader,
                criterion=criterion,
                device=device,
                num_classes=num_classes,
                ignore_index=ignore_index,
            )
            current_val_loss = float(val_metrics["val_loss"])
            current_val_miou = float(val_metrics["miou"])
            current_val_mf1 = float(val_metrics["mf1"])
            current_val_oa = float(val_metrics["overall_accuracy"])
            val_rows.append(
                {
                    "epoch": epoch,
                    "global_step": state.global_step,
                    "train_loss": train_loss,
                    "val_loss": current_val_loss,
                    "val_miou": current_val_miou,
                    "val_mf1": current_val_mf1,
                    "val_overall_accuracy": current_val_oa,
                    "lr": optimizer.param_groups[0]["lr"],
                }
            )
            logger.info(
                "epoch=%d train_loss=%.6f val_loss=%.6f val_miou=%.6f val_mf1=%.6f val_oa=%.6f",
                epoch,
                train_loss,
                current_val_loss,
                current_val_miou,
                current_val_mf1,
                current_val_oa,
            )

            if current_val_miou > state.best_val_miou:
                state.best_val_miou = current_val_miou
                state.best_epoch = epoch
                best_confusion = val_metrics["confusion_matrix"]
                non_improve_epochs = 0
                save_checkpoint(
                    checkpoint_path=run_paths["checkpoints_dir"] / "best.pth",
                    model=model,
                    optimizer=optimizer,
                    train_state=TrainState(
                        epoch=epoch,
                        global_step=state.global_step,
                        best_val_miou=state.best_val_miou,
                        best_epoch=state.best_epoch,
                    ),
                    config=cfg,
                    scheduler=scheduler,
                    scaler=scaler,
                )
                logger.info("Best checkpoint updated at epoch=%d (val_miou=%.6f)", epoch, current_val_miou)
            else:
                non_improve_epochs += val_every

        save_checkpoint(
            checkpoint_path=run_paths["checkpoints_dir"] / "last.pth",
            model=model,
            optimizer=optimizer,
            train_state=TrainState(
                epoch=epoch,
                global_step=state.global_step,
                best_val_miou=state.best_val_miou,
                best_epoch=state.best_epoch,
            ),
            config=cfg,
            scheduler=scheduler,
            scaler=scaler,
        )

        if val_rows:
            write_csv(
                path=run_paths["metrics_dir"] / "val_metrics.csv",
                rows=val_rows,
                fieldnames=[
                    "epoch",
                    "global_step",
                    "train_loss",
                    "val_loss",
                    "val_miou",
                    "val_mf1",
                    "val_overall_accuracy",
                    "lr",
                ],
            )

        if non_improve_epochs >= early_patience:
            logger.info(
                "Early stopping triggered at epoch=%d (no improvement for %d epochs).",
                epoch,
                non_improve_epochs,
            )
            break

    if best_confusion is not None:
        conf_rows: List[Dict[str, Any]] = []
        for row_idx, row_vals in enumerate(best_confusion):
            row: Dict[str, Any] = {"class_idx": row_idx}
            for col_idx, value in enumerate(row_vals):
                row[f"pred_{col_idx}"] = int(value)
            conf_rows.append(row)
        write_csv(
            path=run_paths["metrics_dir"] / "confusion_matrix_val.csv",
            rows=conf_rows,
            fieldnames=["class_idx"] + [f"pred_{i}" for i in range(num_classes)],
        )
    logger.info("Formal training completed successfully.")


if __name__ == "__main__":
    main()
