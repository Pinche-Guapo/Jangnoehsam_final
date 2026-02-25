import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from celery import Celery
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import db
from ..config import settings
from ..llm import llm_service
from ..schemas.llm_session import ChatRequest, EndSessionRequest, SessionMeta, StartRequest
from ..storage import storage

router = APIRouter(tags=["llm-session"])
KST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)

SESSION_OUTPUT_BUCKET = settings.llm_session_output_bucket
VOICE_RECORDING_BUCKET = "voice-recordings"
TRANSCRIPT_BUCKET = (os.getenv("MINIO_TRANSCRIPT_BUCKET", "voice-transcript") or "").strip() or "voice-transcript"

CONVERSATION_MODE_ALIASES = {
    "daily": "daily",
    "일상": "daily",
    "therapy": "therapy",
    "치료": "therapy",
    "상담": "therapy",
    "mixed": "mixed",
    "both": "mixed",
    "hybrid": "mixed",
    "일상+치료": "mixed",
}

MRI_BOOL_TO_NEURO_REGION = {
    "hippocampal_atrophy": "hippocampus_atrophy",
    "medial_temporal_atrophy": "temporal_atrophy",
    "white_matter_lesions": "white_matter_lesions",
    "frontal_atrophy": "frontal_atrophy",
    "parietal_atrophy": "parietal_atrophy",
}

NEURO_REGION_TRAINING_HINTS = {
    "hippocampus_atrophy": ["최근 기억 회상", "순서 기억"],
    "temporal_atrophy": ["의미 기반 단어 찾기", "범주 유창성"],
    "white_matter_lesions": ["주의 전환", "처리 속도 훈련"],
    "frontal_atrophy": ["문장 생성", "행동 설명"],
    "parietal_atrophy": ["공간/방향 설명", "간단 수리 과제"],
}

_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()

# Celery client for dispatching existing voice ML pipeline tasks.
celery_app = Celery(
    broker=os.getenv("REDIS_URL", "redis://redis:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://redis:6379/0"),
)


def _kst_now_naive() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _meta_to_dict(meta: Optional[SessionMeta]) -> Dict[str, Any]:
    if not meta:
        return {}
    return meta.model_dump(exclude_none=True)


def _normalize_conversation_mode(value: Any) -> str:
    mode = _normalize_text(value).lower()
    return CONVERSATION_MODE_ALIASES.get(mode, "mixed")


def _normalize_phase(raw_phase: Any, elapsed_sec: int, request_close: bool) -> str:
    if request_close:
        return "closing"
    phase = _normalize_text(raw_phase).lower()
    if phase in {"opening", "warmup", "dialog", "closing"}:
        return phase
    if elapsed_sec >= 240:
        return "closing"
    if elapsed_sec < 45:
        return "warmup"
    return "dialog"


def _parse_session_uuid(value: Any) -> Optional[UUID]:
    raw = _normalize_text(value)
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        # Preserve session continuity for legacy/non-UUID session ids.
        return uuid5(NAMESPACE_URL, raw)


def _safe_object_token(value: Any, default: str = "anonymous") -> str:
    raw = _normalize_text(value) or default
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    return (cleaned[:120] or default).strip("_") or default


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    lowered = _normalize_text(value).lower()
    return lowered in {"true", "t", "1", "yes", "y", "on"}


def _dedupe_keep_order(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


async def _fetch_patient_model_context(patient_id: int) -> Dict[str, Any]:
    try:
        patient_row = await db.fetchrow(
            """
            SELECT
                COALESCE(risk_level, '') AS risk_level,
                COALESCE(mci_subtype, '') AS mci_subtype
            FROM patients
            WHERE user_id = $1
            """,
            patient_id,
        )
    except Exception:
        logger.exception("Failed to read patient risk_level for patient_id=%s", patient_id)
        return {}
    risk_level = _normalize_text(patient_row["risk_level"]) if patient_row else ""
    mci_subtype = _normalize_text(patient_row["mci_subtype"]) if patient_row else ""

    try:
        mri_row = await db.fetchrow(
            """
            SELECT
                assessment_id,
                classification,
                predicted_stage,
                confidence,
                COALESCE(scan_date, created_at) AS assessed_at,
                hippocampal_atrophy,
                medial_temporal_atrophy,
                white_matter_lesions,
                frontal_atrophy,
                parietal_atrophy
            FROM mri_assessments
            WHERE patient_id = $1
            ORDER BY scan_date DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            patient_id,
        )
    except Exception:
        logger.exception("Failed to read mri_assessments for patient_id=%s", patient_id)
        mri_row = None

    payload: Dict[str, Any] = {}
    if risk_level:
        payload["risk_level"] = risk_level
    if mci_subtype:
        payload["mci_subtype"] = mci_subtype

    if not mri_row:
        return payload

    row = dict(mri_row)
    neuro_pattern: List[str] = []
    region_scores: Dict[str, float] = {}
    for db_col, region_key in MRI_BOOL_TO_NEURO_REGION.items():
        if _coerce_bool(row.get(db_col)):
            neuro_pattern.append(region_key)
            region_scores[region_key] = 1.0

    recommended_training: List[str] = []
    for region_key in neuro_pattern:
        recommended_training.extend(NEURO_REGION_TRAINING_HINTS.get(region_key, []))

    stage = _normalize_text(row.get("classification") or row.get("predicted_stage") or mci_subtype)
    if stage:
        payload["stage"] = stage
    if neuro_pattern:
        payload["neuro_pattern"] = neuro_pattern
        payload["main_region"] = neuro_pattern[0]
        payload["region_scores"] = region_scores
    if recommended_training:
        payload["recommended_training"] = _dedupe_keep_order(recommended_training)

    confidence = row.get("confidence")
    if confidence is not None:
        try:
            payload["confidence"] = float(confidence)
        except (TypeError, ValueError):
            pass

    assessment_id = row.get("assessment_id")
    if assessment_id is not None:
        payload["mri_assessment_id"] = str(assessment_id)
    assessed_at = row.get("assessed_at")
    if isinstance(assessed_at, datetime):
        payload["mri_assessed_at"] = assessed_at.isoformat()
    return payload


def _build_effective_model_result(
    original_model_result: Optional[Dict[str, Any]],
    *,
    patient_id: int,
    patient_context: Dict[str, Any],
) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(original_model_result or {})
    if not patient_context:
        return merged

    for key in (
        "stage",
        "risk_level",
        "mci_subtype",
        "neuro_pattern",
        "main_region",
        "region_scores",
        "recommended_training",
        "confidence",
    ):
        value = patient_context.get(key)
        if value in (None, "", [], {}):
            continue
        merged[key] = value

    merged["context_source"] = "patient_latest_mri_assessment"
    merged["context_patient_id"] = patient_id
    if patient_context.get("mri_assessment_id"):
        merged["mri_assessment_id"] = patient_context["mri_assessment_id"]
    if patient_context.get("mri_assessed_at"):
        merged["mri_assessed_at"] = patient_context["mri_assessed_at"]
    return merged


async def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_chat_sessions (
                session_id UUID PRIMARY KEY,
                patient_id INTEGER NULL REFERENCES patients(user_id) ON DELETE SET NULL,
                profile_id VARCHAR(120) NULL,
                session_mode VARCHAR(20) NULL,
                conversation_mode VARCHAR(20) NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                state JSONB NULL,
                dialog_summary TEXT NULL,
                metadata JSONB NULL,
                started_at TIMESTAMP NULL,
                ended_at TIMESTAMP NULL,
                end_reason VARCHAR(60) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_chat_turns (
                turn_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id UUID NOT NULL REFERENCES llm_chat_sessions(session_id) ON DELETE CASCADE,
                turn_index INTEGER NOT NULL DEFAULT 0,
                user_message TEXT NULL,
                assistant_message TEXT NULL,
                state JSONB NULL,
                metadata JSONB NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_session_outputs (
                output_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id UUID NOT NULL REFERENCES llm_chat_sessions(session_id) ON DELETE CASCADE,
                patient_id INTEGER NULL REFERENCES patients(user_id) ON DELETE SET NULL,
                output_type VARCHAR(40) NOT NULL,
                bucket VARCHAR(63) NOT NULL,
                object_key TEXT NOT NULL,
                content_type VARCHAR(255) NULL,
                size_bytes BIGINT NULL,
                metadata JSONB NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_llm_chat_turns_session_turn
            ON llm_chat_turns (session_id, turn_index)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_llm_session_outputs_session_created
            ON llm_session_outputs (session_id, created_at DESC)
            """
        )

        _SCHEMA_READY = True


async def _resolve_patient_id(requested_patient_id: Optional[int]) -> int:
    if requested_patient_id is not None:
        row = await db.fetchrow(
            "SELECT user_id FROM patients WHERE user_id = $1",
            int(requested_patient_id),
        )
        if not row:
            raise HTTPException(status_code=400, detail=f"patient_id not found: {requested_patient_id}")
        return int(row["user_id"])

    row = await db.fetchrow("SELECT user_id FROM patients ORDER BY user_id ASC LIMIT 1")
    if not row:
        raise HTTPException(
            status_code=400,
            detail="No patients available. Provide meta.patient_id after creating a patient.",
        )
    return int(row["user_id"])


async def _fetch_patient_stage(patient_id: int) -> str:
    row = await db.fetchrow(
        "SELECT COALESCE(risk_level, 'unknown') AS risk_level FROM patients WHERE user_id = $1",
        patient_id,
    )
    if not row:
        return "unknown"
    return _normalize_text(row["risk_level"] or "unknown") or "unknown"


async def _upsert_training_session(session_id: UUID, patient_id: int, started_at: datetime) -> None:
    await db.execute(
        """
        INSERT INTO training_sessions (training_id, patient_id, started_at, exercise_type, created_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (training_id)
        DO UPDATE SET
            patient_id = EXCLUDED.patient_id,
            started_at = COALESCE(training_sessions.started_at, EXCLUDED.started_at),
            exercise_type = COALESCE(training_sessions.exercise_type, EXCLUDED.exercise_type)
        """,
        session_id,
        patient_id,
        started_at,
        "chat",
        started_at,
    )


async def _upsert_session(
    *,
    session_id: UUID,
    patient_id: Optional[int],
    profile_id: Optional[str],
    session_mode: Optional[str],
    conversation_mode: str,
    status: str,
    state: Dict[str, Any],
    dialog_summary: Optional[str],
    metadata: Dict[str, Any],
    started_at: Optional[datetime],
) -> None:
    now = _kst_now_naive()
    await db.execute(
        """
        INSERT INTO llm_chat_sessions (
            session_id,
            patient_id,
            profile_id,
            session_mode,
            conversation_mode,
            status,
            state,
            dialog_summary,
            metadata,
            started_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9::jsonb, $10, $11)
        ON CONFLICT (session_id)
        DO UPDATE SET
            patient_id = COALESCE(EXCLUDED.patient_id, llm_chat_sessions.patient_id),
            profile_id = COALESCE(EXCLUDED.profile_id, llm_chat_sessions.profile_id),
            session_mode = COALESCE(EXCLUDED.session_mode, llm_chat_sessions.session_mode),
            conversation_mode = COALESCE(EXCLUDED.conversation_mode, llm_chat_sessions.conversation_mode),
            status = COALESCE(EXCLUDED.status, llm_chat_sessions.status),
            state = COALESCE(EXCLUDED.state, llm_chat_sessions.state),
            dialog_summary = COALESCE(EXCLUDED.dialog_summary, llm_chat_sessions.dialog_summary),
            metadata = COALESCE(llm_chat_sessions.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb),
            started_at = COALESCE(llm_chat_sessions.started_at, EXCLUDED.started_at),
            updated_at = EXCLUDED.updated_at
        """,
        session_id,
        patient_id,
        profile_id,
        session_mode,
        conversation_mode,
        status,
        json.dumps(state or {}),
        dialog_summary,
        json.dumps(metadata or {}),
        started_at,
        now,
    )


async def _load_history(session_id: UUID, max_turns: int = 24) -> List[Dict[str, str]]:
    rows = await db.fetch(
        """
        SELECT user_message, assistant_message
        FROM llm_chat_turns
        WHERE session_id = $1
        ORDER BY turn_index ASC, created_at ASC
        LIMIT $2
        """,
        session_id,
        max_turns,
    )
    messages: List[Dict[str, str]] = []
    for row in rows:
        user_message = _normalize_text(row["user_message"])
        assistant_message = _normalize_text(row["assistant_message"])
        if user_message:
            messages.append({"role": "user", "content": user_message})
        if assistant_message:
            messages.append({"role": "assistant", "content": assistant_message})
    return messages[-20:]


async def _next_turn_index(session_id: UUID) -> int:
    row = await db.fetchrow(
        """
        SELECT COALESCE(MAX(turn_index), -1) AS max_turn
        FROM llm_chat_turns
        WHERE session_id = $1
        """,
        session_id,
    )
    return int((row["max_turn"] if row else -1) or -1) + 1


async def _insert_turn(
    *,
    session_id: UUID,
    turn_index: int,
    user_message: Optional[str],
    assistant_message: str,
    state: Dict[str, Any],
    metadata: Dict[str, Any],
) -> None:
    await db.execute(
        """
        INSERT INTO llm_chat_turns (
            session_id,
            turn_index,
            user_message,
            assistant_message,
            state,
            metadata,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
        """,
        session_id,
        int(turn_index),
        (_normalize_text(user_message) or None),
        _normalize_text(assistant_message),
        json.dumps(state or {}),
        json.dumps(metadata or {}),
        _kst_now_naive(),
    )


async def _build_dialog_summary(session_id: UUID) -> str:
    rows = await db.fetch(
        """
        SELECT user_message
        FROM llm_chat_turns
        WHERE session_id = $1
          AND COALESCE(TRIM(user_message), '') <> ''
        ORDER BY turn_index DESC
        LIMIT 4
        """,
        session_id,
    )
    snippets = []
    for row in reversed(rows):
        text = _normalize_text(row["user_message"])
        if text:
            snippets.append(text[:120])
    return " / ".join(snippets)


async def _build_conversation_text(session_id: UUID) -> str:
    rows = await db.fetch(
        """
        SELECT turn_index, user_message, assistant_message
        FROM llm_chat_turns
        WHERE session_id = $1
        ORDER BY turn_index ASC, created_at ASC
        """,
        session_id,
    )
    lines: List[str] = []
    for row in rows:
        idx = int(row["turn_index"])
        user_message = _normalize_text(row["user_message"])
        assistant_message = _normalize_text(row["assistant_message"])
        if user_message:
            lines.append(f"[{idx}] user: {user_message}")
        if assistant_message:
            lines.append(f"[{idx}] assistant: {assistant_message}")
    return "\n".join(lines).strip()


async def _collect_session_transcript(session_id: UUID) -> str:
    row = await db.fetchrow(
        """
        SELECT STRING_AGG(TRIM(user_message), ' ' ORDER BY turn_index) AS transcript
        FROM llm_chat_turns
        WHERE session_id = $1
          AND COALESCE(TRIM(user_message), '') <> ''
        """,
        session_id,
    )
    transcript = _normalize_text((row["transcript"] if row else "") or "")
    return transcript


def _ensure_bucket(bucket: str) -> None:
    if not storage.client.bucket_exists(bucket):
        storage.client.make_bucket(bucket)


def _put_object(bucket: str, object_key: str, payload: bytes, content_type: str) -> str:
    _ensure_bucket(bucket)
    storage.client.put_object(
        bucket,
        object_key,
        BytesIO(payload),
        length=len(payload),
        content_type=content_type,
    )
    return f"{bucket}/{object_key}"


async def _track_output(
    *,
    session_id: UUID,
    patient_id: Optional[int],
    output_type: str,
    bucket: str,
    object_key: str,
    content_type: Optional[str],
    size_bytes: Optional[int],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    now = _kst_now_naive()
    payload = metadata or {}
    await db.execute(
        """
        INSERT INTO llm_session_outputs (
            output_id,
            session_id,
            patient_id,
            output_type,
            bucket,
            object_key,
            content_type,
            size_bytes,
            metadata,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
        """,
        uuid4(),
        session_id,
        patient_id,
        output_type,
        bucket,
        object_key,
        content_type,
        size_bytes,
        json.dumps(payload),
        now,
    )
    await db.execute(
        """
        INSERT INTO storage_objects (
            object_id,
            bucket,
            object_key,
            size_bytes,
            content_type,
            source_type,
            source_id,
            metadata,
            uploaded_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
        """,
        uuid4(),
        bucket,
        object_key,
        size_bytes,
        content_type,
        output_type,
        session_id,
        json.dumps(payload),
        now,
    )


def _resolve_audio_extension(file_name: str, content_type: Optional[str]) -> str:
    if file_name and "." in file_name:
        ext = file_name.rsplit(".", 1)[-1].strip().lower()
        if ext:
            return ext

    normalized = _normalize_text(content_type).lower()
    if "wav" in normalized:
        return "wav"
    if "mpeg" in normalized or "mp3" in normalized:
        return "mp3"
    if "ogg" in normalized:
        return "ogg"
    if "webm" in normalized:
        return "webm"
    if "mp4" in normalized or "m4a" in normalized:
        return "m4a"
    return "bin"


@router.post("/start")
@router.post("/api/start")
async def start_chat(req: StartRequest):
    await _ensure_schema()

    meta = req.meta or SessionMeta()
    meta_payload = _meta_to_dict(meta)
    session_id = _parse_session_uuid(meta.session_id) or uuid4()
    patient_id = await _resolve_patient_id(meta.patient_id)
    profile_id = _normalize_text(meta.profile_id) or None
    session_mode = _normalize_text(meta.session_mode) or "live"
    conversation_mode = _normalize_conversation_mode(meta.conversation_mode)
    elapsed_sec = int(meta.elapsed_sec or 0)
    now = _kst_now_naive()
    patient_context = await _fetch_patient_model_context(patient_id)
    effective_model_result = _build_effective_model_result(
        req.model_result or {},
        patient_id=patient_id,
        patient_context=patient_context,
    )

    state_payload: Dict[str, Any] = {
        "conversation_phase": "opening",
        "dialog_state": "session_open",
        "training_type": "none",
        "training_level": 1,
        "training_step": 0,
        "fatigue_level": "low",
        "turn_index": 0,
        "elapsed_sec": elapsed_sec,
        "last_user_utterance": "",
        "last_assistant_utterance": "",
    }

    patient_stage = _normalize_text(effective_model_result.get("stage") or "")
    if not patient_stage:
        patient_stage = await _fetch_patient_stage(patient_id)

    opening_message = await llm_service.chat(
        message="세션을 시작합니다.",
        conversation_history=[],
        prompt_type="cognitive_training",
        patient_stage=patient_stage,
        model_result=effective_model_result,
    )
    opening_message = _normalize_text(opening_message) or "안녕하세요. 오늘은 어떻게 지내셨나요?"
    state_payload["last_assistant_utterance"] = opening_message
    dialog_summary = "세션이 시작되었습니다."

    await _upsert_training_session(session_id, patient_id, now)
    await _upsert_session(
        session_id=session_id,
        patient_id=patient_id,
        profile_id=profile_id,
        session_mode=session_mode,
        conversation_mode=conversation_mode,
        status="active",
        state=state_payload,
        dialog_summary=dialog_summary,
        metadata={"model_result": effective_model_result, "meta": meta_payload},
        started_at=now,
    )
    await _insert_turn(
        session_id=session_id,
        turn_index=0,
        user_message=None,
        assistant_message=opening_message,
        state=state_payload,
        metadata={
            "event": "session_start",
            "conversation_mode": conversation_mode,
            "session_mode": session_mode,
        },
    )

    return {
        "response": opening_message,
        "state": state_payload,
        "dialog_summary": dialog_summary,
        "session_id": str(session_id),
        "meta": {
            "conversation_phase": "warmup",
            "turn_index": 0,
            "request_close": False,
            "conversation_mode": conversation_mode,
            "session_mode": session_mode,
            "stt_event": "speech",
            "patient_id": patient_id,
            "profile_id": profile_id,
            "main_region": effective_model_result.get("main_region"),
            "neuro_pattern": effective_model_result.get("neuro_pattern"),
        },
    }


@router.post("/chat")
@router.post("/api/chat")
async def chat(req: ChatRequest):
    await _ensure_schema()

    meta = req.meta or SessionMeta()
    meta_payload = _meta_to_dict(meta)
    now = _kst_now_naive()
    session_id = _parse_session_uuid(meta.session_id) or uuid4()
    requested_patient_id = meta.patient_id
    conversation_mode = _normalize_conversation_mode(meta.conversation_mode)
    session_mode = _normalize_text(meta.session_mode) or "live"
    user_message = _normalize_text(req.user_message)
    stt_event = _normalize_text(meta.stt_event).lower() or "speech"

    requested_close = bool(meta.request_close) or bool(meta.should_wrap_up)
    elapsed_sec = int(meta.elapsed_sec or 0)
    conversation_phase = _normalize_phase(meta.conversation_phase, elapsed_sec, requested_close)
    closing_reason = _normalize_text(meta.closing_reason) or None

    session_row = await db.fetchrow(
        """
        SELECT session_id, patient_id, profile_id, state
        FROM llm_chat_sessions
        WHERE session_id = $1
        """,
        session_id,
    )

    if session_row:
        patient_id = int(session_row["patient_id"] or await _resolve_patient_id(requested_patient_id))
        profile_id = _normalize_text(meta.profile_id) or _normalize_text(session_row["profile_id"]) or None
    else:
        patient_id = await _resolve_patient_id(requested_patient_id)
        profile_id = _normalize_text(meta.profile_id) or None
        await _upsert_training_session(session_id, patient_id, now)

    history = await _load_history(session_id)
    patient_context = await _fetch_patient_model_context(patient_id)
    effective_model_result = _build_effective_model_result(
        req.model_result or {},
        patient_id=patient_id,
        patient_context=patient_context,
    )
    patient_stage = _normalize_text(effective_model_result.get("stage") or "")
    if not patient_stage:
        patient_stage = await _fetch_patient_stage(patient_id)

    llm_input = user_message
    if not llm_input and stt_event == "no_speech":
        llm_input = "사용자가 잠시 말을 멈췄습니다. 짧고 따뜻하게 다시 말씀해 달라고 안내해 주세요."
    elif not llm_input and requested_close:
        llm_input = "대화를 마무리하는 짧은 인사를 전해주세요."
    elif not llm_input:
        llm_input = "사용자의 입력이 비어 있습니다. 편하게 다시 말씀해 달라고 안내해 주세요."

    assistant_message = await llm_service.chat(
        message=llm_input,
        conversation_history=history,
        prompt_type="cognitive_training",
        patient_stage=patient_stage,
        model_result=effective_model_result,
    )
    assistant_message = _normalize_text(assistant_message) or "말씀 감사합니다. 이어서 한 가지 더 들려주실래요?"

    if requested_close:
        assistant_message = assistant_message.replace("?", ".")
        if not assistant_message:
            assistant_message = "오늘 대화는 여기서 마무리할게요. 다음에 또 이야기해요."

    db_turn_index = await _next_turn_index(session_id)
    client_turn_index = int(meta.turn_index) if meta.turn_index is not None else db_turn_index
    turn_index = max(db_turn_index, client_turn_index)

    state_payload: Dict[str, Any] = dict(req.state or {})
    state_payload.update(
        {
            "conversation_phase": conversation_phase,
            "dialog_state": "session_wrap" if requested_close else "cognitive_training",
            "turn_index": turn_index,
            "elapsed_sec": elapsed_sec,
            "last_user_utterance": user_message,
            "last_assistant_utterance": assistant_message,
            "fatigue_level": "high" if requested_close else state_payload.get("fatigue_level", "low"),
        }
    )

    await _insert_turn(
        session_id=session_id,
        turn_index=turn_index,
        user_message=user_message or None,
        assistant_message=assistant_message,
        state=state_payload,
        metadata={
            "meta": meta_payload,
            "model_result": effective_model_result,
            "stt_event": stt_event,
            "request_close": requested_close,
            "closing_reason": closing_reason,
        },
    )
    dialog_summary = await _build_dialog_summary(session_id)

    await _upsert_session(
        session_id=session_id,
        patient_id=patient_id,
        profile_id=profile_id,
        session_mode=session_mode,
        conversation_mode=conversation_mode,
        status="active",
        state=state_payload,
        dialog_summary=dialog_summary,
        metadata={
            "last_turn_index": turn_index,
            "last_stt_event": stt_event,
            "meta": meta_payload,
            "model_result": effective_model_result,
        },
        started_at=now,
    )

    return {
        "response": assistant_message,
        "state": state_payload,
        "dialog_summary": dialog_summary,
        "session_id": str(session_id),
        "meta": {
            "conversation_phase": conversation_phase,
            "turn_index": turn_index,
            "request_close": requested_close,
            "conversation_mode": conversation_mode,
            "session_mode": session_mode,
            "stt_event": stt_event,
            "closing_reason": closing_reason,
            "patient_id": patient_id,
            "profile_id": profile_id,
            "main_region": effective_model_result.get("main_region"),
            "neuro_pattern": effective_model_result.get("neuro_pattern"),
        },
    }


@router.post("/session/end")
@router.post("/api/session/end")
async def end_session(req: EndSessionRequest):
    await _ensure_schema()

    session_id = _parse_session_uuid(req.session_id)
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id must be a valid UUID")

    session_row = await db.fetchrow(
        """
        SELECT session_id, patient_id, profile_id, started_at, dialog_summary
        FROM llm_chat_sessions
        WHERE session_id = $1
        """,
        session_id,
    )
    if not session_row:
        return {"status": "ok", "session_id": req.session_id}

    now = _kst_now_naive()
    patient_id = int(session_row["patient_id"]) if session_row["patient_id"] is not None else None
    profile_id = _normalize_text(session_row["profile_id"]) or None

    conversation_text = await _build_conversation_text(session_id)
    dialog_summary = _normalize_text(session_row["dialog_summary"]) or await _build_dialog_summary(session_id)
    namespace_token = _safe_object_token(patient_id if patient_id is not None else profile_id, default="anonymous")
    prefix = f"{namespace_token}/{session_id}"
    conversation_key = f"{prefix}/conversation.txt"
    manifest_key = f"{prefix}/manifest.json"

    conversation_bytes = (conversation_text + "\n").encode("utf-8") if conversation_text else b"\n"
    conversation_path = _put_object(
        SESSION_OUTPUT_BUCKET,
        conversation_key,
        conversation_bytes,
        "text/plain; charset=utf-8",
    )
    await _track_output(
        session_id=session_id,
        patient_id=patient_id,
        output_type="session_conversation",
        bucket=SESSION_OUTPUT_BUCKET,
        object_key=conversation_key,
        content_type="text/plain; charset=utf-8",
        size_bytes=len(conversation_bytes),
        metadata={"path": conversation_path},
    )

    manifest_payload = {
        "session_id": str(session_id),
        "patient_id": patient_id,
        "profile_id": profile_id,
        "end_reason": _normalize_text(req.end_reason),
        "elapsed_sec": req.elapsed_sec,
        "turn_count": req.turn_count,
        "session_mode": _normalize_text(req.session_mode),
        "dialog_summary": dialog_summary,
        "generated_at": now.isoformat(),
        "objects": {
            "conversation_path": conversation_path,
        },
    }
    manifest_bytes = json.dumps(manifest_payload, ensure_ascii=False, indent=2).encode("utf-8")
    manifest_path = _put_object(
        SESSION_OUTPUT_BUCKET,
        manifest_key,
        manifest_bytes,
        "application/json",
    )
    await _track_output(
        session_id=session_id,
        patient_id=patient_id,
        output_type="session_manifest",
        bucket=SESSION_OUTPUT_BUCKET,
        object_key=manifest_key,
        content_type="application/json",
        size_bytes=len(manifest_bytes),
        metadata={"path": manifest_path},
    )

    elapsed_seconds = int(req.elapsed_sec) if req.elapsed_sec is not None else None
    if elapsed_seconds is None and session_row["started_at"] is not None:
        delta = now - session_row["started_at"]
        elapsed_seconds = max(0, int(delta.total_seconds()))

    await db.execute(
        """
        UPDATE llm_chat_sessions
        SET status = 'ended',
            ended_at = $2,
            end_reason = $3,
            dialog_summary = COALESCE($4, dialog_summary),
            metadata = COALESCE(metadata, '{}'::jsonb) || $5::jsonb,
            updated_at = $2
        WHERE session_id = $1
        """,
        session_id,
        now,
        _normalize_text(req.end_reason),
        dialog_summary or None,
        json.dumps({"manifest_path": manifest_path}),
    )
    await db.execute(
        """
        UPDATE training_sessions
        SET ended_at = COALESCE(ended_at, $2),
            duration_seconds = COALESCE($3, duration_seconds),
            summary = COALESCE($4, summary)
        WHERE training_id = $1
        """,
        session_id,
        now,
        elapsed_seconds,
        dialog_summary or None,
    )

    return {"status": "ok", "session_id": str(session_id)}


@router.post("/session/upload-audio")
@router.post("/api/session/upload-audio")
async def upload_session_audio(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    profile_id: str = Form(default=""),
):
    await _ensure_schema()

    parsed_session_id = _parse_session_uuid(session_id)
    if not parsed_session_id:
        raise HTTPException(status_code=400, detail="session_id must be a valid UUID")

    now = _kst_now_naive()
    session_row = await db.fetchrow(
        """
        SELECT session_id, patient_id, profile_id
        FROM llm_chat_sessions
        WHERE session_id = $1
        """,
        parsed_session_id,
    )

    if session_row:
        patient_id = int(session_row["patient_id"] or await _resolve_patient_id(None))
        resolved_profile = _normalize_text(session_row["profile_id"]) or _normalize_text(profile_id) or None
    else:
        patient_id = await _resolve_patient_id(None)
        resolved_profile = _normalize_text(profile_id) or None
        await _upsert_training_session(parsed_session_id, patient_id, now)
        await _upsert_session(
            session_id=parsed_session_id,
            patient_id=patient_id,
            profile_id=resolved_profile,
            session_mode="live",
            conversation_mode="mixed",
            status="active",
            state={},
            dialog_summary=None,
            metadata={},
            started_at=now,
        )

    file_payload = await file.read()
    if not file_payload:
        raise HTTPException(status_code=400, detail="Uploaded audio is empty.")

    ext = _resolve_audio_extension(file.filename or "", file.content_type)
    namespace_token = _safe_object_token(patient_id if patient_id is not None else resolved_profile, default="anonymous")
    llm_audio_key = f"{namespace_token}/{parsed_session_id}/conversation.user.{ext}"
    llm_audio_path = _put_object(
        SESSION_OUTPUT_BUCKET,
        llm_audio_key,
        file_payload,
        file.content_type or "application/octet-stream",
    )
    await _track_output(
        session_id=parsed_session_id,
        patient_id=patient_id,
        output_type="session_audio_upload",
        bucket=SESSION_OUTPUT_BUCKET,
        object_key=llm_audio_key,
        content_type=file.content_type,
        size_bytes=len(file_payload),
        metadata={"path": llm_audio_path},
    )

    converted_to_wav = ext == "wav"
    conversion_error: Optional[str] = None
    recording_id: Optional[str] = None
    pipeline_enqueued = False

    if converted_to_wav:
        session_transcript = await _collect_session_transcript(parsed_session_id)
        if not session_transcript:
            conversion_error = "Session transcript is empty; skipped voice pipeline dispatch."
        else:
            recording_uuid = uuid4()
            recording_id = str(recording_uuid)
            voice_key = f"{patient_id}/{recording_uuid}.wav"
            voice_path = _put_object(
                VOICE_RECORDING_BUCKET,
                voice_key,
                file_payload,
                "audio/wav",
            )
            transcript_key = f"{patient_id}/{recording_uuid}.txt"
            transcript_payload = session_transcript.encode("utf-8")
            transcript_path = _put_object(
                TRANSCRIPT_BUCKET,
                transcript_key,
                transcript_payload,
                "text/plain; charset=utf-8",
            )

            await _track_output(
                session_id=parsed_session_id,
                patient_id=patient_id,
                output_type="voice_recording",
                bucket=VOICE_RECORDING_BUCKET,
                object_key=voice_key,
                content_type="audio/wav",
                size_bytes=len(file_payload),
                metadata={"path": voice_path, "recording_id": recording_id},
            )
            await _track_output(
                session_id=parsed_session_id,
                patient_id=patient_id,
                output_type="voice_transcript",
                bucket=TRANSCRIPT_BUCKET,
                object_key=transcript_key,
                content_type="text/plain; charset=utf-8",
                size_bytes=len(transcript_payload),
                metadata={"path": transcript_path, "recording_id": recording_id},
            )

            await _upsert_training_session(parsed_session_id, patient_id, now)
            await db.execute(
                """
                INSERT INTO recordings (
                    recording_id,
                    training_id,
                    patient_id,
                    file_path,
                    file_size_bytes,
                    format,
                    recorded_at,
                    uploaded_at,
                    status,
                    transcription,
                    exercise_type,
                    created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                recording_uuid,
                parsed_session_id,
                patient_id,
                voice_path,
                len(file_payload),
                "wav",
                now,
                now,
                "pending",
                session_transcript,
                "chat",
                now,
            )
            celery_app.send_task(
                "process_voice_recording",
                args=[recording_id, patient_id, voice_path],
                kwargs={"transcript": session_transcript},
            )
            pipeline_enqueued = True
    else:
        conversion_error = "Uploaded audio is not WAV; skipped voice pipeline dispatch."

    return {
        "ok": True,
        "session_id": str(parsed_session_id),
        "patient_id": str(patient_id),
        "audio_path": llm_audio_path,
        "audio_format": ext,
        "converted_to_wav": converted_to_wav,
        "conversion_error": conversion_error,
        "uploaded_at": now.isoformat(),
        "recording_id": recording_id,
        "pipeline_enqueued": pipeline_enqueued,
    }
