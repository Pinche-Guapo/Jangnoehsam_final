<script setup>
import { computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import CaregiverShell from '@/components/shells/CaregiverShell.vue';
import DoctorShell from '@/components/shells/DoctorShell.vue';
import SubjectShell from '@/components/shells/SubjectShell.vue';

const authStore = useAuthStore();
const role = computed(() => authStore.role);
const userName = computed(() => authStore.userName);
const router = useRouter();

const roleLabel = computed(() => {
  if (role.value === 'doctor') return '의료진 계정';
  if (role.value === 'caregiver') return '보호자 계정';
  return '대상자 계정';
});

const goToPersonalInfo = () => {
  router.push({ name: 'personal-info' });
};

const goToNotifications = () => {
  router.push({ name: 'notification-settings' });
};

const goToCaregiverManagement = () => {
  router.push({ name: 'caregiver-management' });
};

const goToCaregiverSharingSettings = () => {
  router.push({ name: 'caregiver-sharing-settings' });
};

const handleLogout = () => {
  authStore.clear();
  router.push({ name: 'landing' });
};

onMounted(() => {
  if (role.value === 'doctor') {
    router.replace({ name: 'doctor-settings' });
  }
});
</script>

<template>
  <component
    :is="role === 'caregiver' ? CaregiverShell : role === 'doctor' ? DoctorShell : SubjectShell"
    title="설정"
  >
    <div class="settings-container">
      <section class="user-info-card">
        <div class="avatar-placeholder"></div>
        <div class="details">
          <h2>{{ userName }} 님</h2>
          <p>{{ roleLabel }}</p>
        </div>
      </section>

      <section class="settings-cards">
        <button class="settings-card profile-card" @click="goToPersonalInfo">
          <div class="card-icon">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#4cb7b7" stroke-width="2.2">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
              <circle cx="12" cy="7" r="4"/>
            </svg>
          </div>
          <div class="card-content">
            <h3>개인정보 수정</h3>
            <p>이름, 연락처, 기본 프로필 관리</p>
          </div>
          <svg class="chevron" width="24" height="24" viewBox="0 0 24 24">
            <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z" fill="#aaa"/>
          </svg>
        </button>

        <button v-if="role === 'subject'" class="settings-card" @click="goToCaregiverSharingSettings">
          <div class="card-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#4cb7b7" stroke-width="2">
              <path d="M12 4a4 4 0 1 0 0 8a4 4 0 0 0 0-8"/>
              <path d="M4 20v-1a8 8 0 0 1 16 0v1"/>
              <path d="M17.5 7.5l2 2 3-3"/>
            </svg>
          </div>
          <div class="card-content">
            <h3>보호자 정보 공유 범위</h3>
            <p>대화 요약, 관찰 필요 알림, 복약 리마인드 항목을 언제든 철회할 수 있습니다.</p>
          </div>
          <svg class="chevron" width="24" height="24" viewBox="0 0 24 24">
            <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z" fill="#aaa"/>
          </svg>
        </button>

        <button v-if="role !== 'subject'" class="settings-card" @click="goToNotifications">
          <div class="card-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#4cb7b7" stroke-width="2">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
              <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
            </svg>
          </div>
          <div class="card-content">
            <h3>알림 상세 설정</h3>
            <p>변화 알림, 주간 리포트, 서비스 공지</p>
          </div>
          <svg class="chevron" width="24" height="24" viewBox="0 0 24 24">
            <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z" fill="#aaa"/>
          </svg>
        </button>

        <button v-if="role === 'caregiver'" class="settings-card" @click="goToCaregiverManagement">
          <div class="card-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#4cb7b7" stroke-width="2">
              <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
              <circle cx="8.5" cy="7" r="4"/>
              <path d="M20 8v6"/>
              <path d="M23 11h-6"/>
            </svg>
          </div>
          <div class="card-content">
            <h3>보호자 연결 관리</h3>
            <p>대상자 연결 및 권한 설정</p>
          </div>
          <svg class="chevron" width="24" height="24" viewBox="0 0 24 24">
            <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z" fill="#aaa"/>
          </svg>
        </button>
      </section>

      <button class="logout-text-button" @click="handleLogout">
        로그아웃
      </button>
    </div>
  </component>
</template>

<style scoped>
.settings-container {
  --card-surface: #f7f9fa;
  --card-elevation-main:
    0 10px 22px rgba(126, 140, 154, 0.18),
    0 3px 8px rgba(126, 140, 154, 0.11),
    0 1px 3px rgba(126, 140, 154, 0.06);
  --card-elevation-icon:
    0 10px 18px rgba(126, 140, 154, 0.18),
    0 3px 8px rgba(126, 140, 154, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.62);
  --card-elevation-hover:
    0 11px 20px rgba(126, 140, 154, 0.16),
    0 4px 10px rgba(126, 140, 154, 0.12);
  display: flex;
  flex-direction: column;
  gap: 28px;
  min-height: calc(100vh - 200px);
}

.user-info-card {
  display: flex;
  align-items: center;
  gap: 20px;
  background: var(--card-surface);
  padding: 24px;
  border-radius: 28px;
  box-shadow: var(--card-elevation-main);
}

.avatar-placeholder {
  width: 72px;
  height: 72px;
  background: #f9fbfb;
  border-radius: 50%;
  box-shadow: var(--card-elevation-icon);
}

.details h2 {
  font-size: 20px;
  font-weight: 800;
  margin: 0 0 4px;
  color: #2e2e2e;
}

.details p {
  font-size: 16px;
  color: #4cb7b7;
  font-weight: 700;
  margin: 0;
}

.settings-cards {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.settings-card {
  display: flex;
  align-items: center;
  gap: 20px;
  width: 100%;
  padding: 24px;
  border: none;
  border-radius: 28px;
  background: var(--card-surface);
  box-shadow: var(--card-elevation-main);
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
  text-align: left;
}

.profile-card {
  gap: 16px;
  padding: 20px 24px;
  border-radius: 24px;
}

.profile-card .card-icon {
  width: 80px;
  height: 80px;
  min-width: 80px;
  border-radius: 22px;
}

.profile-card .card-content h3 {
  font-size: 20px;
  line-height: 1.2;
  margin: 0 0 8px;
}

.profile-card .card-content p {
  font-size: 15px;
  line-height: 1.35;
  max-width: 280px;
}

.settings-card:active {
  transform: translateY(1px);
}

.settings-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--card-elevation-hover);
}

.card-icon {
  width: 64px;
  height: 64px;
  min-width: 64px;
  border-radius: 20px;
  background: #f9fbfb;
  box-shadow: var(--card-elevation-icon);
  display: flex;
  align-items: center;
  justify-content: center;
}

.card-content {
  flex: 1;
}

.card-content h3 {
  font-size: 20px;
  font-weight: 800;
  color: #2e2e2e;
  margin: 0 0 6px;
}

.card-content p {
  font-size: 15px;
  font-weight: 600;
  color: #777;
  margin: 0;
}

.chevron {
  flex-shrink: 0;
}

.logout-text-button {
  background: none;
  border: none;
  font-size: 18px;
  font-weight: 700;
  color: #ff8a80;
  padding: 20px;
  cursor: pointer;
  margin-top: auto;
  transition: opacity 0.2s;
}

.logout-text-button:hover {
  opacity: 0.8;
}
</style>
