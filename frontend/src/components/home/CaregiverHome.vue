<script setup>
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useCaregiverData } from '@/composables/useCaregiverData';
import { useCaregiverSharingSettings } from '@/composables/useCaregiverSharingSettings';
import { useWeeklyTrend } from '@/composables/useWeeklyTrend';
import { buildTrendQueryForRange } from '@/composables/useTrendRange';
import { buildTrendBuckets } from '@/composables/useTrendBuckets';
import { buildCaregiverAlertBundle } from '@/composables/useCaregiverAlerts';

const router = useRouter();
const { data, loading, fetchData } = useCaregiverData();
const { trend, error: trendError, fetchWeeklyTrend } = useWeeklyTrend();
const sharing = useCaregiverSharingSettings();
const selectedRange = ref('7d');

const fetchTrend = async (subjectId) =>
  fetchWeeklyTrend(
    buildTrendQueryForRange(selectedRange.value, subjectId ? { subjectId: String(subjectId) } : {})
  );

onMounted(() => {
  (async () => {
    await fetchData();
    await fetchTrend(data.value?.subject?.id);
  })();
});

const statusTone = computed(() => {
  const label = data.value?.todayStatus?.label ?? '';
  if (label.includes('주의') || label.includes('관찰')) return 'alert';
  if (label.includes('위험') || label.includes('경고') || label.includes('심각')) return 'danger';
  return 'stable';
});

const statusColor = computed(() => {
  if (statusTone.value === 'danger') return '#ff8a80';
  if (statusTone.value === 'alert') return '#ffb74d';
  return '#4cb7b7';
});

const statusLabel = computed(() => data.value?.todayStatus?.label ?? '안정');
const subjectName = computed(() => data.value?.subject?.name ?? '대상자');
const relationText = computed(() => data.value?.subject?.relation ?? '가족');
const subjectWithRelation = computed(() =>
  relationText.value ? `${subjectName.value} ${relationText.value}` : subjectName.value
);

const statusSummary = computed(() => `${subjectName.value} 님은 오늘 ${statusLabel.value} 상태입니다.`);
const statusMessage = computed(() => {
  if (!sharing.dialogSummary.value) return '대상자가 대화 요약 공유를 철회했습니다.';
  return data.value?.todayStatus?.message ?? '오늘 대화 리듬이 안정적으로 유지되었습니다.';
});
const lastChatText = computed(() => data.value?.todayStatus?.lastChat ?? '오늘 기록 없음');

const bucketedTrend = computed(() => buildTrendBuckets(trend.value?.points ?? [], selectedRange.value));
const alertBundle = computed(() => buildCaregiverAlertBundle(selectedRange.value, bucketedTrend.value));

const toRelativeDateLabel = (isoDate) => {
  const target = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(target.getTime())) return isoDate;
  const today = new Date();
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const compare = new Date(target.getFullYear(), target.getMonth(), target.getDate());
  const diffDay = Math.round((base.getTime() - compare.getTime()) / (24 * 60 * 60 * 1000));
  if (diffDay === 0) return '오늘';
  if (diffDay === 1) return '어제';
  return `${target.getMonth() + 1}.${target.getDate()}`;
};

const watchAlerts = computed(() =>
  alertBundle.value.monitoringAlerts.slice(0, 2).map((alert) => ({
    ...alert,
    whenLabel: toRelativeDateLabel(alert.endDate),
  }))
);

const watchFallbackText = computed(() => {
  if (!sharing.dialogSummary.value) return '대화 요약 공유가 비활성화되어 변화를 확인할 수 없어요.';
  if (trendError.value) return '변화 데이터를 불러오지 못했어요. 잠시 후 다시 확인해주세요.';
  return '지금은 눈여겨볼 변화가 없습니다.';
});

const handleOpenHistory = () => {
  router.push({
    name: 'history',
    query: {
      tab: 'summary',
      period: selectedRange.value,
    },
  });
};

const handleOpenWatchAlert = (alert) => {
  router.push({
    name: 'history',
    query: {
      tab: 'monitoring',
      axis: alert.axis,
      period: selectedRange.value,
    },
  });
};

const statusIconPath = computed(() => {
  if (statusTone.value === 'danger') {
    return 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 13h-2v-2h2v2zm0-4h-2V6h2v5z';
  }
  if (statusTone.value === 'alert') {
    return 'M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z';
  }
  return 'M9 16.2l-3.5-3.5L4 14.2l5 5 11-11-1.5-1.5L9 16.2z';
});
</script>

<template>
  <div class="caregiver-home-container">
    <div v-if="loading" class="loading">
      <div class="spinner"></div>
    </div>

    <template v-else-if="data">
      <header class="page-header stagger" style="--delay: 0ms">
        <div class="page-header-copy">
          <p class="hero-caption">보호자 대시보드</p>
          <!-- <h2 class="hero-title">{{ subjectWithRelation }}</h2> -->
        </div>
        <div class="status-pill" :class="statusTone">
          <span class="status-dot"></span>
          <span>{{ statusLabel }}</span>
        </div>
      </header>

      <section class="hero-card stagger" style="--delay: 80ms">
        <div class="hero-body">
          <div class="status-visual">
            <svg viewBox="0 0 24 24" :style="{ color: statusColor }">
              <path :d="statusIconPath" fill="currentColor" />
            </svg>
          </div>
          <div class="hero-text">
            <p class="hero-message">{{ statusSummary }}</p>
            <p class="hero-detail">{{ statusMessage }}</p>
            <p class="hero-meta">
              <span class="hero-meta-label">마지막 대화</span>
              <span class="hero-meta-value">{{ lastChatText }}</span>
            </p>
          </div>
        </div>
      </section>

      <section class="watch-section stagger" style="--delay: 160ms">
        <h3 class="watch-section-title">눈여겨볼 변화</h3>

        <div v-if="watchAlerts.length" class="watch-list">
          <button
            v-for="alert in watchAlerts"
            :key="alert.id"
            type="button"
            class="watch-event-card"
            @click="handleOpenWatchAlert(alert)"
          >
            <div class="watch-event-head">
              <span class="watch-event-tag">{{ alert.tagLabel }}</span>
              <span class="watch-event-time">{{ alert.whenLabel }}</span>
            </div>
            <p class="watch-event-title">{{ alert.titleLine }}</p>
          </button>
        </div>

        <p v-else class="watch-empty">{{ watchFallbackText }}</p>
      </section>

      <button type="button" class="primary-cta stagger" style="--delay: 240ms" @click="handleOpenHistory">
        자세히 확인하기
      </button>
    </template>
  </div>
</template>

<style scoped>
.caregiver-home-container {
  --card-surface: #f7f9fa;
  --card-elevation-main:
    0 10px 22px rgba(126, 140, 154, 0.18),
    0 3px 8px rgba(126, 140, 154, 0.11),
    0 1px 3px rgba(126, 140, 154, 0.06);
  --card-elevation-sub:
    0 8px 16px rgba(126, 140, 154, 0.14),
    0 2px 6px rgba(126, 140, 154, 0.1);
  --card-elevation-icon:
    0 10px 18px rgba(126, 140, 154, 0.18),
    0 3px 8px rgba(126, 140, 154, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.62);
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
  padding-bottom: 8px;
}

.loading {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 4px solid #e0e5ec;
  border-top-color: #4cb7b7;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 0 4px;
}

.hero-caption {
  font-size: 18px;
  font-weight: 700;
  color: #4cb7b7;
  margin: 0 0 4px;
}

.hero-title {
  font-size: 44px;
  font-weight: 900;
  color: #2e2e2e;
  margin: 0;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 18px;
  border-radius: 999px;
  font-size: 22px;
  font-weight: 900;
  line-height: 1;
  background: #e6f3f3;
  color: #1f5f5f;
  white-space: nowrap;
  box-shadow: inset 2px 2px 4px rgba(0, 0, 0, 0.08), inset -2px -2px 4px rgba(255, 255, 255, 0.9);
}

.status-pill.stable {
  background: rgba(76, 183, 183, 0.15);
  color: #1f5f5f;
}

.status-pill.alert {
  background: rgba(255, 183, 77, 0.24);
  color: #c77715;
}

.status-pill.danger {
  background: rgba(255, 138, 128, 0.2);
  color: #d66a61;
}

.status-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: currentColor;
}

.hero-card {
  background: var(--card-surface);
  padding: 24px 20px;
  border-radius: 24px;
  box-shadow: var(--card-elevation-main);
}

.hero-body {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr);
  gap: 16px;
  align-items: center;
}

.status-visual {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  background: #f9fbfb;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--card-elevation-icon);
}

.status-visual svg {
  width: 30px;
  height: 30px;
}

.hero-message {
  font-size: 20px;
  font-weight: 900;
  margin: 0 0 8px;
  color: #2e2e2e;
}

.hero-detail {
  font-size: 16px;
  margin: 0 0 12px;
  color: #4d4d4d;
  line-height: 1.5;
}

.hero-meta {
  margin: 0;
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}

.hero-meta-label {
  font-size: 17px;
  font-weight: 700;
  color: #8a8a8a;
}

.hero-meta-value {
  font-size: 18px;
  font-weight: 800;
  color: #646b71;
}

.watch-section {
  background: var(--card-surface);
  padding: 20px;
  border-radius: 24px;
  box-shadow: var(--card-elevation-main);
  display: grid;
  gap: 12px;
}

.watch-section-title {
  margin: 0;
  font-size: 30px;
  font-weight: 900;
  color: #2e2e2e;
}

.watch-list {
  display: grid;
  gap: 10px;
}

.watch-event-card {
  width: 100%;
  border: none;
  border-radius: 18px;
  background: #f8fbfb;
  box-shadow: var(--card-elevation-sub);
  padding: 14px 16px;
  text-align: left;
  display: grid;
  gap: 8px;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.watch-event-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 18px rgba(126, 140, 154, 0.14), 0 2px 6px rgba(126, 140, 154, 0.1);
}

.watch-event-head {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.watch-event-tag,
.watch-event-time {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 13px;
  font-weight: 800;
}

.watch-event-tag {
  background: #eaf1f4;
  color: #56626b;
}

.watch-event-time {
  background: rgba(255, 183, 77, 0.2);
  color: #9a712f;
}

.watch-event-title {
  margin: 0;
  font-size: 20px;
  font-weight: 900;
  color: #253949;
  line-height: 1.45;
}

.watch-empty {
  margin: 0;
  font-size: 16px;
  color: #66737c;
  line-height: 1.5;
}

.primary-cta {
  border: none;
  border-radius: 18px;
  background: #4cb7b7;
  color: #ffffff;
  font-size: 18px;
  font-weight: 900;
  padding: 14px 18px;
  cursor: pointer;
  box-shadow: 0 8px 16px rgba(76, 183, 183, 0.3);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.primary-cta:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 18px rgba(76, 183, 183, 0.32);
}

.stagger {
  opacity: 0;
  transform: translateY(12px);
  animation: fadeUp 0.6s ease forwards;
  animation-delay: var(--delay, 0ms);
}

@keyframes fadeUp {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 680px) {
  .hero-title {
    font-size: 30px;
  }

  .status-pill {
    font-size: 18px;
    padding: 8px 14px;
  }

  .hero-body {
    grid-template-columns: 52px minmax(0, 1fr);
  }

  .watch-section-title {
    font-size: 26px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .stagger {
    animation: none !important;
    opacity: 1;
    transform: none;
  }
}
</style>
