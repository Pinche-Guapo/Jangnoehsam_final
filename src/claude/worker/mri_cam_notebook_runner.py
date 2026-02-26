from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

HARDCODED_BASE_DIR = "/Users/machanho/Desktop/uv/ad"
PLANE_ORDER: Tuple[str, ...] = ("axial", "coronal", "sagittal")
PLANE_META: Dict[str, Tuple[int, str]] = {
    "axial": (0, "Axial"),
    "coronal": (1, "Coronal"),
    "sagittal": (2, "Sagittal"),
}

_RUNTIME_LOCK = threading.Lock()
_CAM_LOCK = threading.Lock()
_RUNTIME: Dict[str, Any] | None = None


def _severity_from_percentage(percentage: float) -> str:
    if percentage >= 35.0:
        return "high"
    if percentage >= 20.0:
        return "medium"
    return "low"


def _sanitize_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_existing_path(candidates: List[Path], kind: str) -> Path:
    for path in candidates:
        if path and path.exists():
            return path
    raise FileNotFoundError(f"Could not resolve {kind}. candidates={candidates}")


def _resolve_runtime_paths() -> Dict[str, Path]:
    root = _repo_root()

    notebook_env = os.getenv("MRI_CAM_NOTEBOOK_PATH", "").strip()
    notebook_candidates = [
        Path(notebook_env) if notebook_env else None,
        root / "models" / "dig_help" / "inference_cam_5.ipynb",
        Path("/models/dig_help/inference_cam_5.ipynb"),
        Path("/app/models/dig_help/inference_cam_5.ipynb"),
    ]
    notebook_path = _resolve_existing_path(
        [p for p in notebook_candidates if p is not None],
        kind="inference_cam_5.ipynb",
    )

    cn_env = os.getenv("MRI_CAM_CN_MODEL_PATH", "").strip()
    ci_env = os.getenv("MRI_CAM_CI_MODEL_PATH", "").strip()
    model_dir_env = os.getenv("MRI_MODEL_DIR", "").strip()
    model_dir = Path(model_dir_env) if model_dir_env else None

    cn_candidates = [
        Path(cn_env) if cn_env else None,
        (model_dir / "cn_f189.pth") if model_dir else None,
        (model_dir / "model_cn.pth") if model_dir else None,
        root / "models" / "dig_help" / "cn_f189.pth",
        root / "models" / "dig_help" / "model_cn.pth",
        Path("/app/models/dig_help/cn_f189.pth"),
        Path("/app/models/dig_help/model_cn.pth"),
    ]
    ci_candidates = [
        Path(ci_env) if ci_env else None,
        (model_dir / "ci_f184.pth") if model_dir else None,
        (model_dir / "model_ci.pth") if model_dir else None,
        root / "models" / "dig_help" / "ci_f184.pth",
        root / "models" / "dig_help" / "model_ci.pth",
        Path("/app/models/dig_help/ci_f184.pth"),
        Path("/app/models/dig_help/model_ci.pth"),
    ]

    cn_model_path = _resolve_existing_path([p for p in cn_candidates if p is not None], kind="CN model")
    ci_model_path = _resolve_existing_path([p for p in ci_candidates if p is not None], kind="CI model")

    runtime_base = root / ".tmp_notebook_cam_runtime"
    runtime_base.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "runtime_base": runtime_base,
        "notebook_path": notebook_path,
        "cn_model_path": cn_model_path,
        "ci_model_path": ci_model_path,
    }


def _code_cell_map(notebook_path: Path) -> Dict[int, str]:
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    code_map: Dict[int, str] = {}
    for index, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        code_map[index] = "".join(cell.get("source", []))
    return code_map


def _exec_cell(namespace: Dict[str, Any], code: str, cell_index: int) -> None:
    compiled = compile(code, f"inference_cam_5.ipynb#cell{cell_index}", "exec")
    exec(compiled, namespace)


def _build_runtime() -> Dict[str, Any]:
    paths = _resolve_runtime_paths()
    code_map = _code_cell_map(paths["notebook_path"])

    missing = [idx for idx in (1, 3, 5, 7, 9, 11, 19, 21) if idx not in code_map]
    if missing:
        raise RuntimeError(f"Notebook missing required code cells: {missing}")

    runtime: Dict[str, Any] = {}
    _exec_cell(runtime, code_map[1], 1)

    # Keep original code intact while redirecting hardcoded BASE_DIR at runtime.
    original_path_ctor = runtime.get("Path", Path)

    def _patched_path(value: str | os.PathLike[str], *args, **kwargs):
        resolved = original_path_ctor(value, *args, **kwargs)
        if str(resolved) == HARDCODED_BASE_DIR:
            return paths["runtime_base"]
        return resolved

    runtime["Path"] = _patched_path
    _exec_cell(runtime, code_map[3], 3)
    runtime["Path"] = original_path_ctor

    runtime["CN_MODEL_PATH"] = paths["cn_model_path"]
    runtime["CI_MODEL_PATH"] = paths["ci_model_path"]
    runtime["OUTPUT_DIR"] = paths["runtime_base"]

    _exec_cell(runtime, code_map[5], 5)
    _exec_cell(runtime, code_map[7], 7)
    _exec_cell(runtime, code_map[9], 9)

    # Execute only utility definitions from cell 11.
    cell11 = code_map[11]
    utility_part = cell11.split("# 메타데이터 로드", 1)[0]
    _exec_cell(runtime, utility_part, 11)

    _exec_cell(runtime, code_map[19], 19)
    _exec_cell(runtime, code_map[21], 21)
    runtime["plt"].ioff()

    logger.info(
        "Notebook CAM runtime loaded: notebook=%s cn=%s ci=%s",
        paths["notebook_path"],
        paths["cn_model_path"],
        paths["ci_model_path"],
    )
    return runtime


def _get_runtime() -> Dict[str, Any]:
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = _build_runtime()
    return _RUNTIME


def _choose_stage(runtime: Dict[str, Any], final_label: str) -> Tuple[Any, int, str, str]:
    normalized = str(final_label or "").strip().upper()
    if normalized == "CN":
        return runtime["cn_model"], 0, "Stage1_CN-vs-CI", "CN"
    if normalized == "AD":
        return runtime["ci_model"], 1, "Stage2_MCI-vs-AD", "AD"
    return runtime["ci_model"], 0, "Stage2_MCI-vs-AD", "MCI"


def _safe_mid_index(volume_np: np.ndarray, mask_for_profile: np.ndarray, dim: int) -> int:
    foreground_coords = np.argwhere(mask_for_profile > 0.0)
    if foreground_coords.size:
        min_idx = int(foreground_coords[:, dim].min())
        max_idx = int(foreground_coords[:, dim].max())
        return int(round((min_idx + max_idx) / 2.0))
    if dim == 0:
        return int(volume_np.shape[0] // 2)
    if dim == 1:
        return int(volume_np.shape[1] // 2)
    return int(volume_np.shape[2] // 2)


def _cam_peak_index(cam_np: np.ndarray, mask_for_profile: np.ndarray, dim: int) -> int | None:
    positive = np.maximum(cam_np, 0.0) * mask_for_profile
    valid_positive = positive[positive > 0.0]
    if valid_positive.size == 0:
        return None

    high_thr = float(np.percentile(valid_positive, 85))
    high_mask = positive >= high_thr

    if dim == 0:
        high_profile = high_mask.sum(axis=(1, 2)).astype(np.float32)
        cam_profile = positive.sum(axis=(1, 2)).astype(np.float32)
        fg_profile = mask_for_profile.sum(axis=(1, 2)).astype(np.float32)
    elif dim == 1:
        high_profile = high_mask.sum(axis=(0, 2)).astype(np.float32)
        cam_profile = positive.sum(axis=(0, 2)).astype(np.float32)
        fg_profile = mask_for_profile.sum(axis=(0, 2)).astype(np.float32)
    else:
        high_profile = high_mask.sum(axis=(0, 1)).astype(np.float32)
        cam_profile = positive.sum(axis=(0, 1)).astype(np.float32)
        fg_profile = mask_for_profile.sum(axis=(0, 1)).astype(np.float32)

    max_fg = float(np.max(fg_profile)) if fg_profile.size else 0.0
    min_fg = max(16.0, max_fg * 0.05)
    valid = fg_profile >= min_fg
    if not np.any(valid):
        valid = fg_profile > 0.0
    if not np.any(valid):
        return None

    high_profile = np.where(valid, high_profile, -np.inf)
    if np.isfinite(high_profile).any() and float(np.nanmax(high_profile)) > 0.0:
        return int(np.nanargmax(high_profile))

    cam_profile = np.where(valid, cam_profile, -np.inf)
    if np.isfinite(cam_profile).any() and float(np.nanmax(cam_profile)) > 0.0:
        return int(np.nanargmax(cam_profile))
    return None


def _resolve_slice_mode(plane: str) -> str:
    global_mode = str(os.getenv("MRI_CAM_SLICE_MODE", "cam_peak")).strip().lower()
    per_plane_mode = str(os.getenv(f"MRI_CAM_{plane.upper()}_SLICE_MODE", "")).strip().lower()
    if per_plane_mode:
        return per_plane_mode
    if plane == "axial":
        # Keep axial closer to original MRI view by default.
        axial_mode = str(os.getenv("MRI_CAM_AXIAL_SLICE_MODE", "safe_mid")).strip().lower()
        return axial_mode or global_mode
    return global_mode


def _apply_plane_shift(plane: str, index: int, shape: Tuple[int, int, int]) -> int:
    dim = PLANE_META[plane][0]
    depth = int(shape[dim])
    if depth <= 0:
        return index

    total_shift = 0
    if plane == "axial":
        total_shift += int(os.getenv("MRI_AXIAL_SLICE_SHIFT", "0"))
    total_shift += int(os.getenv(f"MRI_CAM_{plane.upper()}_EXTRA_SHIFT", "0"))

    if total_shift == 0:
        return int(np.clip(index, 0, depth - 1))
    return int(np.clip(index + total_shift, 0, depth - 1))


def _build_profile_mask(volume_np: np.ndarray, brain_mask: np.ndarray) -> np.ndarray:
    """Build a stable foreground mask used for slice indexing."""
    mask_for_profile = np.asarray(brain_mask > 0.0, dtype=np.float32)
    coverage = float(mask_for_profile.mean()) if mask_for_profile.size else 0.0
    if coverage < 0.02 or coverage > 0.98:
        median_val = float(np.median(volume_np))
        dynamic_eps = max(
            1e-3,
            float(np.percentile(volume_np, 95) - np.percentile(volume_np, 5)) * 0.03,
        )
        mask_for_profile = np.asarray(np.abs(volume_np - median_val) > dynamic_eps, dtype=np.float32)
        if float(mask_for_profile.mean()) < 0.02:
            mask_for_profile = np.asarray(
                volume_np > np.percentile(volume_np, 60),
                dtype=np.float32,
            )
    return mask_for_profile


def _slice_index_from_percent(mask_for_profile: np.ndarray, dim: int, ratio: float) -> int:
    ratio = max(0.0, min(1.0, float(ratio)))
    depth = int(mask_for_profile.shape[dim])
    coords = np.argwhere(mask_for_profile > 0.0)
    if coords.size:
        lo = int(coords[:, dim].min())
        hi = int(coords[:, dim].max())
    else:
        lo, hi = 0, max(0, depth - 1)
    if hi < lo:
        lo, hi = hi, lo
    return int(round(lo + (hi - lo) * ratio))


def _save_roi_plane_overlay(
    runtime: Dict[str, Any],
    volume_np: np.ndarray,
    cam_np: np.ndarray,
    brain_mask: np.ndarray,
    center: List[int],
    roi_sigma: float,
    plane: str,
    output_path: Path,
) -> int:
    dim, _ = PLANE_META[plane]
    # Use a robust foreground profile for slice selection.
    mask_for_profile = _build_profile_mask(volume_np, brain_mask)

    safe_center = [
        int(np.clip(int(center[0]) if len(center) > 0 else volume_np.shape[0] // 2, 0, volume_np.shape[0] - 1)),
        int(np.clip(int(center[1]) if len(center) > 1 else volume_np.shape[1] // 2, 0, volume_np.shape[1] - 1)),
        int(np.clip(int(center[2]) if len(center) > 2 else volume_np.shape[2] // 2, 0, volume_np.shape[2] - 1)),
    ]
    sigma = max(float(roi_sigma or 6.0), 1.0)
    zz, yy, xx = np.indices(volume_np.shape, dtype=np.float32)
    dist2 = (
        ((zz - float(safe_center[0])) / sigma) ** 2
        + ((yy - float(safe_center[1])) / sigma) ** 2
        + ((xx - float(safe_center[2])) / sigma) ** 2
    )
    roi_weight = np.exp(-0.5 * dist2).astype(np.float32)
    roi_weight = np.where(dist2 <= 6.25, roi_weight, 0.0).astype(np.float32)
    roi_cam = np.maximum(cam_np, 0.0) * roi_weight
    if float(np.max(roi_cam)) <= 0.0:
        roi_cam = np.maximum(cam_np, 0.0) * mask_for_profile

    # Slice mode:
    # - cam_peak: chooses the slice with strongest/highest CAM occupancy.
    # - safe_mid: midpoint of foreground bounds.
    # Axial defaults to safe_mid for better visual alignment with original MRI.
    slice_mode = _resolve_slice_mode(plane)
    safe_mid_index = _safe_mid_index(volume_np, mask_for_profile, dim)
    if slice_mode == "safe_mid":
        safe_index = safe_mid_index
    else:
        safe_index = _cam_peak_index(roi_cam, mask_for_profile, dim)
        if safe_index is None:
            safe_index = safe_mid_index

    safe_index = _apply_plane_shift(plane, safe_index, volume_np.shape)

    # Keep full anatomical shape on base image (no base masking).
    if dim == 0:
        base = volume_np[safe_index, :, :]
        mask_2d = mask_for_profile[safe_index, :, :]
    elif dim == 1:
        base = volume_np[:, safe_index, :]
        mask_2d = mask_for_profile[:, safe_index, :]
    else:
        base = volume_np[:, :, safe_index]
        mask_2d = mask_for_profile[:, :, safe_index]

    if dim == 0:
        heat = roi_cam[safe_index, :, :]
    elif dim == 1:
        heat = roi_cam[:, safe_index, :]
    else:
        heat = roi_cam[:, :, safe_index]

    threshold = float(runtime["percentile_in_mask"](roi_cam, brain_mask, 90, fallback=0.5))
    heat_masked = runtime["_mask_heat"](heat, threshold, mask_2d)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    try:
        ax.imshow(base, cmap="gray", interpolation="bilinear")
        ax.imshow(heat_masked, cmap="turbo", alpha=0.55, vmin=threshold, vmax=1.0, interpolation="bilinear")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.savefig(output_path, dpi=160, bbox_inches="tight", pad_inches=0.02)
    finally:
        plt.close(fig)

    return safe_index


def _save_dense_plane_overlay(
    runtime: Dict[str, Any],
    volume_np: np.ndarray,
    cam_np: np.ndarray,
    brain_mask: np.ndarray,
    plane: str,
    slice_ratio: float,
    output_path: Path,
) -> int:
    """Save one CAM overlay image for a dense slice position in a given plane."""
    dim, _ = PLANE_META[plane]
    mask_for_profile = _build_profile_mask(volume_np, brain_mask)
    safe_index = _slice_index_from_percent(mask_for_profile, dim, slice_ratio)
    safe_index = _apply_plane_shift(plane, safe_index, volume_np.shape)

    if dim == 0:
        base = volume_np[safe_index, :, :]
        mask_2d = mask_for_profile[safe_index, :, :]
        heat = np.maximum(cam_np[safe_index, :, :], 0.0)
    elif dim == 1:
        base = volume_np[:, safe_index, :]
        mask_2d = mask_for_profile[:, safe_index, :]
        heat = np.maximum(cam_np[:, safe_index, :], 0.0)
    else:
        base = volume_np[:, :, safe_index]
        mask_2d = mask_for_profile[:, :, safe_index]
        heat = np.maximum(cam_np[:, :, safe_index], 0.0)

    threshold = float(runtime["percentile_in_mask"](cam_np, brain_mask, 90, fallback=0.5))
    heat_masked = runtime["_mask_heat"](heat, threshold, mask_2d)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    try:
        ax.imshow(base, cmap="gray", interpolation="bilinear")
        ax.imshow(heat_masked, cmap="turbo", alpha=0.55, vmin=threshold, vmax=1.0, interpolation="bilinear")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.savefig(output_path, dpi=160, bbox_inches="tight", pad_inches=0.02)
    finally:
        plt.close(fig)

    return safe_index


def generate_attention_from_notebook(
    nifti_path: str,
    output_dir: str,
    subject_id: str,
    final_label: str,
    run_token: str | None = None,
    top_k: int = 5,
    mode: str = "topk",
    dense_slices_per_plane: int = 100,
) -> Dict[str, Any]:
    runtime = _get_runtime()

    subject_token = str(subject_id or "subject").strip() or "subject"
    run_tag = str(run_token or "latest").strip() or "latest"
    out_dir = Path(output_dir).expanduser().resolve() / "notebook_cam"
    out_dir.mkdir(parents=True, exist_ok=True)

    with _CAM_LOCK:
        volume = runtime["load_nifti"](nifti_path)
        volume_on_device = volume.to(runtime["DEVICE"])
        volume_np = volume[0, 0].detach().cpu().numpy()
        brain_mask = runtime["create_brain_mask"](volume_np, threshold=0.05)

        model, target_class, stage_name, target_label = _choose_stage(runtime, final_label)
        with torch.enable_grad():
            target_layer = runtime["_resolve_target_layer"](model)
            cam_generator = runtime["GradCAM3D"](model, target_layer)
            try:
                cam_raw = cam_generator(volume_on_device, class_idx=target_class)
            finally:
                cam_generator.remove()

            cam_up = F.interpolate(
                cam_raw,
                size=runtime["TARGET_SHAPE"],
                mode="trilinear",
                align_corners=False,
            )
            cam_np = cam_up.detach().cpu().numpy()[0, 0]

        masked_cam = cam_np * brain_mask
        roi_scores = sorted(
            runtime["compute_roi_abnormality"](masked_cam, runtime["ROI_DEFINITIONS"]),
            key=lambda item: item["score"],
            reverse=True,
        )
        top_rois = roi_scores[: max(1, int(top_k))]

        # Execute notebook's original ROI Top-K visualization entrypoint as-is.
        try:
            runtime["visualize_roi_cam"](
                volume_np,
                cam_np,
                "Grad-CAM",
                roi_scores,
                runtime["ROI_DEFINITIONS"],
                stage_name,
                f"{subject_token}_{run_tag}",
                out_dir,
                top_k=max(1, int(top_k)),
                brain_mask=brain_mask,
            )
        except Exception as notebook_vis_exc:
            logger.warning("Notebook ROI visualization failed (continuing): %s", notebook_vis_exc)

        total_score = float(sum(max(float(item["score"]), 0.0) for item in top_rois))
        region_contributions = []
        for roi_item in top_rois:
            roi_name = str(roi_item.get("name") or "")
            roi_score = float(roi_item.get("score") or 0.0)
            roi_percentage = (max(roi_score, 0.0) / total_score * 100.0) if total_score > 0 else 0.0
            region_contributions.append(
                {
                    "region": roi_name,
                    "percentage": round(float(roi_percentage), 1),
                    "severity": _severity_from_percentage(float(roi_percentage)),
                }
            )

        slides: List[Dict[str, Any]] = []
        requested_mode = str(mode or "topk").strip().lower()
        cam_mode = requested_mode if requested_mode in {"topk", "dense"} else "topk"

        if cam_mode == "dense":
            dense_count = max(8, int(dense_slices_per_plane or 100))
            stage_slug = _sanitize_slug(stage_name) or "stage"
            for rank in range(1, dense_count + 1):
                ratio = 0.5 if dense_count <= 1 else float(rank - 1) / float(dense_count - 1)
                local_paths: Dict[str, str] = {}
                slice_indices: Dict[str, int] = {}
                for plane in PLANE_ORDER:
                    image_name = (
                        f"{_sanitize_slug(subject_token) or 'subject'}_"
                        f"{_sanitize_slug(run_tag) or 'latest'}_"
                        f"{stage_slug}_dense_{rank:03d}_{plane}.png"
                    )
                    image_path = out_dir / image_name
                    slice_index = _save_dense_plane_overlay(
                        runtime=runtime,
                        volume_np=volume_np,
                        cam_np=cam_np,
                        brain_mask=brain_mask,
                        plane=plane,
                        slice_ratio=ratio,
                        output_path=image_path,
                    )
                    local_paths[plane] = str(image_path)
                    slice_indices[plane] = int(slice_index)

                slides.append(
                    {
                        "rank": int(rank),
                        "roi": f"dense_{rank:03d}",
                        "description": f"Dense slice {rank}/{dense_count}",
                        "score": 0.0,
                        "percentage": 0.0,
                        "severity": "low",
                        "sliceIndices": slice_indices,
                        "local_paths": local_paths,
                    }
                )
        else:
            for rank, roi_item in enumerate(top_rois, start=1):
                roi_name = str(roi_item.get("name") or f"roi_{rank}")
                roi_description = str(roi_item.get("description") or roi_name)
                roi_score = float(roi_item.get("score") or 0.0)
                roi_info = runtime["ROI_DEFINITIONS"].get(roi_name, {})
                roi_center = [int(v) for v in roi_info.get("center", [48, 56, 48])]
                roi_sigma = float(roi_info.get("sigma", 6.0))
                roi_percentage = (max(roi_score, 0.0) / total_score * 100.0) if total_score > 0 else 0.0

                local_paths: Dict[str, str] = {}
                slice_indices: Dict[str, int] = {}
                roi_slug = _sanitize_slug(roi_name) or f"roi_{rank}"
                stage_slug = _sanitize_slug(stage_name) or "stage"

                for plane in PLANE_ORDER:
                    image_name = (
                        f"{_sanitize_slug(subject_token) or 'subject'}_"
                        f"{_sanitize_slug(run_tag) or 'latest'}_"
                        f"{stage_slug}_r{rank:02d}_{roi_slug}_{plane}.png"
                    )
                    image_path = out_dir / image_name
                    slice_index = _save_roi_plane_overlay(
                        runtime=runtime,
                        volume_np=volume_np,
                        cam_np=cam_np,
                        brain_mask=brain_mask,
                        center=roi_center,
                        roi_sigma=roi_sigma,
                        plane=plane,
                        output_path=image_path,
                    )
                    local_paths[plane] = str(image_path)
                    slice_indices[plane] = int(slice_index)

                slides.append(
                    {
                        "rank": int(rank),
                        "roi": roi_name,
                        "description": roi_description,
                        "score": round(roi_score, 6),
                        "percentage": round(float(roi_percentage), 1),
                        "severity": _severity_from_percentage(float(roi_percentage)),
                        "sliceIndices": slice_indices,
                        "local_paths": local_paths,
                    }
                )

        roi_scores_ranked = [
            {
                "roi": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
                "score": round(float(item.get("score") or 0.0), 6),
            }
            for item in roi_scores[:10]
        ]

        first_local_paths = slides[0]["local_paths"] if slides else {}
        return {
            "method": "GradCAM3D_notebook",
            "mode": cam_mode,
            "stage": stage_name,
            "targetLabel": target_label,
            "targetClassIndex": int(target_class),
            "roiScores": roi_scores_ranked,
            "regionContributions": region_contributions,
            "slides": slides,
            "local_paths": first_local_paths,
            "local_path": first_local_paths.get("axial"),
        }
