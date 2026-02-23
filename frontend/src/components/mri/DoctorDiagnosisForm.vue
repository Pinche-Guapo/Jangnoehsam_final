<script setup lang="ts">
import { ref, computed, onBeforeUnmount } from 'vue';

interface DiagnosisOption {
  id: string;
  label: string;
  value: string;
}

interface StageOption {
  id: string;
  label: string;
  value: string;
}

interface DiagnosisSubmitData {
  patientId: string;
  stage: string;
  diagnoses: string[];
  additionalNotes: string;
  timestamp: string;
  doctorId: string;
}

const props = defineProps<{
  patientId: string;
  doctorId?: string;
}>();

const emit = defineEmits<{
  (e: 'submit', data: DiagnosisSubmitData): void;
}>();

const API_BASE_RAW = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const API_BASE = /\/api$/i.test(API_BASE_RAW) ? API_BASE_RAW : `${API_BASE_RAW}/api`;

// 진단 옵션 목록
const diagnosisOptions: DiagnosisOption[] = [
  { id: 'hippocampus', label: '해마 위축', value: 'hippocampus_atrophy' },
  { id: 'temporal', label: '측두엽 위축', value: 'temporal_lobe_atrophy' },
  { id: 'brain_volume', label: '전체 뇌 부피 감소', value: 'total_brain_volume_loss' },
  { id: 'white_matter', label: '백질 병변', value: 'white_matter_lesion' },
  { id: 'frontal', label: '전두엽 위축', value: 'frontal_lobe_atrophy' },
  { id: 'parietal', label: '두정엽 위축', value: 'parietal_lobe_atrophy' }
];

const stageOptions: StageOption[] = [
  { id: 'cn', label: 'CN', value: 'cn' },
  { id: 'smci', label: 'sMCI', value: 'smci' },
  { id: 'pmci', label: 'pMCI', value: 'pmci' },
  { id: 'ad', label: 'AD', value: 'ad' }
];

// 상태 관리
const selectedStage = ref('');
const selectedDiagnoses = ref<string[]>([]);
const additionalNotes = ref('');
const isSubmitting = ref(false);
const toastVisible = ref(false);
const toastMessage = ref('');
const toastType = ref<'success' | 'error'>('success');
let toastTimer: ReturnType<typeof setTimeout> | null = null;

const showToast = (message: string, type: 'success' | 'error' = 'success') => {
  toastMessage.value = message;
  toastType.value = type;
  toastVisible.value = true;
  if (toastTimer) {
    clearTimeout(toastTimer);
  }
  toastTimer = setTimeout(() => {
    toastVisible.value = false;
  }, 2200);
};

onBeforeUnmount(() => {
  if (toastTimer) {
    clearTimeout(toastTimer);
  }
});

// 유효성 검사
const isValid = computed(() => Boolean(selectedStage.value) && selectedDiagnoses.value.length > 0);

// 체크박스 토글
const handleCheckboxChange = (value: string) => {
  const index = selectedDiagnoses.value.indexOf(value);
  if (index === -1) {
    selectedDiagnoses.value.push(value);
  } else {
    selectedDiagnoses.value.splice(index, 1);
  }
};

// 체크 여부 확인
const isChecked = (value: string) => selectedDiagnoses.value.includes(value);
const isStageSelected = (value: string) => selectedStage.value === value;
const handleStageSelect = (value: string) => {
  selectedStage.value = selectedStage.value === value ? '' : value;
};

// 폼 초기화
const handleReset = () => {
  selectedStage.value = '';
  selectedDiagnoses.value = [];
  additionalNotes.value = '';
};

// 폼 제출
const handleSubmit = async (e: Event) => {
  e.preventDefault();

  if (!selectedStage.value) {
    showToast('CN / sMCI / pMCI / AD 중 하나를 선택해주세요.', 'error');
    return;
  }

  if (selectedDiagnoses.value.length === 0) {
    showToast('최소 하나 이상의 진단 항목을 선택해주세요.', 'error');
    return;
  }

  isSubmitting.value = true;

  try {
    const submitData: DiagnosisSubmitData = {
      patientId: props.patientId,
      stage: selectedStage.value,
      diagnoses: [...selectedDiagnoses.value],
      additionalNotes: additionalNotes.value.trim(),
      timestamp: new Date().toISOString(),
      doctorId: props.doctorId || 'doctor_001'
    };

    const response = await fetch(
      `${API_BASE}/doctor/patients/${encodeURIComponent(String(props.patientId))}/mri/doctor-diagnosis`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        credentials: 'include',
        body: JSON.stringify({
          diagnoses: submitData.diagnoses,
          stage: submitData.stage,
          additionalNotes: submitData.additionalNotes,
          timestamp: submitData.timestamp,
          doctorId: submitData.doctorId
        })
      }
    );

    if (!response.ok) {
      let detail = '진단 저장 중 오류가 발생했습니다.';
      try {
        const body = await response.json();
        if (typeof body?.detail === 'string' && body.detail.trim()) {
          detail = body.detail.trim();
        }
      } catch {
        // ignore parse error
      }
      throw new Error(`${detail} (HTTP ${response.status})`);
    }

    // 부모 컴포넌트에 이벤트 전달
    emit('submit', submitData);

    showToast('진단 결과가 저장되었습니다.', 'success');

    // 폼 초기화
    handleReset();
  } catch (error: unknown) {
    console.error('진단 저장 실패:', error);
    const message = error instanceof Error ? error.message : '진단 저장 중 오류가 발생했습니다.';
    showToast(message, 'error');
  } finally {
    isSubmitting.value = false;
  }
};
</script>

<template>
  <div class="diagnosis-form-container">
    <div class="form-header">
      <h4>의사 진단 확정</h4>
      <p class="form-description">
        AI 분석 결과를 참고하여 임상 단계 1개를 선택하고, 영상 소견을 체크해주세요.
      </p>
    </div>

    <form @submit="handleSubmit">
      <div class="stage-group">
        <p class="stage-title">임상 단계 분류 (필수)</p>
        <div class="stage-list" role="radiogroup" aria-label="임상 단계 분류">
          <button
            v-for="stage in stageOptions"
            :key="stage.id"
            type="button"
            class="stage-item"
            :class="{ selected: isStageSelected(stage.value) }"
            :aria-pressed="isStageSelected(stage.value)"
            @click="handleStageSelect(stage.value)"
          >
            {{ stage.label }}
          </button>
        </div>
      </div>

      <!-- 체크박스 목록 -->
      <div class="checkbox-list">
        <label
          v-for="option in diagnosisOptions"
          :key="option.id"
          class="checkbox-item"
          :class="{ checked: isChecked(option.value) }"
        >
          <input
            type="checkbox"
            :value="option.value"
            :checked="isChecked(option.value)"
            @change="handleCheckboxChange(option.value)"
          />
          <span class="checkbox-custom">
            <svg v-if="isChecked(option.value)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
          </span>
          <span class="checkbox-label">{{ option.label }}</span>
        </label>
      </div>

      <!-- 추가 소견 -->
      <div class="textarea-group">
        <label class="textarea-label">추가 소견 (선택사항)</label>
        <textarea
          v-model="additionalNotes"
          placeholder="진단에 대한 추가 소견이나 특이사항을 입력해주세요..."
          rows="4"
        ></textarea>
      </div>

      <!-- 버튼 영역 -->
      <div class="button-group">
        <button
          type="button"
          class="btn-reset"
          @click="handleReset"
          :disabled="isSubmitting"
        >
          초기화
        </button>
        <button
          type="submit"
          class="btn-submit"
          :disabled="!isValid || isSubmitting"
        >
          <span v-if="isSubmitting">
            <span class="btn-spinner"></span>
            저장 중...
          </span>
          <span v-else>진단 확정</span>
        </button>
      </div>
    </form>

    <transition name="toast-fade">
      <div
        v-if="toastVisible"
        class="form-toast"
        :class="`is-${toastType}`"
        role="status"
        aria-live="polite"
      >
        {{ toastMessage }}
      </div>
    </transition>
  </div>
</template>

<style scoped>
.diagnosis-form-container {
  background: #f5f6f7;
  border-radius: 26px;
  padding: 24px;
  box-shadow: 12px 12px 24px #d1d9e6, -12px -12px 24px #ffffff;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.form-header h4 {
  font-size: 20px;
  font-weight: 800;
  color: #2e2e2e;
  margin: 0 0 8px;
}

.form-description {
  font-size: 15px;
  font-weight: 600;
  color: #777;
  margin: 0;
  line-height: 1.5;
}

.stage-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-bottom: 10px;
}

.stage-title {
  margin: 0;
  font-size: 15px;
  font-weight: 800;
  color: #4b5563;
}

.stage-list {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.stage-item {
  border: 1px solid rgba(196, 205, 216, 0.85);
  background: #ffffff;
  border-radius: 14px;
  padding: 12px 8px;
  font-size: 15px;
  font-weight: 800;
  color: #5f6771;
  cursor: pointer;
  transition: all 0.2s ease;
}

.stage-item:hover {
  background: #fafbfc;
}

.stage-item.selected {
  border-color: rgba(76, 183, 183, 0.5);
  background: rgba(76, 183, 183, 0.12);
  color: #2e7d7d;
  box-shadow: inset 2px 2px 6px rgba(76, 183, 183, 0.08), inset -2px -2px 6px #ffffff;
}

/* 체크박스 목록 */
.checkbox-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.checkbox-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 20px;
  background: #ffffff;
  border-radius: 16px;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: inset 4px 4px 8px rgba(209, 217, 230, 0.5), inset -4px -4px 8px #ffffff;
}

.checkbox-item:hover {
  background: #fafbfc;
}

.checkbox-item.checked {
  background: rgba(76, 183, 183, 0.06);
  box-shadow: inset 4px 4px 8px rgba(76, 183, 183, 0.1), inset -4px -4px 8px #ffffff;
}

.checkbox-item input[type="checkbox"] {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.checkbox-custom {
  width: 24px;
  height: 24px;
  border: 2px solid #d1d5db;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all 0.2s ease;
  background: #ffffff;
}

.checkbox-item.checked .checkbox-custom {
  background: #4cb7b7;
  border-color: #4cb7b7;
}

.checkbox-custom svg {
  width: 14px;
  height: 14px;
  color: #ffffff;
}

.checkbox-label {
  font-size: 16px;
  font-weight: 700;
  color: #2e2e2e;
}

/* 텍스트 영역 */
.textarea-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.textarea-label {
  font-size: 16px;
  font-weight: 800;
  color: #555;
  padding-top: 8px;
}

.textarea-group textarea {
  width: 100%;
  padding: 16px;
  border: none;
  border-radius: 16px;
  background: #ffffff;
  box-shadow: inset 4px 4px 8px rgba(209, 217, 230, 0.6), inset -4px -4px 8px #ffffff;
  font-size: 16px;
  font-weight: 600;
  color: #2e2e2e;
  font-family: inherit;
  resize: vertical;
  min-height: 100px;
  transition: box-shadow 0.2s ease;
}

.textarea-group textarea::placeholder {
  color: #999;
}

.textarea-group textarea:focus {
  outline: none;
  box-shadow: inset 4px 4px 8px rgba(76, 183, 183, 0.15), inset -4px -4px 8px #ffffff, 0 0 0 3px rgba(76, 183, 183, 0.2);
}

/* 버튼 그룹 */
.button-group {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
}

.btn-reset,
.btn-submit {
  padding: 14px 28px;
  border-radius: 14px;
  font-size: 16px;
  font-weight: 800;
  cursor: pointer;
  transition: all 0.2s ease;
  min-height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.btn-reset {
  background: #ffffff;
  border: 2px solid #d1d5db;
  color: #555;
  box-shadow: 6px 6px 12px #d1d9e6, -6px -6px 12px #ffffff;
}

.btn-reset:hover:not(:disabled) {
  background: #f5f6f7;
  border-color: #b0b5bc;
}

.btn-reset:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-submit {
  background: #4cb7b7;
  border: none;
  color: #ffffff;
  box-shadow: 6px 6px 12px #d1d9e6, -6px -6px 12px #ffffff;
}

.btn-submit:hover:not(:disabled) {
  background: #3da5a5;
}

.btn-submit:disabled {
  background: #b0b5bc;
  cursor: not-allowed;
  box-shadow: none;
}

.btn-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #ffffff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.form-toast {
  position: fixed;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 2200;
  padding: 14px 20px;
  border-radius: 14px;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.01em;
  box-shadow: 0 16px 34px rgba(30, 41, 59, 0.24);
  border: 1px solid transparent;
  pointer-events: none;
  white-space: nowrap;
}

.form-toast.is-success {
  color: #1f7f7f;
  background: #e8f7f7;
  border-color: rgba(76, 183, 183, 0.45);
}

.form-toast.is-error {
  color: #9a3412;
  background: #fff5ec;
  border-color: rgba(251, 146, 60, 0.45);
}

.toast-fade-enter-active,
.toast-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.toast-fade-enter-from,
.toast-fade-leave-to {
  opacity: 0;
  transform: translate(-50%, calc(-50% + 8px));
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* 반응형 */
@media (max-width: 520px) {
  .stage-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .button-group {
    flex-direction: column;
  }

  .btn-reset,
  .btn-submit {
    width: 100%;
  }
}
</style>
