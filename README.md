# MCI Multimodal Platform (Portfolio)

Raspberry Pi 5 + Docker 기반으로 구축한 멀티모달 인지평가 플랫폼입니다.  
음성 파이프라인과 MRI 파이프라인을 하나의 서비스로 통합해, 의료진/보호자/환자 워크플로우를 지원합니다.


## Demo Notice
- 본 프로젝트는 포트폴리오/데모 목적의 연구용 시스템이며, 의료기기나 임상 진단 대체 수단이 아닙니다.
- 실제 임상 의사결정은 반드시 의료진의 전문 판단과 정식 검사 결과를 기준으로 해야 합니다.
- 배포 환경에는 합성/비식별 테스트 데이터를 사용하며, 라이선스 제한이 있는 원본 데이터는 저장소에 포함하지 않습니다.
- 외부 공개/상용 전환 시에는 데이터 라이선스, 개인정보, 보안 요건을 별도로 검토해야 합니다.

## Project Summary
- 목표: 음성 + MRI 기반 MCI 평가 워크플로우를 실제 서비스 형태로 구현
- 환경: RPi5 NAS(OMV) + Docker Compose + FastAPI + Celery + PostgreSQL + MinIO + Vue
- 핵심: 비동기 파이프라인, 의료진 화면 연동, 저장소/DB 일관성, 운영 안정성

## My Role
- 시스템 아키텍처 설계 및 백엔드 구축 주도
- 멀티모달 파이프라인(음성/MRI) 설계 및 연결
- 비동기 처리(Celery + Redis)와 저장 계층(PostgreSQL + MinIO) 통합
- 저사양 환경 최적화(zram + swap + 워커 튜닝)로 협업 가능한 개발 인프라 제공

## Key Contributions
1. End-to-End MRI Pipeline
- 입력 등록(`mri_assessments`) → Celery 큐 → ANTs 기반 전처리 → 모델 추론 → XAI 산출물 저장
- 결과 저장 위치 분리: 원본/전처리/XAI 버킷
- 환자별 최신 MRI 상태 조회 API와 프론트 연결

2. Voice Pipeline Integration
- 업로드/텍스트 연동/피처 추출/추론/결과 저장 자동화
- SHAP/XAI 결과 저장 및 조회 구조 반영
- 세션형 워크플로우와 DB 스키마 연동

3. 운영 안정화
- 워커 파라미터 보수화(`concurrency`, `prefetch`, thread 제한)
- 재시도/실패 상태 관리 및 로그 기반 트러블슈팅 루프 구축
- KST 기준 타임스탬프 일관성 정리

4. 데이터/컴플라이언스 가드
- 민감 데이터 경로 Git 제외 (`data/`, `minio-data/`, `runtime/`, `db_backups/`)
- 합성 데이터 기반 테스트 워크플로우 구축
- 라이선스 리스크 점검 체크리스트 반영

## Architecture
- Frontend: Vue 3 + Vite + Pinia
- API: FastAPI (`src/app`)
- Worker: Celery (`src/worker/tasks.py`)
- Queue: Redis
- DB: PostgreSQL 16
- Object Storage: MinIO
- Reverse Proxy: Nginx

## Service Map
- `mci-api`: 인증/환자/의료진/세션 API
- `mci-worker`: 음성/MRI 비동기 작업 처리
- `mci-postgres`: 임상/평가/파일 메타데이터 저장
- `mci-minio`: 파일 오브젝트 저장
- `mci-redis`: 태스크 브로커/백엔드
- `mci-nginx`: 정적 프론트 및 API 프록시

## Pipeline Overview
### MRI
1. MRI 소스 등록
2. Celery `process_mri_scan` 큐잉
3. 전처리(ANTs)
4. 추론/확률 계산
5. XAI(Grad-CAM/ROI) 산출
6. DB + MinIO 반영

### Voice
1. 음성/텍스트 업로드
2. 비동기 피처 추출
3. 모델 추론 + SHAP 결과 생성
4. DB 반영 및 화면 조회


## MRI 전처리 단계별 의미
1. DICOM -> NIfTI 변환
- 소스 포맷을 3D 볼륨 기준으로 통일해 파이프라인 입력 형식을 일관화합니다.

2. Bias Field Correction (N4)
- 위치에 따라 달라지는 저주파 밝기 왜곡을 보정해 조직 대비를 안정화합니다.

3. 템플릿 정합 (Registration, ANTs)
- 환자별 자세/크기 차이를 표준 공간으로 맞춰, 동일 좌표 비교가 가능하도록 만듭니다.

4. Brain Extraction (Skull stripping)
- 두개골/배경을 제거해 모델이 뇌 실질 정보에 집중하도록 합니다.

5. Intensity Normalization + Clipping
- 입력 분포를 학습 시 분포에 가깝게 맞춰 추론 안정성을 높입니다.

6. 전처리 캐시/저장
- 전처리 결과를 재사용해 재처리 비용을 줄이고 운영 효율을 높입니다.

7. 추론 + XAI
- 단계 예측 결과와 ROI/Grad-CAM 근거를 함께 생성해 해석 가능성을 확보합니다.

## Voice 파이프라인 피처 세분화
1. 입력 수집
- 음성 파일과 텍스트(전사/사이드카)를 결합해 동일 세션 단위로 처리합니다.

2. 음향 피처 (Acoustic)
- 에너지, 피치, 스펙트럼 계열 통계량으로 발화 안정성과 변동 특성을 반영합니다.

3. 시간/리듬 피처 (Temporal/Prosody)
- 발화 길이, 침묵 비율, 속도 지표를 통해 지연/주저 패턴을 반영합니다.

4. 언어학 피처 (Linguistic)
- 형태소/품사/어휘 다양도 기반 지표로 언어 구성 능력을 계량화합니다.

5. 모델 추론
- 피처 벡터로 확률/단계 라벨을 산출합니다.

6. 설명 가능성 (SHAP)
- 상위 기여 피처를 기록해 결과 설명 근거를 제공합니다.

7. DB/화면 연동
- 세션 결과를 저장하고 환자 이력 화면에서 추적 가능하도록 반영합니다.


## Outcomes
- 멀티모달 파이프라인의 서비스형 통합 완료
- 환자 단위 결과 조회/추적 가능한 데이터 구조 정립
- 저사양 환경에서도 동시 협업 가능한 운영 안정성 확보

## Repository Structure
- `src/app/`: FastAPI API 레이어
- `src/worker/`: Celery 태스크 및 모델 파이프라인
- `frontend/`: Vue 프론트엔드
- `docker/`: compose/env
- `migrations/`: DB 스키마/마이그레이션
- `scripts/`: 운영 보조 스크립트

## Run (Local)
```bash
cd docker
docker compose --env-file .env up -d
```

Frontend dev:
```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5174
```

## DuckDNS + HTTPS Deployment
Prerequisites:
- DuckDNS subdomain created (example: `jns-hodu.duckdns.org`)
- Router/NAT forwards `80` and `443` to this host
- `docker` and `docker compose` available

Set these in `docker/.env`:
```dotenv
NGINX_DOMAIN=jns-hodu.duckdns.org
DUCKDNS_TOKEN=your_duckdns_token
LETSENCRYPT_EMAIL=you@example.com
NGINX_CERTS_HOST_DIR=/absolute/path/to/hodu/.secrets/nginx-certs
```

1) Update DuckDNS A-record:
```bash
./scripts/duckdns-update.sh
```

2) Start services:
```bash
cd docker
docker compose --env-file .env up -d
```

3) Issue Let's Encrypt certificate (HTTP-01 via webroot):
```bash
cd ..
./scripts/issue-duckdns-cert.sh
```

4) Renew later (recommended via cron):
```bash
./scripts/renew-duckdns-cert.sh
```

Quick checks:
```bash
curl -I http://jns-hodu.duckdns.org
curl -I https://jns-hodu.duckdns.org
curl -I https://jns-hodu.duckdns.org/health
```

### HTTPS에서 마이크 권한 설정 (중요)
- 브라우저 마이크 캡처(`getUserMedia`)는 보안 컨텍스트(`https://` 또는 localhost)에서만 동작합니다.
- Nginx가 `Permissions-Policy: microphone=()`를 내려주면 HTTPS여도 마이크 접근이 차단됩니다.
- `nginx.conf`에는 아래 정책을 사용하세요:

```nginx
add_header Permissions-Policy "geolocation=(), microphone=(self), camera=()" always;
```

- 설정 변경 후 Nginx를 리로드하세요:

```bash
cd docker
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
```

- 응답 헤더 확인:

```bash
curl -I https://jns-hodu.duckdns.org | grep -i permissions-policy
```

- 기대 결과:
```text
permissions-policy: geolocation=(), microphone=(self), camera=()
```

### 해외 IP 차단 (KR Allowlist)
- 운영 환경 보안을 위해 Nginx에서 한국(KR) IP 대역 + 로컬/사설망만 허용하도록 설정했습니다.
- 적용 파일:
  - `nginx.conf` (`geo`, `map` 기반 차단 로직)
  - `docker/nginx-geo/allow_kr.conf` (KR CIDR 목록)
  - `docker/docker-compose.yml` (allowlist 파일 마운트)
- 해외 IP는 기본적으로 `403`이 반환됩니다.
- `/health` 경로는 모니터링을 위해 예외로 열어둡니다.

KR CIDR 목록 업데이트:
```bash
./docker/update-nginx-kr-allowlist.sh
```

Nginx 반영:
```bash
cd docker
docker compose up -d nginx
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
```

## Compliance Note
- 이 저장소는 원본 의료 데이터/식별자 공개를 금지합니다.
- 배포 전 합성 데이터만 남기고 민감 데이터(`data/raw`, `db_backups`, `exports`, MinIO 원본 버킷) 제거가 필요합니다.
- 외부 SaaS/LLM 연동 시 데이터 보존 정책을 반드시 검증합니다.
- 외부 라이선스 데이터 마커 제거용 마이그레이션:
  - `migrations/012_sanitize_external_dataset_markers.sql`
  - `migrations/013_add_compliance_guardrails.sql`
- 외부 라이선스 데이터 import 스크립트는 기본 차단됩니다. 라이선스 승인 시에만 `ALLOW_EXTERNAL_LICENSED_DATA=true`로 실행하세요.

---
This README is intentionally portfolio-oriented: project scope, technical ownership, architecture, and delivery outcomes 중심으로 정리했습니다.
