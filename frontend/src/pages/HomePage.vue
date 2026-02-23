<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'; // 역할별 상태와 리스트 필터링을 처리한다
import { useRouter } from 'vue-router'; // 환자 상세 화면으로 이동한다
import { useAuthStore } from '@/stores/auth'; // 인증 상태를 가져온다
import CaregiverShell from '@/components/shells/CaregiverShell.vue';
import DoctorShell from '@/components/shells/DoctorShell.vue';
import SubjectHome from '@/components/home/SubjectHome.vue';
import CaregiverHome from '@/components/home/CaregiverHome.vue';
import { useDoctorData } from '@/composables/useDoctorData';
import { useDoctorPatientStore } from '@/stores/doctorPatient';

const authStore = useAuthStore(); // 인증 상태를 사용한다
const router = useRouter(); // 상세 페이지 이동을 처리한다
const userRole = computed(() => authStore.role); // 현재 역할을 계산한다

const {
  data: doctorData,
  loading: doctorLoading,
  fetchData: fetchDoctorData,
  switchPatient,
  currentPatientId,
  startAutoRefresh,
  stopAutoRefresh,
} = useDoctorData();

const doctorPatientStore = useDoctorPatientStore(); // 선택 환자 메타를 저장한다

const searchQuery = ref(''); // 환자 검색어를 저장한다
const normalizeSearchKeyword = (value) => String(value ?? '')
  .toLowerCase()
  .normalize('NFC')
  .replace(/\s+/g, '')
  .trim();

onMounted(() => {
  if (userRole.value === 'doctor') {
    // 홈에서는 리스트만 사용하므로 상세 데이터가 없는 기본 목록을 불러온다.
    fetchDoctorData(null);
    startAutoRefresh();
  }
});

onUnmounted(() => {
  stopAutoRefresh();
});

const patients = computed(() => doctorData.value?.patients || []); // 환자 목록을 계산한다

const normalizedQuery = computed(() => normalizeSearchKeyword(searchQuery.value)); // 검색어를 정규화한다

const filteredPatients = computed(() => {
  if (!normalizedQuery.value) return patients.value; // 검색어가 없으면 전체를 노출한다
  return patients.value.filter((patient) => {
    const name = normalizeSearchKeyword(patient?.name); // 이름을 비교한다
    const id = normalizeSearchKeyword(patient?.id); // ID를 비교한다
    const rid = normalizeSearchKeyword(patient?.rid); // RID를 비교한다
    const hospital = normalizeSearchKeyword(patient?.hospital); // 병원을 비교한다
    return (
      name.includes(normalizedQuery.value) ||
      id.includes(normalizedQuery.value) ||
      rid.includes(normalizedQuery.value) ||
      hospital.includes(normalizedQuery.value)
    );
  });
});

const formatGender = (gender) => {
  if (gender === 'F') return '여'; // 여성 표기를 반환한다
  if (gender === 'M') return '남'; // 남성 표기를 반환한다
  return '-'; // 값이 없을 때 기본값을 반환한다
};

const riskLabelMap = {
  low: 'LOW',
  mid: 'MID',
  high: 'HIGH'
}; // 위험도 라벨을 통일한다

const normalizeRiskLevel = (riskLevel) => {
  const normalized = String(riskLevel || '').trim().toLowerCase();
  if (normalized === 'medium') return 'mid';
  if (normalized === 'low' || normalized === 'mid' || normalized === 'high') return normalized;
  return 'unknown';
}; // 위험도 값을 표준화한다

const formatRiskLabel = (riskLevel) => riskLabelMap[normalizeRiskLevel(riskLevel)] || 'UNKNOWN'; // 위험도 라벨을 출력한다

const getTrendStatus = (patient) => {
  const backendStatus = String(patient?.trendStatus || '').trim().toLowerCase();
  if (backendStatus === 'watch' || backendStatus === 'stable' || backendStatus === 'unknown') {
    return backendStatus;
  }
  const normalizedRisk = normalizeRiskLevel(patient?.riskLevel);
  if (normalizedRisk === 'mid' || normalizedRisk === 'high') return 'watch';
  if (normalizedRisk === 'low') return 'stable';
  return 'unknown';
}; // 추세 상태를 음성기반 결과 우선으로 계산한다

const getTrendLabel = (patient) => {
  const backendLabel = String(patient?.trendLabel || '').trim();
  if (backendLabel) return backendLabel;
  const status = getTrendStatus(patient);
  if (status === 'watch') return '변화 관찰';
  if (status === 'stable') return '안정 추세';
  return '정보 부족';
}; // 추세 라벨을 계산한다

const handleSelectPatient = (patientId) => {
  if (!patientId) return; // 잘못된 입력을 방지한다
  const patient = patients.value.find((item) => item.id === patientId);
  if (patient) {
    doctorPatientStore.setSelectedPatient({
      id: patient.id,
      rid: patient.rid,
      name: patient.name,
      age: patient.age,
      gender: patient.gender,
      hospital: patient.hospital
    });
  } else {
    doctorPatientStore.setSelectedPatientId(patientId);
  }
  router.push({ name: 'doctor-report', params: { patientId } }); // 환자 리포트로 이동한다
};
</script>

<template>
  <div>
    <SubjectHome v-if="userRole === 'subject'" />

    <CaregiverShell v-else-if="userRole === 'caregiver'" title="모니터링 홈">
      <CaregiverHome />
    </CaregiverShell>

    <DoctorShell
      v-else-if="userRole === 'doctor'"
      title="환자 현황"
      :patients="doctorData?.patients || []"
      :currentPatientId="doctorData?.currentPatient?.id"
      @patient-change="switchPatient"
    >
      <div class="patients-page">
        <div class="page-header">
          <div class="page-title">
            <h2>장기 모니터링 환자</h2>
            <p>최근 변화와 추세를 중심으로 점검합니다.</p>
          </div>
          <div class="summary-chip">총 {{ patients.length }}명</div>
        </div>

        <div class="search-bar">
          <input
            v-model="searchQuery"
            type="search"
            placeholder="이름 / 환자 ID / RID / 병원 검색"
            class="search-input"
          />
        </div>

        <div v-if="doctorLoading" class="loading-state">
          <div class="loading-card"></div>
          <div class="loading-card"></div>
          <div class="loading-card"></div>
        </div>

        <div v-else class="patient-list" role="list">
          <button
            v-for="patient in filteredPatients"
            :key="patient.id"
            type="button"
            class="patient-card"
            :class="{ active: patient.id === currentPatientId }"
            role="listitem"
            @click="handleSelectPatient(patient.id)"
          >
            <div class="patient-main">
              <div class="patient-top">
                <span class="patient-name">{{ patient.name }}</span>
                <span class="patient-meta">{{ patient.age }}세 · {{ formatGender(patient.gender) }}</span>
              </div>
              <div class="patient-sub">
                <span>{{ patient.id }}</span>
                <span class="divider">·</span>
                <span>{{ patient.rid || '-' }}</span>
                <span class="divider">·</span>
                <span>{{ patient.hospital || '-' }}</span>
              </div>
              <div class="patient-flags">
                <span :class="['flag', `flag-${getTrendStatus(patient)}`]">
                  {{ getTrendLabel(patient) }}
                </span>
              </div>
            </div>
            <div class="patient-right">
              <span :class="['risk-pill', `risk-${normalizeRiskLevel(patient.riskLevel)}`]">
                {{ formatRiskLabel(patient.riskLevel) }}
              </span>
            </div>
          </button>

          <div v-if="filteredPatients.length === 0" class="empty-state">
            검색 결과가 없습니다.
          </div>
        </div>
      </div>
    </DoctorShell>

    <div v-else class="error-state">
      <p>역할 정보를 확인할 수 없습니다.</p>
    </div>
  </div>
</template>

<style scoped>
.patients-page {
  display: flex;
  flex-direction: column;
  gap: 22px;
  width: 100%;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}

.page-title h2 {
  margin: 0 0 8px;
  font-size: 22px;
  font-weight: 900;
  color: #2e2e2e;
}

.page-title p {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #5f5f5f;
}

.summary-chip {
  background: #ffffff;
  padding: 10px 16px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 800;
  color: #4cb7b7;
  box-shadow: inset 4px 4px 10px rgba(209, 217, 230, 0.6), inset -4px -4px 10px #ffffff;
}

.search-bar {
  width: 100%;
}

.search-input {
  width: 100%;
  height: 52px;
  border-radius: 20px;
  border: none;
  padding: 0 18px;
  background: #eef1f3;
  box-shadow: inset 4px 4px 10px rgba(209, 217, 230, 0.7), inset -4px -4px 10px #ffffff;
  font-size: 16px;
  font-weight: 700;
  color: #2e2e2e;
}

.search-input:focus {
  outline: none;
  box-shadow: inset 5px 5px 12px rgba(209, 217, 230, 0.8), inset -5px -5px 12px #ffffff;
}

.loading-state {
  display: grid;
  gap: 16px;
}

.loading-card {
  height: 110px;
  border-radius: 24px;
  background: linear-gradient(90deg, #e0e5ec 25%, #f0f3f6 50%, #e0e5ec 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

.patient-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.patient-card {
  width: 100%;
  border: none;
  cursor: pointer;
  padding: 18px 20px;
  border-radius: 24px;
  background: #f5f6f7;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  box-shadow: 12px 12px 24px #d1d9e6, -12px -12px 24px #ffffff;
  text-align: left;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.patient-card:hover {
  transform: translateY(-1px);
  box-shadow: 14px 14px 26px #cfd6df, -14px -14px 26px #ffffff;
}

.patient-card.active {
  background: #f7fbfb;
  box-shadow: inset 6px 6px 14px rgba(209, 217, 230, 0.75), inset -6px -6px 14px #ffffff;
}

.patient-main {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
}

.patient-top {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

.patient-name {
  font-size: 20px;
  font-weight: 900;
  color: #2e2e2e;
}

.patient-meta {
  font-size: 14px;
  font-weight: 800;
  color: #777;
}

.patient-sub {
  font-size: 14px;
  font-weight: 700;
  color: #666;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.divider {
  color: #b2b2b2;
}

.patient-flags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.flag {
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}

.flag-watch {
  background: rgba(255, 183, 77, 0.2);
  color: #f5a623;
}

.flag-stable {
  background: rgba(76, 183, 183, 0.15);
  color: #4cb7b7;
}

.flag-unknown {
  background: #eef1f3;
  color: #888;
}

.patient-right {
  display: flex;
  align-items: center;
}

.risk-pill {
  min-width: 92px;
  text-align: center;
  padding: 8px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0.4px;
}

.risk-low {
  background: rgba(76, 183, 183, 0.15);
  color: #4cb7b7;
}

.risk-mid {
  background: rgba(255, 183, 77, 0.2);
  color: #f5a623;
}

.risk-high {
  background: rgba(255, 138, 128, 0.2);
  color: #ff8a80;
}

.risk-unknown {
  background: #eef1f3;
  color: #888;
}

.empty-state {
  text-align: center;
  color: #777;
  font-weight: 800;
  padding: 24px;
}

.error-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  font-size: 1rem;
  font-weight: 800;
  color: #888;
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

@media (max-width: 520px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .patient-card {
    flex-direction: column;
    align-items: flex-start;
  }

  .patient-right {
    align-self: flex-start;
  }
}
</style>
