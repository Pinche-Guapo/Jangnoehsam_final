<script setup>
import { ref, computed } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import { updateAuthProfile } from '@/api/settings';
import CaregiverShell from '@/components/shells/CaregiverShell.vue';
import DoctorShell from '@/components/shells/DoctorShell.vue';
import SubjectShell from '@/components/shells/SubjectShell.vue';

const authStore = useAuthStore();
const router = useRouter();
const role = computed(() => authStore.role);
const isSubject = computed(() => role.value === 'subject');

const userName = ref(authStore.userName || '');
const phoneNumber = ref(
  authStore.user?.phone_number
  || authStore.user?.phoneNumber
  || localStorage.getItem('user-phone')
  || ''
);
const initialAvatarUrl = (
  authStore.user?.profile_image_url
  || authStore.user?.profileImageUrl
  || localStorage.getItem('user-avatar')
  || null
);

// Remove only locally uploaded photo(data URI), keep server-side profile URLs intact.
const avatarUrl = ref(
  typeof initialAvatarUrl === 'string' && initialAvatarUrl.startsWith('data:')
    ? null
    : initialAvatarUrl
);

if (typeof initialAvatarUrl === 'string' && initialAvatarUrl.startsWith('data:')) {
  localStorage.removeItem('user-avatar');
}
const memberActionMessage = ref('');
const saveMessage = ref('');
const MEMBER_CODE_MODULUS = 1000000;
const MEMBER_CODE_MULTIPLIER = 741457;
const MEMBER_CODE_INCREMENT = 193939;

const toSubjectMemberNumberFromPatientId = (value) => {
  const digits = String(value ?? '').replace(/\D/g, '');
  if (!digits) return '';
  const parsed = Number.parseInt(digits, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return '';
  const encoded = (parsed * MEMBER_CODE_MULTIPLIER + MEMBER_CODE_INCREMENT) % MEMBER_CODE_MODULUS;
  return `SM-${String(encoded).padStart(6, '0')}`;
};

const subjectMemberNumber = computed(() => {
  if (!isSubject.value) return '';
  return toSubjectMemberNumberFromPatientId(authStore.user?.id);
});

const handleAvatarUpload = (event) => {
  const file = event.target.files[0];
  if (file) {
    const reader = new FileReader();
    reader.onload = (e) => {
      avatarUrl.value = e.target.result;
    };
    reader.readAsDataURL(file);
  }
};

const triggerAvatarUpload = () => {
  document.getElementById('avatar-input').click();
};

const setMemberMessage = (text) => {
  memberActionMessage.value = text;
  setTimeout(() => {
    if (memberActionMessage.value === text) {
      memberActionMessage.value = '';
    }
  }, 1800);
};

const copyMemberNumber = async () => {
  if (!subjectMemberNumber.value) return;

  try {
    await navigator.clipboard.writeText(subjectMemberNumber.value);
    setMemberMessage('회원번호가 클립보드에 복사되었습니다.');
  } catch (error) {
    console.error('Failed to copy member number:', error);
    setMemberMessage('복사에 실패했습니다. 다시 시도해 주세요.');
  }
};

const shareMemberNumber = async () => {
  if (!subjectMemberNumber.value) return;

  const message = `대상자 회원번호: ${subjectMemberNumber.value}`;

  if (navigator.share) {
    try {
      await navigator.share({
        title: '대상자 회원번호',
        text: message
      });
      setMemberMessage('회원번호를 공유했습니다.');
      return;
    } catch (error) {
      if (error?.name === 'AbortError') return;
      console.error('Share failed. Falling back to copy:', error);
    }
  }

  try {
    await navigator.clipboard.writeText(message);
    setMemberMessage('공유 기능을 지원하지 않아 메시지를 복사했습니다.');
  } catch (error) {
    console.error('Fallback copy failed:', error);
    setMemberMessage('공유에 실패했습니다. 다시 시도해 주세요.');
  }
};

const saveChanges = async () => {
  const trimmedName = userName.value.trim();
  const phone = phoneNumber.value.trim();

  if (phone) {
    localStorage.setItem('user-phone', phone);
  } else {
    localStorage.removeItem('user-phone');
  }
  if (avatarUrl.value) {
    localStorage.setItem('user-avatar', avatarUrl.value);
  } else {
    localStorage.removeItem('user-avatar');
  }

  try {
    if (authStore.token) {
      const payload = {
        phone_number: phone || null,
      };
      if (trimmedName) {
        payload.name = trimmedName;
      }
      if (typeof avatarUrl.value === 'string') {
        const avatarCandidate = avatarUrl.value.trim();
        if (avatarCandidate && !avatarCandidate.startsWith('data:') && avatarCandidate.length <= 500) {
          payload.profile_image_url = avatarCandidate;
        }
      }

      const updatedUser = await updateAuthProfile(authStore.token, payload);
      authStore.setUser({
        ...(authStore.user || {}),
        ...updatedUser,
      });
    } else if (authStore.user) {
      authStore.setUser({
        ...authStore.user,
        ...(trimmedName ? { name: trimmedName } : {}),
        phone_number: phone || null,
        role: role.value || 'subject'
      });
    }

    saveMessage.value = '';
    router.back();
  } catch (error) {
    console.error('Failed to save personal profile:', error);
    saveMessage.value = error instanceof Error ? `저장 실패: ${error.message}` : '저장에 실패했습니다.';
  }
};
</script>

<template>
  <component
    :is="role === 'caregiver' ? CaregiverShell : role === 'doctor' ? DoctorShell : SubjectShell"
    title="개인정보 수정"
  >
    <div class="personal-info-container">
      <section class="avatar-section">
        <div
          class="avatar-preview"
          :style="avatarUrl ? { backgroundImage: `url(${avatarUrl})`, backgroundSize: 'cover' } : {}"
          @click="triggerAvatarUpload"
        >
          <div class="avatar-overlay">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2">
              <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
              <circle cx="12" cy="13" r="4"/>
            </svg>
          </div>
        </div>
        <input
          id="avatar-input"
          type="file"
          accept="image/*"
          @change="handleAvatarUpload"
          hidden
        />
        <p class="avatar-hint">사진을 터치하여 변경</p>
      </section>

      <section class="form-section">
        <div class="input-group">
          <label for="userName">이름</label>
          <input
            id="userName"
            v-model="userName"
            type="text"
            placeholder="이름을 입력하세요"
            class="neumorphic-input"
          />
        </div>

        <div class="input-group">
          <label for="phoneNumber">연락처</label>
          <input
            id="phoneNumber"
            v-model="phoneNumber"
            type="tel"
            placeholder="010-0000-0000"
            class="neumorphic-input"
          />
        </div>
      </section>

      <section v-if="isSubject" class="member-number-card">
        <p class="member-label">대상자 회원번호</p>
        <strong class="member-value">{{ subjectMemberNumber }}</strong>
        <div class="member-actions">
          <button type="button" class="member-action-btn" @click="copyMemberNumber">회원번호 복사</button>
          <button type="button" class="member-action-btn secondary" @click="shareMemberNumber">메시지로 공유</button>
        </div>
        <p v-if="memberActionMessage" class="member-message">{{ memberActionMessage }}</p>
      </section>

      <p v-if="saveMessage" class="save-message">{{ saveMessage }}</p>

      <button class="save-button" @click="saveChanges">
        저장하기
      </button>
    </div>
  </component>
</template>

<style scoped>
.personal-info-container {
  display: flex;
  flex-direction: column;
  gap: 32px;
  padding-bottom: 24px;
}

.avatar-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.avatar-preview {
  width: 140px;
  height: 140px;
  border-radius: 50%;
  background: #e0e5ec;
  box-shadow: 14px 14px 28px #cfd6df, -14px -14px 28px #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: transform 0.2s;
}

.avatar-preview:active {
  transform: scale(0.98);
}

.avatar-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.2s;
}

.avatar-preview:hover .avatar-overlay {
  opacity: 1;
}

.avatar-hint {
  font-size: 16px;
  font-weight: 600;
  color: #777;
  margin: 0;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.input-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.input-group label {
  font-size: 18px;
  font-weight: 800;
  color: #2e2e2e;
  padding-left: 4px;
}

.neumorphic-input {
  width: 100%;
  padding: 20px 24px;
  border: none;
  border-radius: 20px;
  background: #f5f6f7;
  box-shadow: inset 4px 4px 10px #d1d9e6, inset -4px -4px 10px #ffffff;
  font-size: 22px;
  font-weight: 700;
  color: #2e2e2e;
  outline: none;
  transition: box-shadow 0.2s;
  box-sizing: border-box;
}

.neumorphic-input:focus {
  box-shadow: inset 4px 4px 10px #d1d9e6,
              inset -4px -4px 10px #ffffff,
              0 0 0 3px rgba(76, 183, 183, 0.3);
}

.neumorphic-input::placeholder {
  color: #aaa;
  font-weight: 600;
}

.member-number-card {
  border-radius: 22px;
  padding: 18px;
  background: #f5f6f7;
  box-shadow: inset 4px 4px 10px #d1d9e6, inset -4px -4px 10px #ffffff;
  display: grid;
  gap: 10px;
}

.member-label {
  margin: 0;
  color: #6b7280;
  font-size: 14px;
  font-weight: 700;
}

.member-value {
  font-size: 24px;
  font-weight: 800;
  color: #1f2937;
}

.member-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.member-action-btn {
  border: none;
  border-radius: 14px;
  padding: 10px 14px;
  background: #4cb7b7;
  color: #ffffff;
  font-weight: 800;
  cursor: pointer;
}

.member-action-btn.secondary {
  background: #3b82f6;
}

.member-message {
  margin: 2px 0 0;
  font-size: 13px;
  color: #4c7d7d;
  font-weight: 700;
}

.save-message {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: #ff7b7b;
  text-align: center;
}

.save-button {
  width: 100%;
  padding: 20px;
  border: none;
  border-radius: 24px;
  background: #4cb7b7;
  box-shadow: 0 10px 20px rgba(76, 183, 183, 0.3);
  font-size: 20px;
  font-weight: 800;
  color: #ffffff;
  cursor: pointer;
  transition: all 0.2s;
  margin-top: 8px;
}

.save-button:active {
  transform: scale(0.98);
  box-shadow: 0 5px 10px rgba(76, 183, 183, 0.2);
}
</style>
