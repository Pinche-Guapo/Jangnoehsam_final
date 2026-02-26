"""
Celery tasks for ML processing pipelines.

This module contains async tasks for:
- Voice recording processing (transcript-first lightweight features + RandomForest)
- MRI scan processing (3D CNN + ResNet)
"""

import os
import json
import tempfile
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path
from typing import Optional, Tuple

from celery import Celery
from celery.utils.log import get_task_logger
# Lazy import: only needed when preprocessing from scratch
# from .mri_utils import preprocess_single_subject, convert_dicom_to_nifti

logger = get_task_logger(__name__)
KST = timezone(timedelta(hours=9))
SUBJECT_ID_PATTERN = re.compile(r"(\d{3}_S_\d{4})", re.IGNORECASE)


def _kst_now_naive() -> datetime:
    """Return current Korea time as naive datetime for TIMESTAMP columns."""
    return datetime.now(KST).replace(tzinfo=None)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Invalid int for {name}={raw!r}; fallback={default}")
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# Initialize Celery app
app = Celery(
    'mci-worker',
    broker=os.getenv('REDIS_URL', 'redis://redis:6379/0'),
    backend=os.getenv('REDIS_URL', 'redis://redis:6379/0')
)

# Configure Celery
_worker_prefetch = _int_env("CELERY_WORKER_PREFETCH_MULTIPLIER", 1)
_worker_max_tasks = _int_env("CELERY_WORKER_MAX_TASKS_PER_CHILD", 0)
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Seoul',
    enable_utc=False,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    worker_prefetch_multiplier=_worker_prefetch,  # Process one task at a time by default
    worker_max_tasks_per_child=(_worker_max_tasks if _worker_max_tasks > 0 else None),
    task_acks_late=_bool_env("CELERY_TASK_ACKS_LATE", False),
    task_reject_on_worker_lost=_bool_env("CELERY_TASK_REJECT_ON_WORKER_LOST", False),
    broker_pool_limit=_int_env("CELERY_BROKER_POOL_LIMIT", 10),
)

# Define MRI Template path relative to this file
# Location: src/claude/worker/templates/mni_icbm152_t1_tal_nlin_sym_09a.nii
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MRI_TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "mni_icbm152_t1_tal_nlin_sym_09a.nii")

def _get_minio_client():
    """Create MinIO client for file downloads."""
    from minio import Minio
    return Minio(
        os.getenv("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        secure=False,
    )


def _get_db_connection():
    """Create sync database connection for worker tasks."""
    import psycopg2
    db_url = os.getenv("DATABASE_URL", "postgresql://mci_user:change_me@postgres:5432/cognitive")
    return psycopg2.connect(db_url, options="-c timezone=Asia/Seoul")


def _resolve_bucket_and_key(file_path: str, default_bucket: str = "voice-recordings") -> Tuple[str, str]:
    """
    Accepts either:
    - "voice-recordings/path/to/file.wav"
    - "s3://voice-recordings/path/to/file.wav"
    - "path/to/file.wav" (defaults bucket to default_bucket)
    """
    path = (file_path or "").strip().replace("s3://", "")
    if not path:
        raise ValueError("file_path is empty")

    if "/" not in path:
        return "voice-recordings", path

    bucket, key = path.split("/", 1)
    known = {
        "voice-recordings",
        "voice-transcript",
        "processed",
        "mri-scans",
        "exports",
        "mri-preprocessed",
        "mri-xai",
    }
    if bucket in known:
        return bucket, key

    # path doesn't include bucket prefix, treat it as an object key
    return default_bucket, path


def _candidate_transcript_keys(recording_id: str, patient_id: Optional[str], audio_key: str) -> list[str]:
    """
    Return likely transcript object keys in priority order.

    New final convention:
    - {patient_id}/{recording_id}.txt
    Also support audio-relative sidecar:
    - {audio_key_without_ext}.txt
    """
    keys = []
    if patient_id and recording_id:
        keys.append(f"{patient_id}/{recording_id}.txt")

    if audio_key:
        audio_key = audio_key.strip("/")
        if "." in audio_key:
            keys.append(f"{audio_key.rsplit('.', 1)[0]}.txt")

    # De-duplicate while preserving order.
    deduped = []
    for key in keys:
        if key and key not in deduped:
            deduped.append(key)
    return deduped


def _load_transcript_from_minio(recording_id: str, patient_id: Optional[str], audio_key: str) -> Optional[str]:
    """Try loading transcript text from MinIO transcript bucket."""
    transcript_bucket = (os.getenv("MINIO_TRANSCRIPT_BUCKET", "voice-transcript") or "").strip() or "voice-transcript"
    minio_client = _get_minio_client()

    for key in _candidate_transcript_keys(recording_id, patient_id, audio_key):
        response = None
        try:
            response = minio_client.get_object(transcript_bucket, key)
            text = response.read().decode("utf-8", errors="replace").strip()
            if text:
                logger.info(f"Loaded transcript from MinIO: {transcript_bucket}/{key}")
                return text
        except Exception:
            continue
        finally:
            if response is not None:
                response.close()
                response.release_conn()
    return None


def _get_preprocessed_dir() -> str:
    """Return writable local cache directory for preprocessed MRI outputs."""
    env_dir = os.getenv("MCI_PREPROCESS_CACHE_DIR") or os.getenv("MCI_PROCESSED_DIR")
    if env_dir:
        path = Path(env_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    candidates = [
        Path("/tmp/mri-preprocessed-cache"),
        Path("/app/data/mri-preprocessed-cache"),
        Path("data/mri-preprocessed-cache"),
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        except Exception:
            continue

    raise RuntimeError("Could not resolve writable preprocessed cache directory")


def _get_preprocessed_bucket() -> str:
    """Bucket name for persistent preprocessed MRI artifacts."""
    bucket = os.getenv("MCI_PREPROCESSED_BUCKET", "mri-preprocessed").strip().lower()
    return bucket or "mri-preprocessed"


def _ensure_bucket_exists(minio_client, bucket: str) -> None:
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)


def _object_exists(minio_client, bucket: str, object_name: str) -> bool:
    try:
        minio_client.stat_object(bucket, object_name)
        return True
    except Exception:
        return False


def _download_object_if_exists(minio_client, bucket: str, object_name: str, local_path: str) -> bool:
    if not _object_exists(minio_client, bucket, object_name):
        return False
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    minio_client.fget_object(bucket, object_name, local_path)
    return True


def _upload_file_to_minio(minio_client, bucket: str, object_name: str, local_path: str) -> str:
    _ensure_bucket_exists(minio_client, bucket)
    minio_client.fput_object(bucket, object_name, local_path)
    return f"{bucket}/{object_name}"


def _download_mri_input_from_minio(file_path: str, work_dir: str, mri_id: str) -> str:
    """
    Download MRI source from MinIO and return local path.
    Supports:
    - Single object (NIfTI/DICOM)
    - Prefix/folder (DICOM series tree)
    """
    minio_client = _get_minio_client()
    bucket, key = _resolve_bucket_and_key(file_path, default_bucket="mri-scans")
    key = key.strip("/")

    # Case 1: exact object path
    try:
        minio_client.stat_object(bucket, key)
        ext = ".nii.gz" if key.endswith(".nii.gz") else Path(key).suffix
        if not ext:
            ext = ".dat"
        local_file = os.path.join(work_dir, f"{mri_id}_source{ext}")
        minio_client.fget_object(bucket, key, local_file)
        logger.info(f"Downloaded MRI object: {bucket}/{key} -> {local_file}")
        return local_file
    except Exception:
        pass

    # Case 2: prefix/folder path
    prefix = key if key.endswith("/") else f"{key}/"
    objects = [
        obj for obj in minio_client.list_objects(bucket, prefix=prefix, recursive=True)
        if getattr(obj, "object_name", None) and not obj.object_name.endswith("/")
    ]
    if not objects:
        raise FileNotFoundError(f"No MinIO object/prefix found for {bucket}/{key}")

    local_root = os.path.join(work_dir, "raw_mri")
    for obj in objects:
        rel_path = obj.object_name[len(prefix):] if obj.object_name.startswith(prefix) else obj.object_name
        target = os.path.join(local_root, rel_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        minio_client.fget_object(bucket, obj.object_name, target)

    logger.info(f"Downloaded MRI prefix: {bucket}/{prefix} -> {local_root} ({len(objects)} objects)")
    return local_root


def _find_dicom_series_dir(root_dir: str) -> Optional[str]:
    """Find the most complete DICOM series directory under root."""
    best_dir = None
    best_count = 0
    for root, _, files in os.walk(root_dir):
        dcm_files = [name for name in files if name.lower().endswith(".dcm")]
        if len(dcm_files) > best_count:
            best_count = len(dcm_files)
            best_dir = root
    return best_dir


def _extract_subject_id_from_path(path: Optional[str]) -> Optional[str]:
    text = str(path or "").strip()
    if not text:
        return None
    match = SUBJECT_ID_PATTERN.search(text)
    if not match:
        return None
    return match.group(1).upper()


@app.task(bind=True, name='process_voice_recording')
def process_voice_recording(
    self,
    recording_id: str,
    patient_id: Optional[str] = None,
    file_path: Optional[str] = None,
    transcript: Optional[str] = None,
):
    """
    Process voice recording for MCI assessment.

    Pipeline:
    1. Download audio from MinIO
    2. Use transcript from chatbot/OpenAI STT (required)
    3. Extract lightweight transcript/session features (Kiwi + heuristics)
    4. Build bundle-model input (numeric + text SVD + audio PCA zeros)
    5. RandomForest prediction + SHAP
    6. Store results in database

    Args:
        recording_id: UUID of the recording
        patient_id: Optional UUID of the patient. If omitted, loaded from DB.
        file_path: Optional object path. If omitted, loaded from DB recordings.file_path.
        transcript: Transcript from upstream chatbot STT (required).
    """
    temp_path = None
    try:
        logger.info(f"Starting voice processing for recording {recording_id}")

        # Resolve source metadata from DB when possible.
        conn = _get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT patient_id, file_path, transcription, uploaded_at
                FROM recordings
                WHERE recording_id = %s
                """,
                (recording_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"Recording not found: {recording_id}")

            db_patient_id, db_file_path, db_transcription, db_uploaded_at = row
            if patient_id is None:
                patient_id = str(db_patient_id) if db_patient_id else None
            if file_path is None:
                file_path = db_file_path
            if (transcript is None or not str(transcript).strip()) and db_transcription:
                transcript = db_transcription

            transcript = (transcript or "").strip()
            if not transcript:
                try:
                    _, audio_key_for_transcript = _resolve_bucket_and_key(file_path or db_file_path or "")
                except Exception:
                    audio_key_for_transcript = ""
                transcript = (_load_transcript_from_minio(recording_id, patient_id, audio_key_for_transcript) or "").strip()
            if not transcript:
                raise RuntimeError("Transcript is required for post-STT voice pipeline")

            # Move status to processing as soon as we start.
            cur.execute(
                """
                UPDATE recordings
                SET status = 'processing',
                    uploaded_at = COALESCE(uploaded_at, %s)
                WHERE recording_id = %s
                """,
                (_kst_now_naive(), recording_id),
            )
            conn.commit()
        finally:
            conn.close()

        if not file_path:
            raise RuntimeError("No file_path available for recording")

        source_bucket, source_key = _resolve_bucket_and_key(file_path)

        # Step 1: Download audio from MinIO
        minio_client = _get_minio_client()
        temp_fd, temp_path = tempfile.mkstemp(suffix=".wav")
        os.close(temp_fd)

        logger.info(f"Downloading from MinIO: {source_bucket}/{source_key}")
        minio_client.fget_object(source_bucket, source_key, temp_path)

        # Step 2-6: Feature extraction pipeline
        from .feature_extractor import extract_features_after_stt
        transcript, feature_row, linguistic_detail = extract_features_after_stt(
            temp_path,
            transcript=transcript,
        )

        logger.info(
            "Lightweight features extracted: %s keys, transcript: %s chars",
            len(feature_row),
            len(transcript),
        )

        # Step 4-5: Model inference
        from .model_inference import predict
        result = predict(feature_row, audio_path=temp_path)

        # Step 6: Store results in database
        conn = _get_db_connection()
        try:
            cur = conn.cursor()
            assessment_id = str(uuid4())
            now = _kst_now_naive()
            confidence_score = max(result["mci_probability"], 1.0 - result["mci_probability"])

            # Build features JSONB
            features_json = json.dumps(
                {
                    "pipeline": "post_stt_lightweight_bundle",
                    "numeric_feature_count": int(result.get("numeric_feature_count", 0)),
                    "text_feature_count": int(result.get("text_feature_count", 0)),
                    "audio_feature_count": int(result.get("audio_feature_count", 0)),
                    "model_feature_count": int(result.get("model_feature_count", 0)),
                    "raw_feature_key_count": int(len(feature_row)),
                    "linguistic_detail": linguistic_detail,
                }
            )
            acoustic_summary_json = json.dumps({
                "pipeline": "post_stt_lightweight_bundle",
                "feature_dims": {
                    "numeric": int(result.get("numeric_feature_count", 0)),
                    "text_svd": int(result.get("text_feature_count", 0)),
                    "audio_pca": int(result.get("audio_feature_count", 0)),
                    "model_input": int(result.get("model_feature_count", 0)),
                },
                "threshold": float(result.get("threshold", 0.5)),
            })
            shap_payload = result.get("shap") if isinstance(result, dict) else None
            shap_payload = shap_payload if isinstance(shap_payload, dict) else {}
            shap_available = bool(shap_payload.get("available"))
            shap_top_features_json = json.dumps(shap_payload.get("top_features")) if shap_available else None
            shap_feature_contributions_json = (
                json.dumps(shap_payload.get("feature_contributions")) if shap_available else None
            )
            shap_meta_json = json.dumps(
                {
                    "enabled": bool(shap_payload.get("enabled", False)),
                    "available": shap_available,
                    "reason": shap_payload.get("reason"),
                    "method": shap_payload.get("method"),
                    "model_stage": shap_payload.get("model_stage"),
                    "class_label": shap_payload.get("class_label"),
                    "class_index": shap_payload.get("class_index"),
                    "base_value": shap_payload.get("base_value"),
                    "output_value": shap_payload.get("output_value"),
                    "predicted_mci_probability": shap_payload.get("predicted_mci_probability"),
                    "feature_count": shap_payload.get("feature_count"),
                    "top_k": shap_payload.get("top_k"),
                }
            )

            # Insert voice assessment
            cur.execute("""
                INSERT INTO voice_assessments
                    (assessment_id, recording_id, transcript, cognitive_score,
                     mci_probability, flag, flag_reasons, features,
                     acoustic_summary, predicted_stage, confidence_score,
                     model_version, assessed_at, created_at,
                     shap_available, shap_top_features, shap_feature_contributions, shap_meta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                assessment_id,
                recording_id,
                transcript,
                result["cognitive_score"],
                result["mci_probability"],
                result["flag"],
                json.dumps(result["flag_reasons"]),
                features_json,
                acoustic_summary_json,
                "pMCI" if result["label"] == "MCI" else "CN",
                round(confidence_score, 4),
                result["model_version"],
                now,
                now,
                shap_available,
                shap_top_features_json,
                shap_feature_contributions_json,
                shap_meta_json,
            ))

            # Update recording status
            cur.execute("""
                UPDATE recordings
                SET status = 'completed',
                    transcription = %s,
                    uploaded_at = COALESCE(uploaded_at, %s),
                    description = NULL
                WHERE recording_id = %s
            """, (transcript, now, recording_id))

            # Send notification to doctor if flag is not normal
            if result["flag"] != "normal" and patient_id is not None:
                # Get assigned doctor
                cur.execute("""
                    SELECT p.doctor_id
                    FROM patients p
                    WHERE p.user_id = %s AND p.doctor_id IS NOT NULL
                """, (patient_id,))
                doctor_row = cur.fetchone()

                if doctor_row:
                    notification_id = str(uuid4())
                    flag_label = "경고" if result["flag"] == "warning" else "위험"
                    cur.execute("""
                        INSERT INTO notifications
                            (notification_id, user_id, type, title, message,
                             related_patient_id, related_type, related_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        notification_id,
                        str(doctor_row[0]),
                        "assessment_alert",
                        f"음성 평가 {flag_label}: MCI 확률 {result['mci_probability']:.1%}",
                        f"환자의 음성 분석 결과 {result['flag']} 단계로 판정되었습니다. "
                        f"인지 점수: {result['cognitive_score']}/100",
                        patient_id,
                        "voice_assessment",
                        assessment_id,
                        now,
                    ))

            conn.commit()
            logger.info(f"Assessment saved: {assessment_id} (flag={result['flag']})")

        finally:
            conn.close()

        logger.info(f"Voice processing completed for recording {recording_id}")

        return {
            'status': 'completed',
            'recording_id': recording_id,
            'assessment_id': assessment_id,
            'label': result['label'],
            'mci_probability': result['mci_probability'],
            'cognitive_score': result['cognitive_score'],
            'flag': result['flag'],
        }

    except Exception as e:
        logger.error(f"Voice processing failed: {str(e)}", exc_info=True)

        # Update recording status to failed
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE recordings
                SET status = 'failed',
                    description = %s,
                    uploaded_at = COALESCE(uploaded_at, %s)
                WHERE recording_id = %s
                """,
                (str(e)[:500], _kst_now_naive(), recording_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        self.retry(exc=e, countdown=60, max_retries=3)

    finally:
        # Clean up temp file
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@app.task(bind=True, name='process_mri_scan')
def process_mri_scan(self, mri_id: str, patient_id: str, file_path: str):
    """
    Process MRI scan for MCI assessment.

    Pipeline:
    1. Download DICOM/NIfTI from MinIO
    2. Preprocessing (skull stripping, normalization)
    3. Extract volumetric features (hippocampus, ventricles)
    4. 3D CNN + ResNet feature extraction
    5. Generate MCI stage prediction
    6. Store results in database
    """
    temp_work_dir = None
    try:
        logger.info(f"Starting MRI processing for scan {mri_id}")
        
        # Verify template exists
        if not os.path.exists(MRI_TEMPLATE_PATH):
            raise FileNotFoundError(f"MNI Template not found at {MRI_TEMPLATE_PATH}")

        # Resolve subject id and mark task status as processing.
        conn = _get_db_connection()
        subject_id = None
        try:
            cur = conn.cursor()
            cur.execute("SELECT subject_id FROM patients WHERE user_id = %s", (patient_id,))
            row = cur.fetchone()
            if row:
                subject_id = row[0]
            cur.execute(
                """
                UPDATE mri_assessments
                SET preprocessing_status = 'processing',
                    processed_at = TIMEZONE('Asia/Seoul', NOW())
                WHERE assessment_id = %s
                """,
                (mri_id,),
            )
            conn.commit()
        finally:
            conn.close()

        # Resolve persistent object identity and local cache path.
        preprocessed_dir = _get_preprocessed_dir()
        preprocessed_bucket = _get_preprocessed_bucket()
        source_file_reference = str(file_path).strip() if file_path else None
        subject_token = (
            (str(subject_id).strip() if subject_id else None)
            or _extract_subject_id_from_path(file_path)
            or str(patient_id)
        )
        output_name = f"{subject_token}_{mri_id}_preprocessed.nii.gz"
        output_path = os.path.join(preprocessed_dir, output_name)
        preprocessed_object_path = f"{preprocessed_bucket}/{output_name}"
        stored_file_path = preprocessed_object_path
        minio_client = _get_minio_client()

        final_path = None

        # 1) Try local cache first.
        if os.path.exists(output_path):
            final_path = output_path
            logger.info(f"Using cached preprocessed MRI: {final_path}")

        # 2) If not cached, try downloading existing preprocessed artifact from MinIO.
        if not final_path:
            if _download_object_if_exists(minio_client, preprocessed_bucket, output_name, output_path):
                final_path = output_path
                logger.info(f"Downloaded preprocessed MRI from MinIO: {preprocessed_object_path}")

        # 3) Otherwise run preprocessing from raw source.
        if not final_path:
            # Lazy import: only needed when preprocessing from scratch
            from .mri_utils import preprocess_single_subject_antspy, convert_dicom_to_nifti

            # Build local working directory for raw MRI material.
            temp_work_dir = tempfile.mkdtemp(prefix=f"mri_{mri_id}_")
            local_input_path = file_path
            if os.path.exists(file_path):
                logger.info(f"Using local MRI source: {file_path}")
            else:
                local_input_path = _download_mri_input_from_minio(file_path, temp_work_dir, mri_id)

            # If the incoming file is already preprocessed NIfTI, do not run ANTs again.
            basename_lower = os.path.basename(str(local_input_path)).lower()
            if (
                os.path.isfile(local_input_path)
                and basename_lower.endswith((".nii", ".nii.gz"))
                and "_preprocessed" in basename_lower
            ):
                logger.info(
                    "Input appears already preprocessed (%s). Skipping ANTs preprocessing.",
                    local_input_path,
                )
                final_path = local_input_path
            else:
                # Raw ingestion: if DICOM source, convert best series to NIfTI.
                current_path = local_input_path
                if os.path.isdir(local_input_path):
                    series_dir = _find_dicom_series_dir(local_input_path)
                    if not series_dir:
                        raise RuntimeError(f"No DICOM series found under {local_input_path}")
                    nifti_path = os.path.join(temp_work_dir, f"{mri_id}.nii.gz")
                    logger.info(f"Converting DICOM series {series_dir} to NIfTI {nifti_path}")
                    convert_dicom_to_nifti(series_dir, nifti_path)
                    current_path = nifti_path

                # ANTs preprocessing.
                ants_registration_type = os.getenv("MRI_ANTS_REGISTRATION_TYPE", "SyNRA")
                ants_clip_min = float(os.getenv("MRI_ANTS_CLIP_MIN", "-5.0"))
                ants_clip_max = float(os.getenv("MRI_ANTS_CLIP_MAX", "5.0"))
                logger.info(
                    f"Running ANTs preprocessing: {current_path} -> {output_path} "
                    f"(registration={ants_registration_type})"
                )
                final_path = preprocess_single_subject_antspy(
                    current_path,
                    output_path,
                    MRI_TEMPLATE_PATH,
                    registration_type=ants_registration_type,
                    clip_range=(ants_clip_min, ants_clip_max),
                )

        # Ensure preprocessed artifact is persisted in MinIO bucket.
        if not _object_exists(minio_client, preprocessed_bucket, output_name):
            try:
                stored_file_path = _upload_file_to_minio(
                    minio_client,
                    preprocessed_bucket,
                    output_name,
                    final_path,
                )
                logger.info(f"Uploaded preprocessed MRI to MinIO: {stored_file_path}")
            except Exception as upload_exc:
                # Keep pipeline running even if object upload fails; store local path for traceability.
                stored_file_path = final_path
                logger.warning(
                    f"Failed to upload preprocessed MRI to MinIO ({preprocessed_object_path}): {upload_exc}. "
                    f"Using local path in DB: {final_path}"
                )

        # 3. Model inference + CAM generation
        logger.info(f"Running 3D CNN Inference on {final_path}")
        
        from .model_inference import predict_mri
        mri_result = predict_mri(
            final_path,
            patient_id=patient_id,
            subject_id=subject_token,
            mri_id=mri_id,
            xai_output_dir=preprocessed_dir,
        )
        
        predicted_stage = mri_result['label']
        confidence = mri_result['confidence']
        probabilities = mri_result['probabilities']
        model_version = mri_result.get("model_version")
        xai_payload = mri_result.get("xai") if isinstance(mri_result, dict) else None
        xai_payload = xai_payload if isinstance(xai_payload, dict) else {}
        region_contributions = xai_payload.get("regionContributions")
        if not isinstance(region_contributions, list):
            region_contributions = []

        ai_analysis = {
            "pipeline": "dig_help_cascade_cam",
            "generatedAt": _kst_now_naive().isoformat(),
            "modelVersion": model_version,
            "sourceFilePath": source_file_reference,
            "preprocessedObjectPath": stored_file_path,
            "regionContributions": region_contributions,
            "xai": {
                "method": xai_payload.get("method"),
                "stage": xai_payload.get("stage"),
                "targetLabel": xai_payload.get("targetLabel"),
                "targetClassIndex": xai_payload.get("targetClassIndex"),
                "sliceIndex": xai_payload.get("sliceIndex"),
                "sliceIndices": xai_payload.get("sliceIndices"),
                "roiScores": xai_payload.get("roiScores"),
            },
        }

        attention_map_entries = []
        attention_slide_entries = []
        slide_payload = xai_payload.get("slides")
        if isinstance(slide_payload, list):
            for slide_idx, slide in enumerate(slide_payload, start=1):
                if not isinstance(slide, dict):
                    continue

                local_paths = slide.get("local_paths")
                if not isinstance(local_paths, dict):
                    continue

                roi_slug = re.sub(
                    r"[^a-z0-9]+",
                    "_",
                    str(slide.get("roi") or f"roi_{slide_idx}").lower(),
                ).strip("_")
                if not roi_slug:
                    roi_slug = f"roi_{slide_idx}"

                view_entries = []
                for plane in ("axial", "coronal", "sagittal"):
                    local_path = local_paths.get(plane)
                    if not isinstance(local_path, str) or not os.path.exists(local_path):
                        continue
                    try:
                        attention_bucket = "mri-xai"
                        attention_key = (
                            f"{subject_token}/{mri_id}/"
                            f"slide_{slide_idx:02d}_{roi_slug}_{plane}.png"
                        )
                        uploaded_attention = _upload_file_to_minio(
                            minio_client,
                            attention_bucket,
                            attention_key,
                            local_path,
                        )
                        view_entries.append(
                            {
                                "plane": plane,
                                "bucket": attention_bucket,
                                "key": attention_key,
                                "object": uploaded_attention,
                            }
                        )
                        logger.info(
                            "Uploaded MRI CAM slide image (slide=%s, plane=%s): %s",
                            slide_idx,
                            plane,
                            uploaded_attention,
                        )
                    except Exception as cam_upload_exc:
                        logger.warning(
                            "Failed to upload MRI CAM slide image (slide=%s, plane=%s): %s",
                            slide_idx,
                            plane,
                            cam_upload_exc,
                        )

                if view_entries:
                    attention_slide_entries.append(
                        {
                            "rank": int(slide.get("rank") or slide_idx),
                            "roi": slide.get("roi"),
                            "description": slide.get("description"),
                            "score": slide.get("score"),
                            "percentage": slide.get("percentage"),
                            "severity": slide.get("severity"),
                            "sliceIndices": slide.get("sliceIndices"),
                            "views": view_entries,
                        }
                    )

        if attention_slide_entries:
            ai_analysis["attentionSlides"] = attention_slide_entries
            first_slide_views = attention_slide_entries[0].get("views")
            if isinstance(first_slide_views, list):
                attention_map_entries = [entry for entry in first_slide_views if isinstance(entry, dict)]

        if not attention_map_entries:
            local_paths = xai_payload.get("local_paths")
            if not isinstance(local_paths, dict):
                local_paths = {"axial": xai_payload.get("local_path")}

            for plane in ("axial", "coronal", "sagittal"):
                local_path = local_paths.get(plane)
                if not isinstance(local_path, str) or not os.path.exists(local_path):
                    continue
                try:
                    attention_bucket = "mri-xai"
                    attention_key = f"{subject_token}/{mri_id}_cam_{plane}.png"
                    uploaded_attention = _upload_file_to_minio(
                        minio_client,
                        attention_bucket,
                        attention_key,
                        local_path,
                    )
                    attention_map_entries.append(
                        {
                            "plane": plane,
                            "bucket": attention_bucket,
                            "key": attention_key,
                            "object": uploaded_attention,
                        }
                    )
                    logger.info("Uploaded MRI CAM image (%s) to MinIO: %s", plane, uploaded_attention)
                except Exception as cam_upload_exc:
                    logger.warning("Failed to upload MRI CAM image (%s): %s", plane, cam_upload_exc)

        if attention_map_entries:
            ai_analysis["attentionMaps"] = attention_map_entries
            axial_entry = next((entry for entry in attention_map_entries if entry.get("plane") == "axial"), None)
            if not axial_entry:
                axial_entry = attention_map_entries[0]
            ai_analysis["attentionMapObject"] = axial_entry.get("object")
            ai_analysis["attentionMapBucket"] = axial_entry.get("bucket")
            ai_analysis["attentionMapKey"] = axial_entry.get("key")

        logger.info(f"Inference Result: {predicted_stage} (Confidence: {confidence:.4f})")

        # 4. Save Results to DB
        conn = _get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE mri_assessments
                SET classification = %s,
                    predicted_stage = %s,
                    confidence = %s,
                    probabilities = %s,
                    model_version = %s,
                    region_contributions = %s,
                    ai_analysis = %s,
                    file_path = %s,
                    preprocessing_status = 'completed',
                    processed_at = TIMEZONE('Asia/Seoul', NOW())
                WHERE assessment_id = %s
            """, (
                predicted_stage,
                predicted_stage,
                confidence,
                json.dumps(probabilities),
                model_version,
                json.dumps(region_contributions),
                json.dumps(ai_analysis),
                source_file_reference or stored_file_path,
                mri_id,
            ))
            conn.commit()
        finally:
            conn.close()

        return {
            'status': 'completed',
            'mri_id': mri_id,
            'result': predicted_stage,
            'confidence': confidence,
            'attention_map': ai_analysis.get("attentionMapObject"),
            'attention_maps': ai_analysis.get("attentionMaps"),
        }

    except Exception as e:
        logger.error(f"MRI processing failed: {str(e)}", exc_info=True)
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE mri_assessments
                SET preprocessing_status = 'failed',
                    processed_at = TIMEZONE('Asia/Seoul', NOW())
                WHERE assessment_id = %s
                """,
                (mri_id,),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        if temp_work_dir and os.path.isdir(temp_work_dir):
            try:
                import shutil
                shutil.rmtree(temp_work_dir, ignore_errors=True)
            except Exception:
                pass


@app.task(name='test_celery')
def test_celery():
    """Test task to verify Celery is working."""
    logger.info("Celery test task executed successfully!")
    return {'status': 'ok', 'message': 'Celery worker is running'}


if __name__ == '__main__':
    app.start()
