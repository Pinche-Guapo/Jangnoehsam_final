## MCI 인지 평가 플랫폼

Raspberry Pi 5 + Docker Compose 환경에서 동작하는 이중(음성/MRI) 인지 평가 플랫폼입니다.
이 문서는 현재 저장소 기준으로, 지금까지 구현된 범위와 운영 방법을 한국어로 정리합니다.

### ADNI 데이터 라이선스 준수

본 프로젝트는 ADNI 기반 데이터를 사용하지만, 라이선스 준수를 위해 다음 원칙을 지킵니다.

- 원본 ADNI 데이터 파일, 원본 DICOM/NIfTI, 피험자 식별 정보는 README에 기록하지 않습니다.
- 데이터 스크린샷/샘플 파일/외부 업로드 링크를 문서에 포함하지 않습니다.
- 실행 예시는 모두 일반화된 경로와 플레이스홀더만 사용합니다.
- Git 저장소에는 코드/설정/합성(synthetic) 예시만 포함하고, ADNI 원본/파생 산출물은 커밋하지 않습니다.
- 발표/문서/데모에서는 ADNI 개별 사례를 재식별할 수 있는 정보(식별자, 경로, 메타데이터)를 노출하지 않습니다.

### 기술 스택

| 레이어 | 기술 |
|-------|-----------|
| Frontend | Vue 3, Vite, Pinia, Vue Router |
| Backend API | FastAPI, Python 3.12 |
| Worker | Celery, ANTsPy/ANTsPyNet, PyTorch, CatBoost |
| Queue | Redis |
| Database | PostgreSQL 16 |
| Object Storage | MinIO |
| Infra | Docker Compose, Nginx |

### 최신 진행 요약 (2026-02-26 업데이트)

1. 백엔드 인프라를 `FastAPI + Celery + Redis + PostgreSQL + MinIO` 구조로 정리해, 업로드/추론/저장 흐름을 비동기 파이프라인으로 고정했습니다.
2. MRI 파이프라인은 `ingest -> mri_assessments 생성 -> Celery process_mri_scan -> ANTs 전처리 -> 분류 -> SQL 반영`까지 연결했고, 전처리 결과는 `mri-preprocessed` 버킷 캐시를 사용합니다.
3. 음성 파이프라인은 세션 API(`/start`, `/chat`, `/session/upload-audio`, `/session/end`)와 연동되며, 세션 산출물은 MinIO + PostgreSQL에 저장되고 조건 충족 시 ML 워커로 자동 enqueue됩니다.
4. LLM 세션은 `patient_id` 기준 최신 MRI 진단 컨텍스트를 백엔드에서 자동 병합하도록 연결해, 프론트 입력 누락 시에도 환자 맞춤 컨텍스트를 유지합니다.
5. 운영 안정화를 위해 워커 동시성/스레드 제한, `max-tasks-per-child`, DB/스크립트 KST 타임존 고정 설정을 반영했습니다.
6. 검증 체계는 소표본 환경에 맞춰 `LOOCV` 중심으로 운영하고, AI-Hub 짧은 세션과 학습 데이터(긴 세션) 간 도메인/길이 차이에 따른 분포 흔들림을 확인해 길이 정합 재학습을 후속 과제로 정의했습니다.

### 워커 튜닝(안전 설정, 2026-02-22 반영)

파이프라인 로직(피처 추출/모델 추론/DB 저장)은 변경하지 않고, **Celery 실행 파라미터만** 조정 가능하도록 구성했습니다.

- 적용 파일:
  - `docker/docker-compose.yml` (worker 실행/환경변수)
  - `src/claude/worker/tasks.py` (Celery 설정 env override)

- 기본 적용값(현재):
  - `CELERY_WORKER_CONCURRENCY=1`
  - `CELERY_WORKER_PREFETCH_MULTIPLIER=1`
  - `CELERY_WORKER_MAX_TASKS_PER_CHILD=5`
  - `CELERY_TASK_ACKS_LATE=false`
  - `CELERY_TASK_REJECT_ON_WORKER_LOST=false`
  - `CELERY_BROKER_POOL_LIMIT=10`
  - `OMP_NUM_THREADS=2`
  - `MKL_NUM_THREADS=2`
  - `OPENBLAS_NUM_THREADS=2`

- 목적:
  - API 응답성 유지(워커 과점유 완화)
  - 장시간 처리 시 메모리 누수/누적 리스크 완화(`max-tasks-per-child`)
  - RPI 8GB 환경에서 스왑 급증/쓰래싱 가능성 완화

- 즉시 반영 방법(워커만 재기동):

```bash
cd docker
docker compose --env-file .env up -d worker
docker compose --env-file .env logs -f worker
```

- 필요 시 조정 예시(일시적으로 처리량 우선):

```bash
export CELERY_WORKER_CONCURRENCY=2
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=2
cd docker
docker compose --env-file .env up -d worker
```

### 프로젝트 구조

```text
├── src/claude/
│   ├── app/                         # FastAPI 라우터/인증/스토리지
│   └── worker/                      # Celery 태스크, MRI/음성 추론 로직
├── scripts/
│   ├── ingest_mri_folder.py         # MRI 풀 파이프라인 진입점(비동기 트리거)
│   └── preprocess_mri_subject_antspy.py
├── models/                          # 학습된 추론 모델
├── migrations/                      # DB 스키마
├── Dockerfile.api
├── Dockerfile.worker
└── pyproject.toml
```

### 지금까지 완료된 핵심 작업 (2026-02-26 기준)

#### 1) MRI 풀 파이프라인 연결

아래 흐름이 백엔드에서 연결되어 있습니다.

1. `scripts/ingest_mri_folder.py` 실행
2. `patients.subject_id`로 대상 환자 조회
3. `mri_assessments` 레코드 생성 (`pending`)
4. Celery 태스크 `process_mri_scan` 비동기 발행
5. 워커에서 MRI 전처리 -> 모델 추론 -> 결과 SQL 반영

중요: `ingest_mri_folder.py`는 태스크를 큐에 넣고 종료되므로, 전처리/추론은 백그라운드에서 계속 실행됩니다.

#### 2) 호스트-컨테이너 실행 안정화

`scripts/ingest_mri_folder.py`에 아래 보완이 반영되어 있습니다.

- 기본 입력 경로 환경변수 지원 (`MRI_INGEST_DEFAULT_PATH`)
- 호스트 경로(`/host/...`) -> 컨테이너 경로(`/app/...`) 변환 처리
- Host에서 `redis` DNS를 못 찾는 경우 자동 fallback
  - `docker exec mci-worker ... send_task(...)` 방식으로 태스크 발행

#### 3) ANTs 기반 MRI 전처리 파이프라인 구현

`src/claude/worker/tasks.py` + `src/claude/worker/mri_utils.py` 기준으로, 모델 학습 전처리 흐름에 맞춰 구성되어 있습니다.

- 입력이 DICOM 폴더면 `dcm2niix`로 NIfTI 변환
- ANTs N4 bias correction
- 템플릿 정합 (기본 `SyNRA`, env로 조정 가능)
- ANTsPyNet brain extraction
- robust normalization + clipping
- 전처리 산출물 저장 후 추론 입력으로 사용

주요 환경변수:

- `MRI_ANTS_REGISTRATION_TYPE` (기본 `SyNRA`)
- `MRI_ANTS_CLIP_MIN`, `MRI_ANTS_CLIP_MAX`

#### 4) 전처리 산출물 캐시/저장 전략

`process_mri_scan`의 동작 우선순위:

1. 로컬 캐시 확인
2. MinIO `mri-preprocessed` 버킷에서 기존 산출물 확인
3. 없으면 새로 전처리 수행 후 `mri-preprocessed`에 업로드

즉, 동일 입력에 대해 이미 산출물이 있으면 재전처리를 건너뛰어 시간과 리소스를 절약합니다.

#### 5) MRI 분류 + MCI 서브타입 분기

`src/claude/worker/model_inference.py` 기준:

- Cascade 3D CNN
  - Stage 1: CN vs Impaired
  - Stage 2: MCI vs AD
- 최종이 MCI인 경우, CatBoost(`.cbm`) 서브타입 분기
  - 입력 피처: 인구통계 + 임상 + 신경심리 + 바이오마커
  - 출력: `sMCI` 또는 `pMCI`

결과는 `mri_assessments.classification`, `predicted_stage`, `confidence`, `probabilities`로 저장됩니다.

#### 6) 한국 시간(KST) 저장 반영

현재는 MRI에 한정하지 않고, 백엔드에서 생성/갱신되는 주요 타임스탬프를 KST 기준으로 저장하도록 반영되었습니다.

- `scripts/ingest_mri_folder.py`
  - `scan_date`, `created_at` -> `TIMEZONE('Asia/Seoul', NOW())`
- `src/claude/worker/tasks.py`
  - `processed_at`(processing/completed/failed) -> `TIMEZONE('Asia/Seoul', NOW())`
- `src/claude/app/db.py`
  - `asyncpg` 풀 세션 타임존을 `Asia/Seoul`로 고정
- `psycopg2` 기반 워커/스크립트 연결
  - DB 접속 옵션에 `-c timezone=Asia/Seoul` 적용
- `datetime.utcnow()`를 직접 저장하던 일부 경로
  - KST 기준 시간 헬퍼로 교체

주의: 이미 저장된 과거 레코드는 자동 변환되지 않으며, 컨테이너 재시작 후 새로 기록되는 데이터부터 적용됩니다.

#### 7) 모델/의존성 정리 (워커)

MRI 워커 경로에서 ANTs + CatBoost 추론이 동작하도록 의존성이 정리되었습니다.

- `antspyx`
- `antspynet`
- `catboost`

의존성은 `pyproject.toml`, `uv.lock`, `Dockerfile.worker` 기준으로 관리됩니다.

#### 8) 프론트엔드 영향 범위

2026-02-16 시점까지는 MRI 백엔드 파이프라인 중심으로 진행되어 프론트 영향이 제한적이었습니다.
이후 2026-02-19 기준으로 프론트 소스 동기화가 추가로 수행되었습니다(아래 10, 11 항목).

#### 9) 백엔드-프론트 연동 현황 (현재 기준)

아래는 코드 기준으로 확인된 연동 상태입니다.

- 연결됨 (의사 화면 핵심 조회):
  - 프론트 `DoctorAdapter`가 `/api/doctor/patient/{patient_id}` 호출
  - 백엔드가 임상 추세, 바이오마커, 방문 정보, MRI 요약을 반환
  - MRI 원본/전처리 슬라이스 조회 엔드포인트 사용 가능
    - `/api/doctor/patients/{patient_id}/mri/original-nii`
    - `/api/doctor/patients/{patient_id}/mri/original-slice.png`
    - `/api/doctor/patients/{patient_id}/mri/preprocessed-slice.png`

- 부분 연결 (설정에 따라):
  - 의사 데이터는 `VITE_DOCTOR_USE_MOCK=false`일 때 실 API를 사용
  - `true`이면 mock 데이터 경로 사용

- 현재 mock 고정:
  - 대상자/보호자 대시보드 컴포저블은 어댑터가 `true`로 고정되어 mock 데이터 사용
  - 코드상 API 호출 함수는 있으나 현재 설정으로는 실서버를 타지 않음

- 라우트 네이밍 불일치(정리 필요):
  - 프론트 호출 경로: `/api/subject/dashboard`, `/api/caregiver/dashboard`
  - 백엔드 실제 경로: `/api/patient/dashboard`, `/api/family/dashboard`
  - 즉, 대상자/보호자 실연동을 위해 경로 통일이 필요

- 음성 세션 경로:
  - 프론트 음성 세션은 `VITE_API_BASE_URL` 기준 REST(`/start`, `/chat`, `/session/upload-audio`, `/session/end`) 사용
  - 동일 라우트 alias(`/api/start`, `/api/chat`, `/api/session/upload-audio`, `/api/session/end`)도 지원
  - 별도로 백엔드에는 `/api/patient/chat` WebSocket 엔드포인트도 존재
  - 현재 어떤 경로를 표준으로 운영할지 결정 후 단일화 권장

- MRI 업로드 -> 추론 자동 시작:
  - 현재는 `scripts/ingest_mri_folder.py`가 풀 파이프라인 진입점
  - 프론트 업로드 이벤트에서 자동으로 ingest를 트리거하는 API 연동은 다음 단계

#### 10) 비밀번호 로그인 백엔드 추가 (2026-02-19)

OAuth 기반 로그인과 별개로, 이메일/비밀번호 로그인 백엔드를 추가했습니다.

- 마이그레이션 추가:
  - `migrations/009_add_password_hash_to_users.sql`
  - `users.password_hash` 컬럼 + `LOWER(email)` 인덱스 추가
- 인증 라우터 확장:
  - `POST /api/auth/register`
  - `POST /api/auth/login`
  - `POST /api/auth/verify-subject-link`
- 해시 방식:
  - `pbkdf2_sha256` 기반 해시 저장/검증
- 기존 Google OAuth 흐름은 그대로 유지

적용 시 유의사항:

- 신규 컬럼 반영을 위해 `009` 마이그레이션 적용이 필요합니다.
- API 컨테이너 재빌드/재시작 후 사용해야 합니다.

#### 11) 프론트 소스 동기화 작업 (2026-02-19)

프론트 최신 UI를 반영하기 위해 원격 브랜치 소스를 안전 방식으로 동기화했습니다.

- 소스 기준 브랜치:
  - `oracle/chore/upload-sim-web-service-llm-20260219`
- 동기화 원칙:
  - 원격 `src/**` -> 로컬 `frontend/src/**` 매핑 반영
  - 백엔드 코드가 있는 로컬 `src/claude/**`는 절대 덮어쓰지 않음
- 반영 결과:
  - 매핑 대상 77개 파일 기준 `SAME=77, DIFF=0, NEW=0` 상태 확인
  - 프론트 빌드(`npm --prefix frontend run build`) 성공

주의:

- 동기화 과정에서 프론트 로그인/회원가입 화면 로직이 원격 최신본으로 바뀔 수 있으므로,
  비밀번호 로그인 UI를 최종 운영하려면 백엔드 `/api/auth/*` 엔드포인트 호출 연결을 재확인해야 합니다.

#### 12) LLM 세션 파이프라인 백엔드 연동 (2026-02-19)

LLM 대화 세션의 결과를 PostgreSQL + MinIO에 남기고, 조건 충족 시 음성 ML 워커까지 자동 연결되도록 반영했습니다.

- 신규/수정 파일:
  - `src/claude/app/routers/llm_session.py` (신규)
  - `src/claude/app/schemas/llm_session.py` (신규)
  - `src/claude/app/main.py` (라우터 등록)
  - `src/claude/app/config.py` (`llm_session_output_bucket`)
  - `src/claude/app/storage.py` (기본 버킷 생성 목록 확장)

- REST 엔드포인트:
  - `POST /start` (`/api/start` alias)
  - `POST /chat` (`/api/chat` alias)
  - `POST /session/upload-audio` (`/api/session/upload-audio` alias)
  - `POST /session/end` (`/api/session/end` alias)

- 저장 구조:
  - MinIO: `llm-session-outputs` 버킷에 세션 오디오/대화 로그/manifest 저장
  - PostgreSQL:
    - `llm_chat_sessions`
    - `llm_chat_turns`
    - `llm_session_outputs`
  - 공통 오브젝트 이력: `storage_objects`에도 함께 기록

- Celery 트리거 규칙(`process_voice_recording`):
  - 업로드 파일은 우선 `llm-session-outputs`에 항상 저장
  - 아래 조건에서만 음성 ML 파이프라인 큐 발행
    - 업로드 포맷이 `wav`
    - 세션 transcript(사용자 발화 누적)가 비어있지 않음
  - 조건 충족 시:
    - `voice-recordings` / `voice-transcript` 객체 생성
    - `recordings` 레코드 생성
    - Redis broker 통해 Celery 태스크 enqueue

- 운영 시 주의:
  - API 컨테이너 환경변수는 `docker/.env`를 기준으로 로드됩니다.
  - 변경 반영 시 아래처럼 env 파일을 명시해 재기동하는 것을 권장합니다.

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml up -d api
```

#### 13) 프론트-백엔드 LLM 컨텍스트 자동 연결 (frontend_backend_connect, 2026-02-19)

LLM 프롬프트가 프론트의 고정 더미값에만 의존하지 않도록, `patient_id` 기준 최신 MRI 진단 플래그를 백엔드에서 자동 주입하도록 반영했습니다.

- 대상 파일:
  - `src/claude/app/routers/llm_session.py`
  - `src/claude/app/llm.py`

- 동작 방식:
  1. `/start`, `/chat` 요청 시 `meta.patient_id`로 환자 식별
  2. `mri_assessments` 최신 1건을 조회
  3. 의사진단 체크박스(Boolean) 컬럼을 LLM 지역 키로 매핑
  4. `model_result`를 서버에서 병합(override) 후 프롬프트 구성

- MRI 체크박스 -> LLM 지역 매핑:
  - `hippocampal_atrophy` -> `hippocampus_atrophy`
  - `medial_temporal_atrophy` -> `temporal_atrophy`
  - `white_matter_lesions` -> `white_matter_lesions`
  - `frontal_atrophy` -> `frontal_atrophy`
  - `parietal_atrophy` -> `parietal_atrophy`

- 최종 프롬프트 반영 필드(자동):
  - `stage`
  - `risk_level`
  - `neuro_pattern`
  - `main_region`
  - `region_scores`
  - `recommended_training`

- 장애/예외 처리:
  - MRI 데이터가 없거나 조회 실패 시 기존 로직으로 fallback
  - 즉, 서비스 중단 없이 일반 대화 경로 유지

- 기대 효과:
  - 의사가 체크한 뇌영역 정보가 같은 환자 세션 프롬프트에 일관되게 반영
  - 프론트가 단순 payload를 보내도 백엔드에서 환자 맞춤형 컨텍스트 보정 가능

### 실행 방법 (MRI 풀 파이프라인)

프로젝트 루트에서:

```bash
<PROJECT_ROOT>/.venv/bin/python <PROJECT_ROOT>/scripts/ingest_mri_folder.py <MRI_FOLDER_PATH>
```

기본 경로를 쓰는 경우:

```bash
<PROJECT_ROOT>/.venv/bin/python <PROJECT_ROOT>/scripts/ingest_mri_folder.py
```

실행 후 출력되는 `Task ID`는 큐에 정상 등록되었다는 의미입니다.

### 상태 확인 방법

#### 워커 로그 확인

```bash
docker logs -f mci-worker
```

로그에서 아래 흐름을 확인하면 정상 진행입니다.

1. `Running ANTs preprocessing ...`
2. `Running 3D CNN Inference ...`
3. `Inference Result: ...`

#### SQL에서 결과 확인 (KST 기준 조회)

```sql
SET TIME ZONE 'Asia/Seoul';

SELECT
  assessment_id,
  patient_id,
  classification,
  predicted_stage,
  confidence,
  preprocessing_status,
  scan_date,
  processed_at,
  created_at
FROM mri_assessments
ORDER BY created_at DESC
LIMIT 20;
```

#### LLM 세션 + 음성 워커 연동 확인 SQL

```sql
-- 최근 LLM 세션 상태
SELECT session_id, patient_id, status, started_at, ended_at, end_reason
FROM llm_chat_sessions
ORDER BY created_at DESC
LIMIT 20;

-- 세션 산출물(MinIO 객체) 확인
SELECT session_id, output_type, bucket, object_key, size_bytes, created_at
FROM llm_session_outputs
ORDER BY created_at DESC
LIMIT 30;

-- 음성 워커 처리 완료 여부 확인
SELECT recording_id, training_id, patient_id, status, file_path, uploaded_at
FROM recordings
ORDER BY created_at DESC
LIMIT 20;
```

### 참고: 전처리 단독 테스트 스크립트

전처리만 단독 점검할 때는 아래 스크립트를 사용합니다.

```bash
<PROJECT_ROOT>/.venv/bin/python <PROJECT_ROOT>/scripts/preprocess_mri_subject_antspy.py
```

이 스크립트는 전체 DB->Celery->추론 파이프라인을 모두 수행하는 진입점은 아닙니다.

### 음성 파일 복사 (m_ch -> final)

MinIO의 `minio-data/` 내부 폴더를 OS 파일 탐색기로 보면 `xl.meta` 같은 내부 메타데이터 구조가 보일 수 있습니다.
이것은 정상 동작이며, 실제 `.wav`/`.txt` 파일 복사는 반드시 MinIO API(S3)로 해야 합니다.

1. m_ch MinIO(`9003`)에서 final MinIO(`9000`)로 실제 객체 복사:

```bash
<PROJECT_ROOT>/.venv/bin/python <PROJECT_ROOT>/scripts/copy_voice_pair_from_mch_minio.py \
  --audio-key "patients/patient_001/<session_id>/audio/<file.wav>" \
  --transcript-key "patients/patient_001/<session_id>/transcript/<file.txt>"
```

2. final API에 등록 + Celery 큐 발행:

```bash
curl -X POST "http://localhost:8000/api/patient/recordings/from-minio?patient_id=100&audio_key=voice-recordings/patients/patient_001/<session_id>/audio/<file.wav>&transcript_key=voice-transcript/patients/patient_001/<session_id>/transcript/<file.txt>"
```

현재 `from-minio` 엔드포인트는 전달한 `audio_key`/`transcript_key` 원본 객체를 그대로 사용해 DB 레코드를 생성합니다.
(파일명/키를 강제로 UUID로 바꾸지 않음)

### 향후 계획 (요약)

- CAM/XAI 결과 산출 및 저장 경로 분리(`mri-xai` 버킷)
- 전처리/추론 상태를 API에서 더 상세하게 노출
- 실패 재처리(retry) 및 운영 관측성(메트릭/알림) 강화
