# HODU 프로젝트 포트폴리오

## 0. 지원자 요약

HODU는 음성·MRI 이중 모달 데이터를 활용해 인지저하(MCI) 평가 워크플로우를 지원하는 의료 AI 플랫폼입니다.  
저는 이 프로젝트에서 데이터 파이프라인과 모델 추론을 서비스 레벨로 연결하는 백엔드/ML 엔지니어링 영역을 중심으로, 다음을 구현했습니다.

1. API, Worker, DB, Object Storage를 분리한 운영형 아키텍처 설계 및 구현
2. 음성/MRI 비동기 추론 파이프라인과 결과 저장 자동화
3. 역할 기반(의사/환자/보호자) 기능 분리와 운영 안정성(헬스체크, 재시도, 리소스 튜닝) 확보

---

## 1. 프로젝트 개요

### 1-1. 문제 정의

의료 현장에서 인지저하 평가는 데이터 종류(음성, MRI, 임상정보), 처리시간, 저장 규정, 사용자 역할이 복합적으로 얽혀 있습니다.  
단일 모델 성능만으로는 실제 사용이 어렵고, 다음이 동시에 필요했습니다.

1. 비동기 대용량 처리: MRI/음성 처리시간을 사용자 요청과 분리
2. 결과 추적성: 입력, 전처리, 추론, 결과 저장까지 이력 관리
3. 역할 기반 접근: 의사/환자/보호자 별 접근 화면과 API 분리
4. 저사양 환경 고려: 제한된 리소스에서 안정적으로 동작

### 1-2. 목표

1. 멀티모달 인지평가 서비스를 단일 스택으로 운영 가능한 형태로 구현
2. 모델 추론 결과를 DB + MinIO에 구조적으로 저장해 재사용성 확보
3. 의료 데이터 취급 원칙(민감정보 최소화, synthetic 활용)을 반영한 개발/운영 체계 확보

---

## 2. 기술 아키텍처

### 2-1. 시스템 구성

Docker Compose 기반 6개 서비스로 구성했습니다.

1. `api` (FastAPI)
2. `worker` (Celery)
3. `postgres` (PostgreSQL 16)
4. `redis` (큐/백엔드)
5. `minio` (오브젝트 스토리지)
6. `nginx` (정적/프록시)

근거: `docker/docker-compose.yml`

### 2-2. 요청/처리 흐름

1. 프론트(Vue)에서 음성/MRI 업로드 및 조회 요청
2. API(FastAPI)가 메타데이터를 DB에 저장하고 Celery 작업 발행
3. Worker가 MinIO에서 원본 로드 후 전처리/추론 수행
4. 결과(JSON, 확률, XAI, 상태)를 DB 및 MinIO에 저장
5. 의사/환자/보호자 화면에서 권한 기반으로 결과 조회

근거: `src/app/routers/*.py`, `src/worker/tasks.py`, `src/app/storage.py`

---

## 3. 핵심 구현 상세

### 3-1. API 계층 설계 및 역할 기반 엔드포인트

라우터 레벨에서 역할별 기능을 분리했습니다.

1. `auth`: 회원가입/로그인/JWT/Google OAuth/프로필
2. `doctor`: 환자목록, 리포트, MRI 결과 이미지, 진단 입력
3. `patient`: 녹음 업로드, 채팅, 진행도/대시보드
4. `family`: 보호자 관점 환자 상태/기록 조회
5. `notifications`: 알림 조회/읽음 처리
6. `llm_session`: 세션 시작/대화/종료/음성 업로드
7. `health`: DB/MinIO/Redis 헬스체크

정량 근거:

1. 라우터 데코레이터 기준 API 경로 71개
2. `doctor.py` 단독 21개, `patient.py` 12개, `auth.py` 12개

근거: `src/app/main.py`, `src/app/routers/*.py`

### 3-2. 음성 모델링 파이프라인 운영화

음성 파이프라인은 "Transcript-first" 전략으로 설계했습니다.

1. 업스트림 transcript를 입력으로 받아 STT 의존성을 줄임
2. 형태소/언어학 기반 세션 피처를 추출(Kiwi 기반 엔진)
3. 경량 번들 모델(joblib) 우선, 레거시 모델 fallback 지원
4. 임계값(`VOICE_MCI_THRESHOLD`) 기반 분류 + SHAP 출력 저장
5. 실패 시 상태 롤백/재시도 및 진단 알림 연동

핵심 의사결정:

1. 모델 추론 정확도만이 아니라 운영 안정성과 재현성을 우선
2. 환경변수 중심으로 모델 경로/threshold/feature 모드 제어 가능하게 설계

근거: `src/worker/tasks.py`, `src/worker/feature_extractor.py`, `src/worker/transcript_feature_engine.py`, `src/worker/model_inference.py`

### 3-3. MRI 파이프라인: 전처리 캐시 + XAI 산출물 저장

MRI 파이프라인은 원본 형식(DICOM/NIfTI) 다양성을 흡수하도록 구현했습니다.

1. MinIO object/prefix 모두 입력으로 허용
2. DICOM 시리즈 자동 탐색 후 NIfTI 변환
3. ANTs 전처리(정합, 보정, 정규화) 수행
4. 전처리 결과 캐시(local + MinIO `mri-preprocessed`) 재사용
5. 추론 결과와 함께 CAM/XAI 이미지를 `mri-xai` 버킷에 저장
6. DB의 `mri_assessments`에 확률, stage, region contribution, ai_analysis 업데이트

핵심 의사결정:

1. 전처리 재사용 전략으로 반복 작업 비용 절감
2. XAI 산출물을 오브젝트 스토리지에 분리해 추적성과 프론트 연동성 개선

근거: `src/worker/tasks.py`, `src/worker/mri_utils.py`, `src/worker/mri_xai.py`

### 3-4. LLM 세션 및 임상 맥락 결합

LLM 세션 라우터는 대화형 인지훈련 흐름을 API로 관리합니다.

1. 세션 시작/대화/종료/출력 업로드 엔드포인트 구현
2. 환자 최신 MRI 맥락(위험도/부위)을 세션 컨텍스트로 병합
3. 세션/턴/산출물 테이블을 런타임에서 보장(`CREATE TABLE IF NOT EXISTS`)
4. 외부 LLM 제공자 사용을 `LLM_EXTERNAL_ALLOWED`로 제어

근거: `src/app/routers/llm_session.py`, `src/app/llm.py`, `src/app/config.py`

### 3-5. 스토리지/DB 계층

1. MinIO 기본 버킷을 앱 시작 시 자동 보장
2. DB 커넥션 풀(asyncpg)로 API 쿼리 계층 표준화
3. 마이그레이션 기반 핵심 스키마 운영

정량 근거:

1. 메인 스키마 `CREATE TABLE` 17개
2. SQL 마이그레이션 파일 10개

근거: `src/app/storage.py`, `src/app/db.py`, `migrations/004_mci_full_schema.sql`, `migrations/*.sql`

### 3-6. 프론트엔드 역할 분리 UX

1. Vue Router 가드에서 인증/권한(role) 기반 접근 제어
2. 홈/히스토리/설정을 역할별 컴포넌트로 분기
3. 페이지 단위 라우트와 도메인 컴포넌트 구조 분리

정량 근거:

1. `pages` 20개
2. `views` 4개
3. `components` 27개

근거: `frontend/src/router/index.js`, `frontend/src/stores/auth.js`, `frontend/src/components/home/HomeByRole.vue`, `frontend/src/pages/*`

---

## 4. 운영 안정성 설계

### 4-1. 헬스체크

1. `/health`
2. `/health/db`
3. `/health/minio`
4. `/health/redis`

근거: `src/app/routers/health.py`

### 4-2. 워커 안정화

1. `task_time_limit=3600` 설정
2. `CELERY_WORKER_PREFETCH_MULTIPLIER=1` 기본값
3. `max_tasks_per_child` 설정으로 메모리 누수성 이슈 완화
4. 실패 시 `max_retries=3`, `countdown=60` 재시도 정책

근거: `src/worker/tasks.py`, `docker/docker-compose.yml`

### 4-3. 저사양 환경 최적화

1. 스레드(`OMP`, `MKL`, `OPENBLAS`) 제한
2. worker concurrency 1 기본값으로 과부하 방지
3. 캐시/사전처리 산출물 재사용으로 반복 연산 절감

근거: `docker/docker-compose.yml`, `src/worker/tasks.py`

---

## 5. 데이터 거버넌스 및 윤리

이 프로젝트는 의료 데이터 특성에 맞춰 민감정보 최소화 원칙을 유지합니다.

1. synthetic 데이터 생성 스크립트 별도 운영(원본 데이터 대체 데모)
2. 민감 경로(`data`, `minio-data`, `runtime`, `db_backups`) 분리 관리
3. 오브젝트 버킷을 원본/전처리/XAI로 구분해 접근 경계를 명확화

정량 근거:

1. synthetic 데이터 자동 생성 환자 수 기본값 5명

근거: `ops/generate_synthetic_dataset.py`, `.gitignore`, `src/app/storage.py`

---

## 6. 트러블슈팅 사례 (면접형)

### 사례 1. MRI 입력 형식 불일치(DICOM folder vs NIfTI file)

문제: 입력이 단일 파일일 때와 폴더 prefix일 때 처리 경로가 달라 실패 가능성이 높음  
해결: object/prefix 양쪽을 모두 지원하고 DICOM series 최적 폴더 자동 탐색 + NIfTI 변환 단계 추가  
효과: MRI ingest 실패 유형을 구조적으로 흡수하고 운영 입력 제약 완화

근거: `src/worker/tasks.py`

### 사례 2. 전처리 중복 비용

문제: 동일 MRI 재처리 시 ANTs 전처리 비용이 반복됨  
해결: 로컬 캐시 -> MinIO preprocessed bucket -> 신규 전처리 순으로 단계적 재사용 전략 적용  
효과: 반복 작업 시간/리소스 소모를 줄이고 재현 가능한 처리 경로 확보

근거: `src/worker/tasks.py`

### 사례 3. 음성 파이프라인의 운영 복잡도

문제: STT까지 워커에서 강제하면 의존성과 장애면이 커짐  
해결: transcript-first 경량 파이프라인으로 분리하고, SHAP 포함 결과를 DB에 구조화 저장  
효과: 모델 추론 경로 단순화, 장애 분리, 설명 가능성 확보

근거: `src/worker/feature_extractor.py`, `src/worker/model_inference.py`, `src/worker/tasks.py`

---

## 7. 포지션 매칭 포인트

### 7-1. 의료/바이오 데이터 분석가

1. 임상 변수와 모델 산출물의 연결 구조를 DB/JSON으로 설계
2. 음성·MRI 멀티모달 데이터를 운영 가능한 파이프라인으로 구현
3. 결과 해석(XAI/SHAP) 및 임상 의사결정 보조에 필요한 구조를 반영

### 7-2. 백엔드 엔지니어

1. 권한 기반 API 설계와 비동기 작업 분리
2. DB/캐시/오브젝트 스토리지 분리 아키텍처
3. 헬스체크, 재시도, 자원 제어까지 포함한 운영 관점 구현

### 7-3. ML 엔지니어

1. 모델 번들/레거시 경로 분기 및 환경변수 기반 실험/운영 제어
2. 데이터 전처리와 추론 산출물 저장 표준화
3. 설명 가능성(XAI, SHAP) 산출물 파이프라인 내 통합

---

## 8. 이력서용 핵심 불릿 (복붙용)

1. FastAPI, Celery, PostgreSQL, Redis, MinIO 기반의 멀티모달 인지평가 플랫폼 아키텍처를 설계하고 서비스 간 책임을 분리했습니다.
2. 의사/환자/보호자 역할 기반 API를 구현해 라우터 데코레이터 기준 71개 엔드포인트를 운영 가능한 형태로 구성했습니다.
3. 음성 파이프라인을 transcript-first 구조로 재설계해 STT 의존도를 분리하고 경량 번들 모델 추론 경로를 구축했습니다.
4. SHAP 기반 설명 가능성 결과를 DB에 구조화 저장해 임상 관점의 해석 가능성을 강화했습니다.
5. MRI ingest 단계에서 object/prefix를 모두 지원하고 DICOM series 자동 탐색 및 NIfTI 변환 경로를 구현했습니다.
6. MRI 전처리 캐시(local + MinIO) 전략을 도입해 반복 처리 비용을 줄이고 재현 가능한 추론 경로를 확보했습니다.
7. CAM/XAI 이미지를 `mri-xai` 버킷으로 분리 저장하고 region contribution을 API 소비 가능한 JSON 형태로 표준화했습니다.
8. Docker Compose 6개 서비스 기반 배포 환경에서 healthcheck, 재시도, 리소스 튜닝까지 포함한 운영 안정성을 확보했습니다.
9. 메인 스키마 17개 테이블과 마이그레이션 체계를 구축해 의료 데이터 워크플로우를 데이터 모델로 정착시켰습니다.
10. synthetic 데이터 생성 자동화를 통해 민감정보 비노출 원칙을 준수하면서 데모/검증 가능한 개발 환경을 마련했습니다.

---

## 9. 면접 대비 핵심 답변 프레임

### Q1. 이 프로젝트에서 본인이 가장 잘한 기술적 선택은?

운영 가능성 기준으로 파이프라인을 쪼갠 점입니다.  
음성은 transcript-first로 단순화했고, MRI는 전처리 캐시를 도입해 반복 비용을 줄였습니다.  
모델 정확도 수치 외에도 실패 복구, 재시도, 저장 경계까지 포함해 실제 서비스에 가까운 구조를 만들었습니다.

### Q2. 모델링 프로젝트가 아니라 서비스 프로젝트라고 볼 근거는?

모델 호출 이전과 이후의 경계를 명확히 설계했습니다.  
입력 정규화, 비동기 작업 큐, 결과 저장 스키마, 알림, 조회 API, 권한 제어까지 연결되어 있기 때문에 단순 실험 코드가 아니라 운영형 시스템입니다.

### Q3. 의료 데이터 관점에서 어떤 점을 신경 썼나?

원본 데이터 노출을 최소화하고 synthetic 데이터 활용 경로를 별도로 둔 점입니다.  
또한 버킷 분리와 메타데이터 중심 저장 구조로 데이터 추적성과 접근 경계를 확보했습니다.

---

## 10. 다음 고도화 계획

1. 통합 테스트/회귀 테스트 스위트 추가로 배포 안정성 강화
2. 모델 성능 모니터링(캘리브레이션, drift, cohort 단위 분석) 자동화
3. 비식별화/접근통제 정책 문서화를 통한 운영 컴플라이언스 강화
4. LLM 세션과 음성/MRI 결과의 멀티모달 피드백 루프 정교화

---

## 11. 검증 가능한 정량 스냅샷

1. Docker 서비스: 6개 (`postgres`, `minio`, `redis`, `api`, `worker`, `nginx`)
2. API 라우트 데코레이터: 71개
3. 메인 스키마 테이블: 17개
4. SQL 마이그레이션 파일: 10개
5. 프론트 페이지/뷰/컴포넌트: 20/4/27
6. synthetic 데이터 기본 생성 규모: 5명

근거: `docker/docker-compose.yml`, `src/app/routers/*.py`, `migrations/004_mci_full_schema.sql`, `migrations/*.sql`, `frontend/src/*`, `ops/generate_synthetic_dataset.py`
