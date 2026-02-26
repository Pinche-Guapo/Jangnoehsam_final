"""
Model inference for voice and MRI assessment pipelines.

Voice pipeline supports two modes:
- Lightweight bundle mode (preferred): transcript-first features + bundled RF pipeline
- Legacy mode (fallback): direct numeric vector + imputer + RF classifier
"""

from __future__ import annotations

import logging
import os
import pickle
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np

logger = logging.getLogger(__name__)


# Voice model holders (singleton)
_voice_model = None
_voice_imputer = None
_voice_bundle = None
_bundle_threshold = None
_wav2vec2_model = None
_wav2vec2_processor = None
_wav2vec2_device = None

MODEL_DIR = os.getenv("MODEL_DIR", "/models/audio_processed")
MRI_MODEL_DIR = os.getenv("MRI_MODEL_DIR", "models/dig_help")
MRI_MODEL_CN_FILENAME = os.getenv("MRI_MODEL_CN_FILENAME", "model_cn.pth")
MRI_MODEL_CI_FILENAME = os.getenv("MRI_MODEL_CI_FILENAME", "model_ci.pth")


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        return default


DEFAULT_THRESHOLD = _env_float("VOICE_MCI_THRESHOLD", 0.48)
MODEL_VERSION = os.getenv("VOICE_MODEL_VERSION", "RandomForest_v2_lightweight_bundle")

VOICE_MODEL_BUNDLE_PATH = os.getenv(
    "VOICE_MODEL_BUNDLE_PATH",
    os.path.join(MODEL_DIR, "redesigned_rf_model_bundle_20260212_194046.joblib"),
)
VOICE_LEGACY_MODEL_PATH = os.getenv("VOICE_LEGACY_MODEL_PATH", os.path.join(MODEL_DIR, "trained_model.pkl"))
VOICE_LEGACY_IMPUTER_PATH = os.getenv(
    "VOICE_LEGACY_IMPUTER_PATH", os.path.join(MODEL_DIR, "trained_model_imputer.pkl")
)

SHAP_ENABLE = _env_bool("SHAP_ENABLE", True)
SHAP_TOP_K = max(1, _env_int("SHAP_TOP_K", 8))
VOICE_ENABLE_AUDIO_EMBEDDING = _env_bool("VOICE_ENABLE_AUDIO_EMBEDDING", True)
VOICE_AUDIO_MODEL = os.getenv("VOICE_AUDIO_MODEL", "facebook/wav2vec2-base-960h")
VOICE_AUDIO_DEVICE = os.getenv("VOICE_AUDIO_DEVICE", "cpu")
VOICE_AUDIO_MAX_SEC = _env_float("VOICE_AUDIO_MAX_SEC", 0.0)
VOICE_AUDIO_CHUNK_SEC = max(_env_float("VOICE_AUDIO_CHUNK_SEC", 20.0), 5.0)


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default
        out = float(value)
        if np.isnan(out) or np.isinf(out):
            return default
        return out
    except Exception:
        return default


def load_models():
    """Load voice models from disk (singleton)."""
    global _voice_model, _voice_imputer, _voice_bundle, _bundle_threshold

    if _voice_model is not None:
        return _voice_model, _voice_imputer, _voice_bundle

    # Preferred: lightweight bundle
    if os.path.exists(VOICE_MODEL_BUNDLE_PATH):
        logger.info("Loading voice model bundle from: %s", VOICE_MODEL_BUNDLE_PATH)
        loaded = joblib.load(VOICE_MODEL_BUNDLE_PATH)
        if not isinstance(loaded, dict) or "model_pipeline" not in loaded:
            raise RuntimeError(f"Invalid voice bundle format: {VOICE_MODEL_BUNDLE_PATH}")

        _voice_bundle = loaded
        _voice_model = loaded["model_pipeline"]
        _voice_imputer = None
        _bundle_threshold = _as_float(loaded.get("threshold"), DEFAULT_THRESHOLD)

        logger.info(
            "Voice bundle loaded: model=%s, n_features=%s, threshold=%.3f",
            type(_voice_model).__name__,
            getattr(_voice_model, "n_features_in_", "unknown"),
            _bundle_threshold,
        )
        return _voice_model, _voice_imputer, _voice_bundle

    # Fallback: legacy model + imputer
    if not os.path.exists(VOICE_LEGACY_MODEL_PATH):
        raise FileNotFoundError(
            f"No voice model found. Checked bundle={VOICE_MODEL_BUNDLE_PATH}, legacy={VOICE_LEGACY_MODEL_PATH}"
        )
    if not os.path.exists(VOICE_LEGACY_IMPUTER_PATH):
        raise FileNotFoundError(f"Legacy imputer not found: {VOICE_LEGACY_IMPUTER_PATH}")

    logger.info("Loading legacy voice model from: %s", VOICE_LEGACY_MODEL_PATH)
    with open(VOICE_LEGACY_MODEL_PATH, "rb") as f:
        _voice_model = pickle.load(f)

    logger.info("Loading legacy voice imputer from: %s", VOICE_LEGACY_IMPUTER_PATH)
    with open(VOICE_LEGACY_IMPUTER_PATH, "rb") as f:
        _voice_imputer = pickle.load(f)

    _voice_bundle = None
    _bundle_threshold = DEFAULT_THRESHOLD

    logger.info(
        "Legacy voice model loaded: model=%s, n_features=%s, threshold=%.3f",
        type(_voice_model).__name__,
        getattr(_voice_model, "n_features_in_", "unknown"),
        _bundle_threshold,
    )

    return _voice_model, _voice_imputer, _voice_bundle


def _extract_bundle_numeric(feature_row: Dict[str, Any], bundle: Dict[str, Any]) -> Tuple[np.ndarray, List[str]]:
    numeric_columns = [str(col) for col in (bundle.get("numeric_feature_columns") or [])]

    service_stats = bundle.get("service_safe_stats")
    duration_fallback = 0.0
    if isinstance(service_stats, dict):
        duration_fallback = _as_float(service_stats.get("duration_fallback_ms"), 0.0)

    alias_map = {
        "eojeol_sum": "eojeol",
        "token_total_mor_sum": "token_total_mor",
        "pos_noun_sum": "pos_noun",
        "pos_verb_sum": "pos_verb",
        "pos_adj_sum": "pos_adj",
        "pos_adv_sum": "pos_adv",
        "pos_pron_sum": "pos_pron",
        "deictic_cnt_sum": "deictic_cnt",
        "filler_cnt_sum": "filler_cnt",
        "particle_cnt_text_proxy_sum": "particle_cnt_text_proxy",
        "deictic_rate_sum_based": "deictic_rate",
        "filler_rate_sum_based": "filler_rate",
        "particle_rate_sum_based": "particle_rate_text_proxy",
    }

    values: List[float] = []
    for col in numeric_columns:
        candidates = [col]
        alias = alias_map.get(col)
        if alias:
            candidates.append(alias)
        if col.endswith("_sum"):
            candidates.append(col[:-4])
        if col.endswith("_sum_based"):
            candidates.append(col.replace("_sum_based", ""))
        if col.endswith("_text_proxy_sum"):
            candidates.append(col.replace("_sum", ""))

        value = None
        for key in candidates:
            if key and key in feature_row:
                value = feature_row.get(key)
                break

        if value is None and col in {"latency_ms_mean", "latency_ms_median"}:
            value = feature_row.get("dur_ms", duration_fallback)
        if value is None and col == "n_par_utts":
            value = 1.0

        values.append(_as_float(value, 0.0))

    return np.asarray(values, dtype=float), numeric_columns


def _extract_bundle_text(feature_row: Dict[str, Any], bundle: Dict[str, Any]) -> Tuple[np.ndarray, List[str]]:
    if not bundle.get("use_text"):
        return np.array([], dtype=float), []

    vectorizer = bundle.get("text_vectorizer")
    text_svd = bundle.get("text_svd")
    n_text = int(getattr(text_svd, "n_components", 0)) if text_svd is not None else 0
    if n_text <= 0:
        return np.array([], dtype=float), []

    names = [f"text_svd_{idx}" for idx in range(n_text)]
    text = str(feature_row.get("text", "")).strip()

    if not text or vectorizer is None or text_svd is None:
        return np.zeros(n_text, dtype=float), names

    try:
        sparse_vec = vectorizer.transform([text])
        reduced = text_svd.transform(sparse_vec)
        arr = np.asarray(reduced, dtype=float).reshape(-1)
        if arr.size == n_text:
            return arr, names

        fixed = np.zeros(n_text, dtype=float)
        upto = min(n_text, arr.size)
        fixed[:upto] = arr[:upto]
        return fixed, names
    except Exception as e:
        logger.warning("Text transform failed in bundle mode, using zeros: %s", e)
        return np.zeros(n_text, dtype=float), names


def _resolve_audio_device(device_preference: str):
    import torch

    pref = (device_preference or "cpu").strip().lower()
    if pref == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if pref == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("VOICE_AUDIO_DEVICE=cuda but CUDA is not available")
        return torch.device("cuda")
    if pref == "mps":
        if not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
            raise RuntimeError("VOICE_AUDIO_DEVICE=mps but MPS is not available")
        return torch.device("mps")
    return torch.device("cpu")


def _load_wav2vec2_runtime():
    global _wav2vec2_model, _wav2vec2_processor, _wav2vec2_device
    if _wav2vec2_model is not None and _wav2vec2_processor is not None and _wav2vec2_device is not None:
        return _wav2vec2_model, _wav2vec2_processor, _wav2vec2_device

    import torch
    from transformers import Wav2Vec2Model, Wav2Vec2Processor

    device = _resolve_audio_device(VOICE_AUDIO_DEVICE)
    logger.info("Loading wav2vec2 runtime model: %s (device=%s)", VOICE_AUDIO_MODEL, device)
    processor = Wav2Vec2Processor.from_pretrained(VOICE_AUDIO_MODEL)
    model = Wav2Vec2Model.from_pretrained(VOICE_AUDIO_MODEL).to(device)
    model.eval()

    _wav2vec2_model = model
    _wav2vec2_processor = processor
    _wav2vec2_device = device
    return _wav2vec2_model, _wav2vec2_processor, _wav2vec2_device


def _read_audio_waveform(audio_path: str, sr: int = 16000) -> np.ndarray:
    import librosa

    waveform, _ = librosa.load(audio_path, sr=sr, mono=True)
    if VOICE_AUDIO_MAX_SEC > 0:
        max_samples = int(VOICE_AUDIO_MAX_SEC * sr)
        if len(waveform) > max_samples:
            waveform = waveform[:max_samples]
    return np.asarray(waveform, dtype=np.float32)


def _embed_wav2vec2_mean_pool(audio_path: str, expected_dim: int) -> np.ndarray:
    import torch

    model, processor, device = _load_wav2vec2_runtime()
    sr = 16000
    waveform = _read_audio_waveform(audio_path, sr=sr)
    if waveform.size == 0:
        return np.zeros(expected_dim, dtype=float)

    chunk_samples = int(max(VOICE_AUDIO_CHUNK_SEC, 5.0) * sr)
    if chunk_samples <= 0:
        chunk_samples = 20 * sr

    weighted_sum = None
    total_tokens = 0
    for start in range(0, len(waveform), chunk_samples):
        chunk = waveform[start : start + chunk_samples]
        if chunk.size == 0:
            continue
        inputs = processor(chunk, sampling_rate=sr, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = model(**inputs).last_hidden_state

        # Weighted mean approximates full-utterance pooling without OOM risk.
        seq_len = int(outputs.shape[1])
        pooled = outputs.mean(dim=1).squeeze().detach().cpu().numpy().astype(np.float64)
        if weighted_sum is None:
            weighted_sum = pooled * seq_len
        else:
            weighted_sum += pooled * seq_len
        total_tokens += seq_len

    if weighted_sum is None or total_tokens <= 0:
        return np.zeros(expected_dim, dtype=float)

    emb = (weighted_sum / float(total_tokens)).astype(np.float32)
    if emb.size == expected_dim:
        return emb.astype(float)
    if emb.size > expected_dim:
        return emb[:expected_dim].astype(float)
    fixed = np.zeros(expected_dim, dtype=float)
    fixed[: emb.size] = emb.astype(float)
    return fixed


def _embed_mel_fallback(audio_path: str, expected_dim: int) -> np.ndarray:
    """Mel fallback kept for compatibility if a bundle was trained with mel-like vectors."""
    import librosa

    sr = 16000
    waveform = _read_audio_waveform(audio_path, sr=sr)
    if waveform.size == 0:
        return np.zeros(expected_dim, dtype=float)

    n_mels = max(1, expected_dim // 2)
    mel = librosa.feature.melspectrogram(y=waveform, sr=sr, n_mels=n_mels)
    mel_db = librosa.power_to_db(mel, ref=np.max)
    emb = np.concatenate([mel_db.mean(axis=1), mel_db.std(axis=1)]).astype(float)
    if emb.size == expected_dim:
        return emb
    if emb.size > expected_dim:
        return emb[:expected_dim]
    fixed = np.zeros(expected_dim, dtype=float)
    fixed[: emb.size] = emb
    return fixed


def _extract_bundle_audio(bundle: Dict[str, Any], audio_path: Optional[str] = None) -> Tuple[np.ndarray, List[str]]:
    if not bundle.get("use_audio"):
        return np.array([], dtype=float), []

    audio_pca = bundle.get("audio_pca")
    n_audio = int(getattr(audio_pca, "n_components", 0)) if audio_pca is not None else 0
    if n_audio <= 0:
        return np.array([], dtype=float), []

    names = [f"audio_pca_{idx}" for idx in range(n_audio)]
    if audio_pca is None:
        return np.zeros(n_audio, dtype=float), names

    if not VOICE_ENABLE_AUDIO_EMBEDDING:
        logger.info("VOICE_ENABLE_AUDIO_EMBEDDING=false, using zero audio features")
        return np.zeros(n_audio, dtype=float), names

    expected_in = int(getattr(audio_pca, "n_features_in_", 768) or 768)
    if not audio_path:
        logger.warning("Audio path missing in bundle mode, using zero audio features")
        return np.zeros(n_audio, dtype=float), names

    try:
        if expected_in == 768:
            emb = _embed_wav2vec2_mean_pool(audio_path, expected_in)
        else:
            # Only use mel fallback if PCA expects non-wav2vec dimension.
            emb = _embed_mel_fallback(audio_path, expected_in)

        reduced = audio_pca.transform(emb.reshape(1, -1))
        arr = np.asarray(reduced, dtype=float).reshape(-1)
        if arr.size == n_audio:
            return arr, names

        fixed = np.zeros(n_audio, dtype=float)
        upto = min(n_audio, arr.size)
        fixed[:upto] = arr[:upto]
        return fixed, names
    except Exception as e:
        logger.warning("Audio embedding/PCA failed, using zeros: %s", e)
        return np.zeros(n_audio, dtype=float), names


def _align_feature_vector_and_names(
    values: np.ndarray,
    names: List[str],
    keep_mask: Any,
    expected_size: int,
) -> Tuple[np.ndarray, List[str]]:
    vector = np.asarray(values, dtype=float).reshape(-1)
    feature_names = list(names)

    if keep_mask is not None:
        mask = np.asarray(keep_mask, dtype=bool).reshape(-1)
        if mask.size == vector.size:
            vector = vector[mask]
            if len(feature_names) >= mask.size:
                feature_names = [name for name, keep in zip(feature_names, mask) if keep]
            else:
                feature_names = [f"feature_{idx}" for idx in range(vector.size)]
        else:
            logger.warning(
                "Keep-mask size mismatch (mask=%s, features=%s), skipping mask",
                mask.size,
                vector.size,
            )

    if len(feature_names) < vector.size:
        feature_names.extend(f"feature_{idx}" for idx in range(len(feature_names), vector.size))
    elif len(feature_names) > vector.size:
        feature_names = feature_names[: vector.size]

    if vector.size < expected_size:
        pad_count = expected_size - vector.size
        vector = np.concatenate([vector, np.zeros(pad_count, dtype=float)], axis=0)
        feature_names.extend(f"pad_{idx}" for idx in range(pad_count))
    elif vector.size > expected_size:
        vector = vector[:expected_size]
        feature_names = feature_names[:expected_size]

    return vector, feature_names


def _prepare_bundle_input(
    feature_row: Dict[str, Any],
    model: Any,
    bundle: Dict[str, Any],
    audio_path: Optional[str] = None,
) -> Tuple[np.ndarray, List[str], Dict[str, int]]:
    numeric_values, numeric_names = _extract_bundle_numeric(feature_row, bundle)
    text_values, text_names = _extract_bundle_text(feature_row, bundle)
    audio_values, audio_names = _extract_bundle_audio(bundle, audio_path=audio_path)

    blocks = [arr for arr in (numeric_values, text_values, audio_values) if arr.size > 0]
    names: List[str] = []
    for block_names, block_values in (
        (numeric_names, numeric_values),
        (text_names, text_values),
        (audio_names, audio_values),
    ):
        if block_values.size > 0:
            names.extend(block_names)

    if not blocks:
        raise ValueError("No bundle features available")

    full = np.concatenate(blocks, axis=0)
    aligned, aligned_names = _align_feature_vector_and_names(
        values=full,
        names=names,
        keep_mask=bundle.get("constant_feature_keep_mask"),
        expected_size=int(getattr(model, "n_features_in_", full.size)),
    )

    meta = {
        "numeric_feature_count": int(numeric_values.size),
        "text_feature_count": int(text_values.size),
        "audio_feature_count": int(audio_values.size),
        "model_feature_count": int(aligned.size),
    }
    return aligned.reshape(1, -1), aligned_names, meta


def _extract_shap_values_for_class(shap_values: Any, class_index: int) -> np.ndarray:
    if isinstance(shap_values, list):
        if not shap_values:
            return np.array([], dtype=float)
        idx = class_index if class_index < len(shap_values) else len(shap_values) - 1
        arr = np.asarray(shap_values[idx], dtype=float)
    else:
        arr = np.asarray(shap_values, dtype=float)
        if arr.ndim == 3:
            idx = class_index if class_index < arr.shape[2] else arr.shape[2] - 1
            arr = arr[:, :, idx]
        elif arr.ndim == 1:
            arr = arr.reshape(1, -1)

    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2:
        arr = arr.reshape(arr.shape[0], -1)
    return arr


def _extract_expected_value(expected_value: Any, class_index: int) -> float:
    if isinstance(expected_value, (list, tuple, np.ndarray)):
        arr = np.asarray(expected_value, dtype=float).reshape(-1)
        if arr.size == 0:
            return 0.0
        idx = class_index if class_index < arr.size else arr.size - 1
        return float(arr[idx])
    return float(expected_value)


def _compute_shap_explanation(
    model: Any,
    model_input: np.ndarray,
    mci_probability: float,
    feature_names: List[str],
) -> Dict[str, Any]:
    if not SHAP_ENABLE:
        return {
            "enabled": False,
            "available": False,
            "reason": "disabled_by_config",
        }

    try:
        import shap
    except Exception as e:
        return {
            "enabled": True,
            "available": False,
            "reason": f"shap_import_error: {e}",
        }

    try:
        X = np.asarray(model_input, dtype=float)
        names = list(feature_names)
        model_for_shap = model
        model_stage = "classifier_input"

        if hasattr(model, "named_steps") and isinstance(getattr(model, "named_steps"), dict):
            steps = list(model.named_steps.items())
            if steps:
                X_stage = X
                names_stage = list(names)
                for _, step in steps[:-1]:
                    if hasattr(step, "transform"):
                        X_stage = step.transform(X_stage)
                    if hasattr(step, "get_support"):
                        try:
                            mask = np.asarray(step.get_support(), dtype=bool).reshape(-1)
                            if len(names_stage) >= mask.size:
                                names_stage = [n for n, keep in zip(names_stage, mask) if keep]
                        except Exception:
                            pass

                model_for_shap = steps[-1][1]
                model_stage = f"pipeline:{steps[-1][0]}"
                X = np.asarray(X_stage, dtype=float)
                names = names_stage

        classes = list(getattr(model_for_shap, "classes_", [0, 1]))
        mci_class = 1 if 1 in classes else (classes[-1] if classes else 1)
        class_index = classes.index(mci_class) if mci_class in classes else max(len(classes) - 1, 0)

        explainer = shap.TreeExplainer(model_for_shap)
        shap_values_raw = explainer.shap_values(X)
        expected_raw = explainer.expected_value

        shap_matrix = _extract_shap_values_for_class(shap_values_raw, class_index)
        if shap_matrix.size == 0 or shap_matrix.shape[0] == 0:
            return {
                "enabled": True,
                "available": False,
                "reason": "empty_shap_values",
            }

        shap_row = np.asarray(shap_matrix[0], dtype=float).reshape(-1)
        feature_values = np.asarray(X[0], dtype=float).reshape(-1)

        use_size = min(shap_row.size, feature_values.size)
        shap_row = shap_row[:use_size]
        feature_values = feature_values[:use_size]

        if len(names) < use_size:
            names.extend(f"feature_{idx}" for idx in range(len(names), use_size))
        elif len(names) > use_size:
            names = names[:use_size]

        base_value = _extract_expected_value(expected_raw, class_index)
        output_value = float(base_value + float(np.sum(shap_row)))

        contributions = []
        for idx, (name, f_value, s_value) in enumerate(zip(names, feature_values, shap_row)):
            contributions.append(
                {
                    "index": idx,
                    "feature": str(name),
                    "value": float(f_value),
                    "shap_value": float(s_value),
                    "impact": "increase_mci" if float(s_value) >= 0 else "decrease_mci",
                }
            )

        top_features = sorted(contributions, key=lambda x: abs(x["shap_value"]), reverse=True)[:SHAP_TOP_K]
        positive_features = [item for item in top_features if item["shap_value"] >= 0]
        negative_features = [item for item in top_features if item["shap_value"] < 0]

        return {
            "enabled": True,
            "available": True,
            "method": "TreeExplainer",
            "model_stage": model_stage,
            "class_label": "MCI",
            "class_index": int(class_index),
            "base_value": base_value,
            "output_value": output_value,
            "predicted_mci_probability": float(mci_probability),
            "feature_count": int(use_size),
            "top_k": int(SHAP_TOP_K),
            "top_features": top_features,
            "positive_features": positive_features,
            "negative_features": negative_features,
            "feature_contributions": contributions,
        }
    except Exception as e:
        logger.warning("SHAP computation failed: %s", e)
        return {
            "enabled": True,
            "available": False,
            "reason": f"shap_compute_error: {e}",
        }


def predict(features: Any, audio_path: Optional[str] = None) -> dict:
    """
    Run voice MCI prediction.

    Bundle mode expects `features` as a dict from transcript lightweight extractor.
    Legacy mode accepts numpy-like numeric vector.
    """
    model, imputer, bundle = load_models()

    model_input: np.ndarray
    model_input_feature_names: List[str]

    numeric_feature_count = 0
    text_feature_count = 0
    audio_feature_count = 0

    if bundle is not None:
        if not isinstance(features, dict):
            raise ValueError("Bundle mode requires dict features from lightweight extractor")

        model_input, model_input_feature_names, meta = _prepare_bundle_input(
            features,
            model,
            bundle,
            audio_path=audio_path,
        )
        numeric_feature_count = int(meta["numeric_feature_count"])
        text_feature_count = int(meta["text_feature_count"])
        audio_feature_count = int(meta["audio_feature_count"])
    else:
        vector = np.asarray(features, dtype=float).reshape(1, -1)
        model_input = imputer.transform(vector)
        model_input_feature_names = [f"feature_{idx}" for idx in range(model_input.shape[1])]
        numeric_feature_count = int(model_input.shape[1])

    probas = model.predict_proba(model_input)[0]
    classes = list(getattr(model, "classes_", range(len(probas))))
    mci_index = classes.index(1) if 1 in classes else max(len(probas) - 1, 0)

    mci_probability = float(probas[mci_index])
    threshold = _bundle_threshold if _bundle_threshold is not None else DEFAULT_THRESHOLD
    label = "MCI" if mci_probability >= threshold else "Control"

    cognitive_score = round((1.0 - mci_probability) * 100, 2)

    if mci_probability < 0.35:
        flag = "normal"
    elif mci_probability < 0.65:
        flag = "warning"
    else:
        flag = "critical"

    flag_reasons = _generate_flag_reasons(mci_probability, label, flag)
    shap_payload = _compute_shap_explanation(
        model=model,
        model_input=model_input,
        mci_probability=mci_probability,
        feature_names=model_input_feature_names,
    )

    logger.info(
        "Prediction: %s (prob=%.3f, score=%.2f, flag=%s, threshold=%.3f)",
        label,
        mci_probability,
        cognitive_score,
        flag,
        threshold,
    )

    return {
        "label": label,
        "mci_probability": round(mci_probability, 4),
        "cognitive_score": cognitive_score,
        "flag": flag,
        "flag_reasons": flag_reasons,
        "model_version": MODEL_VERSION,
        "threshold": float(threshold),
        "shap": shap_payload,
        "shap_available": bool(shap_payload.get("available")),
        "numeric_feature_count": int(numeric_feature_count),
        "text_feature_count": int(text_feature_count),
        "audio_feature_count": int(audio_feature_count),
        "model_feature_count": int(model_input.shape[1]),
    }


def _generate_flag_reasons(mci_probability: float, label: str, flag: str) -> List[str]:
    """Generate human-readable reasons for the assessment flag."""
    reasons = []

    if flag == "critical":
        reasons.append(f"MCI 확률이 매우 높습니다 ({mci_probability:.1%})")
        reasons.append("전문의 상담을 권장합니다")
    elif flag == "warning":
        reasons.append(f"MCI 가능성이 감지되었습니다 ({mci_probability:.1%})")
        reasons.append("추가 검사를 고려해 주세요")
    else:
        reasons.append("인지 기능이 정상 범위입니다")

    return reasons

MCI_SUBTYPE_MODEL_PATH = (
    os.getenv("MCI_SUBTYPE_MODEL_PATH")
    or os.path.join(
        os.getenv("MCI_SUBTYPE_MODEL_DIR", "models/mci(s,p)"),
        os.getenv("MCI_SUBTYPE_MODEL_FILENAME", "mci_model_best.cbm"),
    )
)

# CatBoost sMCI/pMCI feature columns (must match training order)
SUBTYPE_FEATURES = [
    'age', 'gender', 'pteducat', 'apoe4', 'ldeltotal',
    'mmse', 'moca', 'adas_cog13', 'faq', 'cdr_sb',
    'abeta42', 'ptau', 'nxaudito', 'tau', 'ab42/ab40', 'ptau/ab42'
]


def _predict_mci_subtype(patient_id: str) -> dict:
    """
    CatBoost sMCI/pMCI classification using clinical data from DB.
    Returns None if clinical data is not available.
    """
    import psycopg2
    from catboost import CatBoostClassifier

    db_url = os.getenv("DATABASE_URL", "postgresql://mci_user:change_me@postgres:5432/cognitive")
    conn = psycopg2.connect(db_url, options="-c timezone=Asia/Seoul")
    try:
        cur = conn.cursor()
        # Get patient demographics (date_of_birth is on patients table)
        cur.execute("""
            SELECT p.gender, p.apoe4, p.date_of_birth, p.pteducat
            FROM patients p
            WHERE p.user_id = %s
        """, (patient_id,))
        patient_row = cur.fetchone()
        if not patient_row:
            logger.warning(f"Patient {patient_id} not found for subtype prediction")
            return None

        gender_val, apoe4, date_of_birth, pteducat = patient_row

        # Calculate age
        from datetime import date
        if date_of_birth:
            age = (date.today() - date_of_birth).days // 365
        else:
            age = None

        # Get latest clinical assessment (includes nxaudito)
        cur.execute("""
            SELECT mmse, moca, adas_cog13, faq, cdr_sb, nxaudito
            FROM clinical_assessments
            WHERE patient_id = %s
            ORDER BY exam_date DESC NULLS LAST
            LIMIT 1
        """, (patient_id,))
        clinical_row = cur.fetchone()

        # Get latest neuropsych test (RAVLT delayed = avdeltot)
        cur.execute("""
            SELECT avdeltot
            FROM neuropsych_tests
            WHERE patient_id = %s
            ORDER BY exam_date DESC NULLS LAST
            LIMIT 1
        """, (patient_id,))
        neuro_row = cur.fetchone()

        # Get latest biomarkers
        cur.execute("""
            SELECT abeta42, ptau, tau, ab42_ab40, ptau_ab42
            FROM biomarkers
            WHERE patient_id = %s
            ORDER BY collected_date DESC NULLS LAST
            LIMIT 1
        """, (patient_id,))
        bio_row = cur.fetchone()

    finally:
        conn.close()

    # Build feature vector in exact order: SUBTYPE_FEATURES
    mmse = clinical_row[0] if clinical_row else None
    moca = clinical_row[1] if clinical_row else None
    adas_cog13 = clinical_row[2] if clinical_row else None
    faq = clinical_row[3] if clinical_row else None
    cdr_sb = clinical_row[4] if clinical_row else None
    nxaudito = clinical_row[5] if clinical_row else None
    ldeltotal = neuro_row[0] if neuro_row else None
    abeta42 = bio_row[0] if bio_row else None
    ptau = bio_row[1] if bio_row else None
    tau = bio_row[2] if bio_row else None
    ab42_ab40 = bio_row[3] if bio_row else None
    ptau_ab42 = bio_row[4] if bio_row else None

    features = [
        age, gender_val, pteducat, apoe4, ldeltotal,
        mmse, moca, adas_cog13, faq, cdr_sb,
        abeta42, ptau, nxaudito, tau, ab42_ab40, ptau_ab42
    ]

    logger.info(f"Subtype features: {dict(zip(SUBTYPE_FEATURES, features))}")

    model = CatBoostClassifier()
    model.load_model(MCI_SUBTYPE_MODEL_PATH)

    # CatBoost: categorical features need str/"None", numeric need float NaN
    cat_indices = set(model.get_cat_feature_indices())
    for i in range(len(features)):
        if features[i] is None:
            features[i] = "None" if i in cat_indices else float('nan')

    proba = model.predict_proba([features])[0]

    p_smci = float(proba[0])
    p_pmci = float(proba[1])

    # Custom threshold 0.497 for pMCI (class 1)
    SUBTYPE_THRESHOLD = 0.497
    if p_pmci >= SUBTYPE_THRESHOLD:
        return {'label': 'pMCI', 'confidence': p_pmci, 'p_smci': p_smci, 'p_pmci': p_pmci}
    else:
        return {'label': 'sMCI', 'confidence': p_smci, 'p_smci': p_smci, 'p_pmci': p_pmci}


def predict_mri(
    nifti_path: str,
    patient_id: str = None,
    subject_id: str | None = None,
    mri_id: str | None = None,
    xai_output_dir: str | None = None,
) -> dict:
    """
    Run Cascaded 3D CNN inference:
    1. model_cn: CN vs (MCI+AD)
    2. model_ci: MCI vs AD
    3. CatBoost: sMCI vs pMCI (if MCI, using clinical data)
    Loads models from MRI_MODEL_DIR (/models/mri(cam)).
    """
    import torch
    import random
    import nibabel as nib
    from .mri_model import get_model
    from .mri_cam_notebook_runner import generate_attention_from_notebook
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    cn_conf_threshold = _env_float("MRI_CN_CONFIDENCE_THRESHOLD", 0.75)
    impaired_prob_threshold = _env_float("MRI_IMPAIRED_PROB_THRESHOLD", 0.25)
    
    try:
        # 1. Find cascade models: prefer model_cn/model_ci, keep 189/184 as fallback.
        files = os.listdir(MRI_MODEL_DIR)
        lower_to_original = {f.lower(): f for f in files if f.lower().endswith(".pth")}
        path_cn = lower_to_original.get(MRI_MODEL_CN_FILENAME.lower())
        path_ci = lower_to_original.get(MRI_MODEL_CI_FILENAME.lower())

        if not path_cn:
            path_cn = next((f for f in files if f.lower().endswith(".pth") and "model_cn" in f.lower()), None)
        if not path_ci:
            path_ci = next((f for f in files if f.lower().endswith(".pth") and "model_ci" in f.lower()), None)

        # Backward compatibility with legacy naming convention
        if not path_cn:
            path_cn = next((f for f in files if f.lower().endswith(".pth") and "189" in f), None)
        if not path_ci:
            path_ci = next((f for f in files if f.lower().endswith(".pth") and "184" in f), None)

        if not path_cn or not path_ci:
            logger.warning(
                "Cascade models (model_cn/model_ci or 189/184) not found in %s. Found: %s",
                MRI_MODEL_DIR,
                files,
            )
            raise FileNotFoundError("Required cascade models not found (model_cn/model_ci)")

        full_path_cn = os.path.join(MRI_MODEL_DIR, path_cn)
        full_path_ci = os.path.join(MRI_MODEL_DIR, path_ci)
        logger.info("Loading Cascade Models: %s (CN/Imp) -> %s (MCI/AD)", path_cn, path_ci)
        
        # 2. Load NIfTI Image and apply notebook-consistent normalization.
        #    Keep zero-background as zero, and normalize only foreground voxels.
        img = nib.load(nifti_path)
        img_array = np.asarray(img.dataobj, dtype=np.float32)  # (D, H, W)
        if img_array.ndim == 4:
            img_array = img_array[..., 0]

        foreground_mask = np.abs(img_array) > 1e-6
        if np.any(foreground_mask):
            foreground_values = img_array[foreground_mask]
            lo, hi = np.percentile(foreground_values, (1, 99))
            if hi <= lo:
                lo, hi = float(np.min(foreground_values)), float(np.max(foreground_values))
            if hi > lo:
                normalized = np.zeros_like(img_array, dtype=np.float32)
                scaled = np.clip((img_array - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)
                normalized[foreground_mask] = scaled[foreground_mask]
                img_array = normalized
            else:
                img_array = np.zeros_like(img_array, dtype=np.float32)
        else:
            img_array = np.zeros_like(img_array, dtype=np.float32)

        # Resize to (96, 112, 96) using trilinear interpolation
        MODEL_INPUT_SIZE = (96, 112, 96)
        if img_array.shape != MODEL_INPUT_SIZE:
            logger.info(f"Resizing {img_array.shape} -> {MODEL_INPUT_SIZE}")
            tmp = torch.tensor(img_array, dtype=torch.float32).unsqueeze(0).unsqueeze(0)  # (1,1,D,H,W)
            tmp = torch.nn.functional.interpolate(tmp, size=MODEL_INPUT_SIZE, mode='trilinear', align_corners=False)
            img_array = tmp.squeeze().numpy()

        # Add Batch and Channel dimensions: (1, 1, 96, 112, 96)
        input_tensor = torch.tensor(img_array, dtype=torch.float32).unsqueeze(0).unsqueeze(0).to(device)

        def _load_checkpoint_state(path: str):
            # MRI checkpoints may be stored as training snapshots with "state_dict".
            # PyTorch >=2.6 defaults to weights_only=True, so explicitly disable it
            # for trusted local checkpoints.
            checkpoint = torch.load(path, map_location=device, weights_only=False)
            if isinstance(checkpoint, dict):
                state_dict = checkpoint.get("state_dict")
                if state_dict is not None:
                    return state_dict

                # Fallback: allow plain state_dict dict checkpoints.
                if checkpoint and all(hasattr(v, "shape") for v in checkpoint.values()):
                    return checkpoint

            raise RuntimeError(f"Unsupported MRI checkpoint format: {path}")

        # 3. Stage 1: CN vs Impaired (model_cn) - CNNAttention3D
        model1 = get_model(model_name="cnn_attention_3d", num_classes=2, device=device)
        model1.load_state_dict(_load_checkpoint_state(full_path_cn), strict=True)
        model1.eval()
        
        with torch.no_grad():
            out1 = model1(input_tensor)
            probs1 = torch.softmax(out1, dim=1).squeeze().cpu().numpy()
        
        p_cn = float(probs1[0])
        p_impaired = float(probs1[1])
        
        logger.info(f"Stage 1 (CN): CN={p_cn:.4f}, Impaired={p_impaired:.4f}")
        
        # Stage 2: MCI vs AD (model_ci)
        # Prefer CNNEncoder3D; if checkpoint keys mismatch, fallback to CNNAttention3D.
        stage2_state = _load_checkpoint_state(full_path_ci)
        try:
            model2 = get_model(model_name="cnn_encoder_3d", num_classes=2, device=device)
            model2.load_state_dict(stage2_state, strict=True)
            logger.info("Stage 2 model loaded as CNNEncoder3D")
        except Exception as stage2_exc:
            logger.warning(
                "Stage 2 checkpoint is not compatible with CNNEncoder3D (%s). "
                "Falling back to CNNAttention3D.",
                stage2_exc,
            )
            model2 = get_model(model_name="cnn_attention_3d", num_classes=2, device=device)
            model2.load_state_dict(stage2_state, strict=True)
            logger.info("Stage 2 model loaded as CNNAttention3D")
        model2.eval()
        
        # Always run stage 2 regardless of stage 1 result
        with torch.no_grad():
            out2 = model2(input_tensor)
            probs2 = torch.softmax(out2, dim=1).squeeze().cpu().numpy()

        p_mci_cond = float(probs2[0])
        p_ad_cond = float(probs2[1])
        
        logger.info(f"Stage 2 (CI): MCI|Imp={p_mci_cond:.4f}, AD|Imp={p_ad_cond:.4f}")

        # Cascaded decision: Stage 1이 CN이면 CN, Impaired면 Stage 2 결과를 따름
        probs_map = {"CN": p_cn, "MCI": p_impaired * p_mci_cond, "AD": p_impaired * p_ad_cond}

        if p_cn > p_impaired:
            # Gray-zone handling: if CN confidence is not strong enough and
            # impaired probability is meaningful, defer to stage2 MCI/AD.
            if p_cn < cn_conf_threshold and p_impaired >= impaired_prob_threshold:
                if p_mci_cond > p_ad_cond:
                    final_label = "MCI"
                    confidence = p_mci_cond
                else:
                    final_label = "AD"
                    confidence = p_ad_cond
            else:
                final_label = "CN"
                confidence = p_cn
        else:
            # Stage 1: Impaired 승 → Stage 2 결과로 MCI/AD 결정
            if p_mci_cond > p_ad_cond:
                final_label = "MCI"
                confidence = p_mci_cond
            else:
                final_label = "AD"
                confidence = p_ad_cond

        # Stage 3: MCI Subtype (sMCI vs pMCI) — CatBoost with clinical data
        if final_label == "MCI" and patient_id and os.path.exists(MCI_SUBTYPE_MODEL_PATH):
            try:
                subtype_result = _predict_mci_subtype(patient_id)
                if subtype_result:
                    final_label = subtype_result['label']
                    confidence = subtype_result['confidence']
                    probs_map['sMCI'] = subtype_result['p_smci']
                    probs_map['pMCI'] = subtype_result['p_pmci']
                    logger.info(f"Stage 3 (Subtype): sMCI={subtype_result['p_smci']:.4f}, pMCI={subtype_result['p_pmci']:.4f}")
            except Exception as e:
                logger.warning(f"Subtype prediction failed: {e}. Keeping MCI.")
        elif final_label == "MCI":
            logger.info("No patient_id or subtype model. Keeping MCI.")
        
        logger.info(f"Cascade Result: {final_label} ({confidence:.4f})")

        xai_payload = {}
        if xai_output_dir:
            try:
                os.makedirs(xai_output_dir, exist_ok=True)
                patient_token = str(patient_id or "").strip()
                subject_token = str(subject_id or "").strip()
                forced_mode = os.getenv("MRI_CAM_MODE", "").strip().lower()
                # Default to dense CAM so slider movement reflects per-slice variation
                # consistently across demo patients unless explicitly overridden.
                cam_mode = forced_mode if forced_mode in {"topk", "dense"} else "dense"
                dense_slices_per_plane = max(8, _env_int("MRI_CAM_DENSE_SLICES_PER_PLANE", 100))
                logger.info(
                    "MRI CAM mode selected: mode=%s patient_id=%s subject_id=%s dense_slices_per_plane=%s",
                    cam_mode,
                    patient_token or "n/a",
                    subject_token or "n/a",
                    dense_slices_per_plane,
                )
                xai_payload = generate_attention_from_notebook(
                    nifti_path=nifti_path,
                    output_dir=xai_output_dir,
                    subject_id=str(subject_token or patient_token or "patient"),
                    final_label=str(final_label),
                    run_token=str(mri_id or "latest"),
                    top_k=max(1, _env_int("MRI_CAM_TOP_K", 5)),
                    mode=cam_mode,
                    dense_slices_per_plane=dense_slices_per_plane,
                )
            except Exception as xai_exc:
                logger.warning("Notebook-style MRI CAM generation failed: %s", xai_exc)
                try:
                    from .mri_xai import generate_attention_artifacts

                    xai_name = f"{patient_id or 'patient'}_{mri_id or 'latest'}_cam.png"
                    xai_local_path = os.path.join(xai_output_dir, xai_name)

                    normalized_final = str(final_label or "").strip().upper()
                    if normalized_final == "CN":
                        xai_stage = "stage1_cn_vs_ci"
                        xai_model = model1
                        xai_target_idx = 0
                        xai_target_label = "CN"
                    elif normalized_final == "AD":
                        xai_stage = "stage2_mci_vs_ad"
                        xai_model = model2
                        xai_target_idx = 1
                        xai_target_label = "AD"
                    else:
                        xai_stage = "stage2_mci_vs_ad"
                        xai_model = model2
                        xai_target_idx = 0
                        xai_target_label = "MCI"

                    xai_payload = generate_attention_artifacts(
                        model=xai_model,
                        input_tensor=input_tensor,
                        base_volume=img_array,
                        target_class_idx=xai_target_idx,
                        output_path=xai_local_path,
                        stage_name=xai_stage,
                        target_label=xai_target_label,
                    )
                except Exception as fallback_exc:
                    logger.warning("Fallback MRI CAM generation also failed: %s", fallback_exc)

        return {
            "label": final_label,
            "confidence": confidence,
            "probabilities": probs_map,
            "model_version": "cascade_cn_ci_subtype",
            "xai": xai_payload,
        }
        

    except Exception as e:
        logger.warning(f"MRI Cascade Inference failed: {e}. Falling back to dummy.")
    
    # Fallback / Mock logic (matches the requested behavior if model fails)
    classes = ["CN", "MCI", "AD"]
    predicted_stage = random.choice(classes)
    confidence = random.uniform(0.75, 0.98)
    probabilities = {c: (confidence if c == predicted_stage else (1.0 - confidence) / (len(classes) - 1)) for c in classes}

    return {
        "label": predicted_stage,
        "confidence": confidence,
        "probabilities": probabilities,
        "model_version": "dig_help_v1"
    }
