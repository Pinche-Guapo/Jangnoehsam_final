<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import MRIImageDisplay from '../mri/MRIImageDisplay.vue';
import RegionContributionChart from '../mri/RegionContributionChart.vue';
import DoctorDiagnosisForm from '../mri/DoctorDiagnosisForm.vue';
import EmptyState from '../common/EmptyState.vue';
import AlertBanner from '../common/AlertBanner.vue';

// 임상 추세 데이터 타입 정의
interface ClinicalTrends {
  adasCog13?: number[];
  faq?: number[];
  labels?: string[];
  mmse?: number[];
  moca?: number[];
}

interface VisitRecord {
  examDate: string;
  viscode2?: string | null;
  imageId?: string | null;
  mriAssessmentId?: string | null;
}

interface VisitOption extends VisitRecord {
  optionKey: string;
  optionValue: string;
}

interface BiomarkerResults {
  abeta42?: number;
  abeta40?: number;
  ptau?: number;
  tau?: number;
  ratioAb42Ab40?: number;
  ratioPtauAb42?: number;
  ratioPtauTau?: number;
  ratioTtauAb42?: number;
}

interface VoiceAssessmentPoint {
  timepoint?: string | null;
  cognitiveScore?: number | null;
  mciProbability?: number | null;
  flag?: string | null;
}

type BiomarkerItem = {
  id: string;
  label: string;
  value: number | null;
  unit: string;
  range: string;
  isRatio: boolean;
  referenceType?: 'gte' | 'lte' | 'between' | 'unknown';
  referenceMin?: number | null;
  referenceMax?: number | null;
};

type BiomarkerRatioDisplayItem = BiomarkerItem & {
  hasReference: boolean;
  referenceStartPercent: number;
  referenceWidthPercent: number;
  markerPercent: number;
  markerColor: string;
  statusKey: 'good' | 'warn' | 'bad' | 'empty';
  scaleMinLabel: string;
  scaleMaxLabel: string;
};

type AttentionMapItem = {
  plane: 'axial' | 'coronal' | 'sagittal';
  label: string;
  url: string;
};

type AttentionSlideItem = {
  rank: number;
  roi?: string;
  description?: string;
  score?: number | null;
  percentage?: number | null;
  views: AttentionMapItem[];
};

type DataAvailability = {
  hasCognitiveTests: boolean;
  hasMMSE: boolean;
  hasMoCA: boolean;
  hasADAS: boolean;
  hasFAQ: boolean;
  hasBiomarkers: boolean;
  hasMRI: boolean;
  mriCount: number;
  hasVoiceUploads: boolean;
  hasVoiceData: boolean;
  voiceDataStatus: 'none' | 'processing' | 'ready';
  voiceDataDays: number;
  dataCompleteness: 'none' | 'minimal' | 'partial' | 'complete';
  lastUpdateDate: string | null;
  daysSinceLastUpdate: number;
};

type UserType = 'prevention' | 'suspected' | 'confirmed' | 'inactive';

type EmptyStateType = 'info' | 'warning' | 'success' | 'no-data' | 'partial-data' | 'not-applicable';

interface EmptyStateContent {
  type: EmptyStateType;
  icon?: string;
  title: string;
  description: string;
  benefits?: string[];
  note?: string;
  actionText?: string;
}

interface ScoreCardConfig {
  id: string;
  label: string;
  value: number | null;
  unit: string;
  max: number;
  direction: 'higher-better' | 'higher-worse';
  goodThreshold: number;
  warnThreshold: number;
  referenceRange: string;
  referenceMin: number;
  referenceMax: number;
  rangeLabel?: string;
  digits?: number;
}

const STALE_THRESHOLD_DAYS = 120;
const VOICE_CHART_WIDTH = 640;
const VOICE_CHART_HEIGHT = 200;
const VOICE_CHART_MAX_TICK_LABELS = 7;

const ensureArray = <T,>(value: T[] | null | undefined) => (Array.isArray(value) ? value : []);

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

const toDateOnly = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());

const getYesterday = () => {
  const date = toDateOnly(new Date());
  date.setDate(date.getDate() - 1);
  return date;
};

const parseMonthDayLabel = (label: string, pivotDate: Date) => {
  const matched = label.match(/^(\d{1,2})[-/.](\d{1,2})$/);
  if (!matched) return null;
  const month = Number(matched[1]);
  const day = Number(matched[2]);
  if (!Number.isFinite(month) || !Number.isFinite(day)) return null;
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;

  let year = pivotDate.getFullYear();
  let candidate = new Date(year, month - 1, day);
  if (candidate.getMonth() !== month - 1 || candidate.getDate() !== day) return null;

  if (candidate.getTime() > pivotDate.getTime()) {
    year -= 1;
    candidate = new Date(year, month - 1, day);
    if (candidate.getMonth() !== month - 1 || candidate.getDate() !== day) return null;
  }
  return candidate;
};

const parseLabelToDate = (label: string, pivotDate: Date) => {
  const normalized = String(label ?? '').trim();
  if (!normalized) return null;

  const ymdMatched = normalized.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
  if (ymdMatched) {
    const year = Number(ymdMatched[1]);
    const month = Number(ymdMatched[2]);
    const day = Number(ymdMatched[3]);
    const candidate = new Date(year, month - 1, day);
    if (candidate.getFullYear() === year && candidate.getMonth() === month - 1 && candidate.getDate() === day) {
      return candidate;
    }
    return null;
  }

  return parseMonthDayLabel(normalized, pivotDate);
};

const formatScoreValue = (value: number | null, digits = 0) => {
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  if (digits > 0) return value.toFixed(digits);
  return String(Math.round(value));
};

const formatBiomarkerValue = (value: number | null, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  return value.toFixed(digits);
};

const formatRatioScaleValue = (value: number) => {
  if (!Number.isFinite(value)) return '-';
  if (value >= 10) return String(Math.round(value));
  if (value >= 1) return value.toFixed(1).replace(/\.0$/, '');
  return value.toFixed(3).replace(/\.?0+$/, '');
};

const VOICE_METRIC_DEFAULT_RANGE: Record<string, { min: number; max: number }> = {
  utterance: { min: 0, max: 12 },
  length: { min: 0, max: 8 },
  pause: { min: 0, max: 1 },
  participation: { min: 0, max: 6 }
};

const smoothSeries = (values: number[]) => {
  if (!values.length) return [];
  if (values.length < 3) return [...values];
  return values.map((value, index) => {
    const prev = values[index - 1] ?? value;
    const next = values[index + 1] ?? value;
    return (prev + value + next) / 3;
  });
};

const normalizeVoiceSeries = (metricId: string, values: number[]) => {
  if (!values || values.length < 2) return [];
  const smoothed = smoothSeries(values);
  const defaultRange = VOICE_METRIC_DEFAULT_RANGE[metricId] ?? { min: 0, max: 1 };
  const observedMax = Math.max(...smoothed);
  const rangeMin = defaultRange.min;
  const rangeMax = Math.max(defaultRange.max, observedMax * 1.1);
  const span = Math.max(rangeMax - rangeMin, 1e-6);

  return smoothed.map((value) => {
    const normalized = clamp((value - rangeMin) / span, 0, 1);
    // Prevent tiny numeric fluctuations from looking overly sharp on small screens.
    return 0.5 + (normalized - 0.5) * 0.7;
  });
};

const getNormalizedPath = (values: number[], width = VOICE_CHART_WIDTH, height = VOICE_CHART_HEIGHT) => {
  if (!values || values.length < 2) return '';
  const padding = 18;
  const points = values.map((value, i) => {
    const x = padding + (i / (values.length - 1)) * (width - padding * 2);
    const clamped = Math.min(1, Math.max(0, value));
    const y = height - padding - clamped * (height - padding * 2);
    return `${x},${y}`;
  });
  return `M ${points.join(' L ')}`;
};

const getTrendDirection = (metricId: string, values: number[]) => {
  if (!values || values.length < 2) return 'stable';
  const normalized = normalizeVoiceSeries(metricId, values);
  if (normalized.length < 2) return 'stable';
  const diff = normalized.at(-1)! - normalized.at(-2)!;
  if (Math.abs(diff) < 0.05) return 'stable';
  return diff > 0 ? 'increase' : 'decrease';
};

const getLatestDate = (dates: Array<string | null | undefined>) => {
  let latest: string | null = null;
  let latestTime = 0;

  dates.forEach((dateString) => {
    if (!dateString) return;
    const time = new Date(dateString).getTime();
    if (Number.isNaN(time)) return;
    if (time >= latestTime) {
      latestTime = time;
      latest = dateString;
    }
  });

  return latest;
};

const calculateDaysSince = (dateString: string | null) => {
  if (!dateString) return 0;
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return 0;
  const today = new Date();
  const normalizedToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const normalizedDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const diff = normalizedToday - normalizedDate;
  return diff > 0 ? Math.floor(diff / (1000 * 60 * 60 * 24)) : 0;
};

const formatDateOnly = (dateString: string | null | undefined) => {
  if (!dateString) return '-';
  const raw = String(dateString).trim();
  if (!raw) return '-';
  const match = raw.match(/^(\d{4}-\d{2}-\d{2})/);
  if (match) return match[1];
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
};

const asFiniteNumber = (value: unknown) => {
  if (value === null || value === undefined) return null;
  const normalized =
    typeof value === 'string' && value.trim() === '' ? Number.NaN : Number(value);
  return Number.isFinite(normalized) ? normalized : null;
};

const formatReferenceRange = (min: number, max: number, digits = 0) => {
  const format = (value: number) => (digits > 0 ? value.toFixed(digits) : String(Math.round(value)));
  return `${format(min)}-${format(max)}`;
};

type ScoreCriteria = {
  goodThreshold: number;
  warnThreshold: number;
  referenceRange: string;
  referenceMin: number;
  referenceMax: number;
};

const resolveScoreCriteria = (
  scoreId: 'mmse' | 'moca' | 'adas' | 'faq' | 'ldeltotal' | 'cdrsb',
  age: number | null,
  educationYears: number | null
): ScoreCriteria => {
  const resolvedAge = age ?? 70;
  const resolvedEducation = educationYears ?? 12;

  const ageAdjustment = resolvedAge >= 80 ? 2 : resolvedAge >= 75 ? 1 : resolvedAge <= 64 ? -1 : 0;
  const educationAdjustment = resolvedEducation >= 16 ? -1 : resolvedEducation <= 9 ? 1 : 0;
  const demographicAdjustment = ageAdjustment + educationAdjustment;

  if (scoreId === 'mmse') {
    const min = clamp(Math.round(27 - demographicAdjustment), 20, 30);
    const max = 30;
    const goodThreshold = min;
    const warnThreshold = clamp(min - 6, 10, goodThreshold);
    return {
      goodThreshold,
      warnThreshold,
      referenceRange: formatReferenceRange(min, max),
      referenceMin: min,
      referenceMax: max
    };
  }

  if (scoreId === 'moca') {
    const min = clamp(Math.round(26 - demographicAdjustment), 16, 30);
    const max = 30;
    const goodThreshold = min;
    const warnThreshold = clamp(min - 7, 8, goodThreshold);
    return {
      goodThreshold,
      warnThreshold,
      referenceRange: formatReferenceRange(min, max),
      referenceMin: min,
      referenceMax: max
    };
  }

  if (scoreId === 'adas') {
    const min = 0;
    const max = clamp(Math.round(10 + demographicAdjustment), 4, 24);
    const goodThreshold = max;
    const warnThreshold = clamp(max + 10, goodThreshold, 85);
    return {
      goodThreshold,
      warnThreshold,
      referenceRange: formatReferenceRange(min, max),
      referenceMin: min,
      referenceMax: max
    };
  }

  if (scoreId === 'ldeltotal') {
    // ADNI Logical Memory II(Delayed Recall) 교육연수별 기준(최대 25점)
    const min = resolvedEducation >= 16 ? 9 : resolvedEducation >= 8 ? 5 : 3;
    const max = 25;
    const goodThreshold = min;
    const warnThreshold = clamp(min - 2, 0, goodThreshold);
    return {
      goodThreshold,
      warnThreshold,
      referenceRange: formatReferenceRange(min, max),
      referenceMin: min,
      referenceMax: max
    };
  }

  if (scoreId === 'cdrsb') {
    // CDR-SB 해석 가이드(0, 0.5-4, 4.5-9, 9.5-15.5, 16-18) 기반
    // 참고범위(0-4)와 해석 기준을 동일하게 맞춤
    const min = 0;
    const max = 4;
    const goodThreshold = 4;
    const warnThreshold = 9;
    return {
      goodThreshold,
      warnThreshold,
      referenceRange: formatReferenceRange(min, max, 1),
      referenceMin: min,
      referenceMax: max
    };
  }

  const min = 0;
  const max = clamp(Math.round(9 + demographicAdjustment), 3, 20);
  const goodThreshold = max;
  const warnThreshold = clamp(max + 10, goodThreshold, 30);
  return {
    goodThreshold,
    warnThreshold,
    referenceRange: formatReferenceRange(min, max),
    referenceMin: min,
    referenceMax: max
  };
};

const makeScoreCard = (config: ScoreCardConfig) => {
  const {
    id,
    label,
    value,
    unit,
    max,
    direction,
    goodThreshold,
    warnThreshold,
    referenceRange,
    referenceMin,
    referenceMax,
    rangeLabel = '참고 범위',
    digits = 0
  } = config;

  const hasValue = value !== null && value !== undefined;
  let status: 'good' | 'warn' | 'bad' | 'empty' = 'empty';
  if (hasValue) {
    if (direction === 'higher-better') {
      if (value >= goodThreshold) status = 'good';
      else if (value >= warnThreshold) status = 'warn';
      else status = 'bad';
    } else {
      if (value <= goodThreshold) status = 'good';
      else if (value <= warnThreshold) status = 'warn';
      else status = 'bad';
    }
  }

  const interpretationMap = {
    good: '양호',
    warn: '주의 필요',
    bad: '추가 평가 권장',
    empty: '측정 없음'
  };

  const percent = hasValue ? Math.round((clamp(value, 0, max) / max) * 100) : 0;
  const lowerReference = Math.min(referenceMin, referenceMax);
  const upperReference = Math.max(referenceMin, referenceMax);
  const inReferenceRange = hasValue ? value >= lowerReference && value <= upperReference : false;
  const referenceStartPercent = max > 0 ? (clamp(Math.min(referenceMin, referenceMax), 0, max) / max) * 100 : 0;
  const referenceEndPercent = max > 0 ? (clamp(Math.max(referenceMin, referenceMax), 0, max) / max) * 100 : 0;
  const referenceWidthPercent = Math.max(referenceEndPercent - referenceStartPercent, 0);
  const activeColor = !hasValue
    ? '#b0b8c2'
    : inReferenceRange
    ? '#198f63'
    : status === 'warn'
    ? '#f59e0b'
    : '#ef4444';
  const levelKey = !hasValue ? '측정 없음' : percent >= 80 ? '높음' : percent >= 40 ? '중간' : '낮음';
  const levelText = !hasValue
    ? '측정 없음'
    : `${levelKey} (${formatScoreValue(value, digits)}/${max}점, ${percent}%)`;

  return {
    id,
    label,
    value,
    unit,
    displayValue: formatScoreValue(value, digits),
    max,
    percent,
    status,
    interpretation: interpretationMap[status],
    referenceRange,
    referenceStartPercent,
    referenceWidthPercent,
    inReferenceRange,
    activeColor,
    rangeLabel,
    levelText
  };
};

const props = defineProps({
  currentTab: {
    type: String,
    default: 'clinical'
  },
  data: {
    type: Object,
    default: null
  },
  loading: {
    type: Boolean,
    default: false
  }
});

const isLoading = computed(() => props.loading || !props.data);
const currentPatient = computed(() => props.data?.currentPatient || null);
const visits = computed<VisitRecord[]>(
  () => (props.data?.visits as VisitRecord[] | undefined) ?? []
);
const sortedVisits = computed(() => {
  if (!visits.value.length) return [];
  return [...visits.value].sort((a, b) => new Date(a.examDate).getTime() - new Date(b.examDate).getTime());
});

const visitOptions = computed<VisitOption[]>(() =>
  sortedVisits.value.map((visit, index) => {
    const imageId = String(visit?.imageId ?? '').trim();
    const fallbackBase = String(visit?.examDate || visit?.viscode2 || `visit-${index}`).trim();
    const fallbackValue = `${fallbackBase}-${index}`;

    return {
      ...visit,
      optionKey: imageId || fallbackValue,
      optionValue: imageId || fallbackValue
    };
  })
);

const selectedVisitImageId = ref('');
const selectedVisitOption = computed<VisitOption | null>(() => {
  if (!visitOptions.value.length) return null;
  const selected = visitOptions.value.find((visit) => visit.optionValue === selectedVisitImageId.value);
  return selected ?? visitOptions.value.at(-1) ?? null;
});

const clinicalTrends = computed<ClinicalTrends | null>(() =>
  props.data?.clinicalTrends ?? null
);
const dailyParticipation = computed(() => props.data?.dailyParticipation || null);
const acousticFeatures = computed(() => props.data?.acousticFeatures || []);
const voiceAssessments = computed<VoiceAssessmentPoint[]>(
  () => ensureArray((props.data?.voiceAssessments as VoiceAssessmentPoint[] | undefined) ?? [])
);
const voiceChartDailyParticipation = computed(() => {
  const base = dailyParticipation.value || {};
  const labels = ensureArray(base.labels).map((label) => String(label));
  const counts = ensureArray(base.counts).map((count) => Number(count));
  if (!labels.length) {
    return {
      labels,
      counts
    };
  }

  const cutoffDate = getYesterday();
  const parsedDates = labels.map((label) => parseLabelToDate(label, cutoffDate));
  if (parsedDates.some((date) => date === null)) {
    return {
      labels,
      counts
    };
  }

  let lastVisibleIndex = -1;
  parsedDates.forEach((date, index) => {
    if (date && date.getTime() <= cutoffDate.getTime()) {
      lastVisibleIndex = index;
    }
  });

  if (lastVisibleIndex < 0) {
    return {
      labels,
      counts
    };
  }

  return {
    labels: labels.slice(0, lastVisibleIndex + 1),
    counts: counts.slice(0, lastVisibleIndex + 1)
  };
});
const mriAnalysis = computed(() => props.data?.mriAnalysis || null);

const aiAnalysis = computed(() => mriAnalysis.value?.aiAnalysis || null);
const selectedVisitImageIdParam = computed(() => {
  const raw = String(selectedVisitOption.value?.imageId ?? '').trim();
  if (!raw) return '';
  return /^\d+$/.test(raw) ? raw : '';
});

const selectedVisitAssessmentIdParam = computed(() => {
  const raw = String(selectedVisitOption.value?.mriAssessmentId ?? '').trim();
  if (!raw) return '';
  return raw;
});

const selectedVisitExamDateParam = computed(() => {
  const raw = String(selectedVisitOption.value?.examDate ?? '').trim();
  if (!raw) return '';
  const match = raw.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : '';
});

const selectedVisitViscode2Param = computed(() => {
  const raw = String(selectedVisitOption.value?.viscode2 ?? '').trim();
  return raw;
});

const upsertQueryParam = (url: string, key: string, value: string) => {
  const raw = String(url || '').trim();
  if (!raw || !key || !value) return raw;
  const encodedValue = encodeURIComponent(value);
  const matcher = new RegExp(`([?&]${key}=)[^&]*`, 'i');
  if (matcher.test(raw)) {
    return raw.replace(matcher, `$1${encodedValue}`);
  }
  return `${raw}${raw.includes('?') ? '&' : '?'}${key}=${encodedValue}`;
};

const appendSelectedVisitContext = (url: string) => {
  const raw = String(url || '').trim();
  if (!raw) return '';
  let next = raw;
  if (selectedVisitImageIdParam.value) {
    next = upsertQueryParam(next, 'image_id', selectedVisitImageIdParam.value);
  }
  if (selectedVisitAssessmentIdParam.value) {
    next = upsertQueryParam(next, 'mri_assessment_id', selectedVisitAssessmentIdParam.value);
  }
  if (selectedVisitExamDateParam.value) {
    next = upsertQueryParam(next, 'visit_exam_date', selectedVisitExamDateParam.value);
  }
  if (selectedVisitViscode2Param.value) {
    next = upsertQueryParam(next, 'visit_viscode2', selectedVisitViscode2Param.value);
  }
  return next;
};

const buildMriSliceEndpoint = (
  sliceType: 'original-slice' | 'preprocessed-slice',
  plane?: AttentionMapItem['plane']
) => {
  const patientId =
    currentPatient.value?.id ??
    props.data?.currentPatient?.id ??
    props.data?.currentPatientId ??
    props.data?.patientId;

  if (patientId === null || patientId === undefined || patientId === '') {
    return '';
  }

  const rvCandidate =
    mriAnalysis.value?.scanDate ??
    aiAnalysis.value?.updatedAt ??
    aiAnalysis.value?.generatedAt ??
    props.data?.lastUpdateDate ??
    '';
  const query = new URLSearchParams();
  if (plane) query.set('plane', plane);
  if (rvCandidate) query.set('rv', String(rvCandidate));
  const queryString = query.toString();
  return `/api/doctor/patients/${encodeURIComponent(String(patientId))}/mri/${sliceType}.png${queryString ? `?${queryString}` : ''}`;
};

const originalImage = computed(() => {
  const provided = String(aiAnalysis.value?.originalImage || '').trim();
  const endpoint = buildMriSliceEndpoint('original-slice', 'axial');

  if (provided.includes('/api/doctor/patients/')) return provided;

  return endpoint || provided;
});

const attentionMap = computed(() => {
  const provided = String(aiAnalysis.value?.attentionMap || '').trim();
  const endpoint = buildMriSliceEndpoint('preprocessed-slice');

  if (provided.includes('/api/doctor/patients/')) return provided;

  return endpoint || provided;
});

const normalizeAttentionPlane = (value: unknown): AttentionMapItem['plane'] => {
  const text = String(value || '').trim().toLowerCase();
  if (text.startsWith('cor')) return 'coronal';
  if (text.startsWith('sag')) return 'sagittal';
  return 'axial';
};

const isNotebookAttentionLayout = computed(() => {
  const method = String(aiAnalysis.value?.xai?.method || '').trim().toLowerCase();
  return method === 'gradcam3d_notebook';
});

const resolveAttentionStoragePlane = (plane: AttentionMapItem['plane']): AttentionMapItem['plane'] => {
  // Notebook CAM outputs were generated with axial/sagittal swapped.
  if (isNotebookAttentionLayout.value) {
    if (plane === 'axial') return 'sagittal';
    if (plane === 'sagittal') return 'axial';
  }
  return plane;
};

const rewriteAttentionPlaneQuery = (url: string, requestedPlane: AttentionMapItem['plane']) => {
  const raw = String(url || '').trim();
  if (!raw) return raw;

  const storagePlane = resolveAttentionStoragePlane(requestedPlane);
  if (storagePlane === requestedPlane) return raw;

  if (/[?&]plane=/i.test(raw)) {
    return raw.replace(/([?&]plane=)(axial|coronal|sagittal)/i, `$1${storagePlane}`);
  }

  return `${raw}${raw.includes('?') ? '&' : '?'}plane=${storagePlane}`;
};

const labelByAttentionPlane: Record<AttentionMapItem['plane'], string> = {
  axial: 'Axial',
  coronal: 'Coronal',
  sagittal: 'Sagittal'
};

const orderByAttentionPlane: Record<AttentionMapItem['plane'], number> = {
  axial: 0,
  coronal: 1,
  sagittal: 2
};

const appendCacheVersion = (url: string) => {
  const raw = String(url || '').trim();
  if (!raw) return '';
  const version = String(aiAnalysis.value?.generatedAt || mriAnalysis.value?.scanDate || '').trim();
  if (!version) return raw;
  return `${raw}${raw.includes('?') ? '&' : '?'}v=${encodeURIComponent(version)}`;
};

const parseAttentionMapItems = (
  maps: Array<any>,
  options: { rewritePlaneQuery?: boolean } = {}
): AttentionMapItem[] => {
  const parsed = maps
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const plane = normalizeAttentionPlane(item.plane);
      const baseUrl = String(item.url || item.path || item.image || '').trim();
      const resolvedUrl = options.rewritePlaneQuery
        ? rewriteAttentionPlaneQuery(baseUrl, plane)
        : baseUrl;
      const url = appendCacheVersion(resolvedUrl);
      if (!url) return null;
      const label = labelByAttentionPlane[plane];
      return { plane, label, url } as AttentionMapItem;
    })
    .filter((item): item is AttentionMapItem => Boolean(item));

  const uniqueByPlane = new Map<AttentionMapItem['plane'], AttentionMapItem>();
  parsed.forEach((item) => {
    if (!uniqueByPlane.has(item.plane)) {
      uniqueByPlane.set(item.plane, item);
    }
  });

  return Array.from(uniqueByPlane.values()).sort(
    (a, b) => orderByAttentionPlane[a.plane] - orderByAttentionPlane[b.plane]
  );
};

const originalMaps = computed<AttentionMapItem[]>(() => {
  const endpointMaps = [
    { plane: 'axial', label: 'Axial', url: buildMriSliceEndpoint('original-slice', 'axial') },
    { plane: 'coronal', label: 'Coronal', url: buildMriSliceEndpoint('original-slice', 'coronal') },
    { plane: 'sagittal', label: 'Sagittal', url: buildMriSliceEndpoint('original-slice', 'sagittal') }
  ].filter((item): item is AttentionMapItem => Boolean(String(item.url || '').trim()));

  if (endpointMaps.length > 0) {
    return endpointMaps;
  }

  const maps = ensureArray(aiAnalysis.value?.originalMaps as Array<any> | undefined);
  if (maps.length > 0) {
    return parseAttentionMapItems(maps);
  }

  if (originalImage.value) {
    return [
      { plane: 'axial', label: 'Axial', url: originalImage.value },
      { plane: 'coronal', label: 'Coronal', url: originalImage.value },
      { plane: 'sagittal', label: 'Sagittal', url: originalImage.value }
    ];
  }

  return [];
});

const attentionMaps = computed<AttentionMapItem[]>(() => {
  const maps = ensureArray(aiAnalysis.value?.attentionMaps as Array<any> | undefined);
  if (maps.length > 0) {
    return parseAttentionMapItems(maps, { rewritePlaneQuery: true });
  }

  if (attentionMap.value) {
    return [{ plane: 'axial', label: 'Axial', url: attentionMap.value }];
  }

  return [];
});

const attentionSlides = computed<AttentionSlideItem[]>(() => {
  const slides = ensureArray(aiAnalysis.value?.attentionSlides as Array<any> | undefined);
  const parsedSlides = slides
    .map((slide, index) => {
      if (!slide || typeof slide !== 'object') return null;
      const views = parseAttentionMapItems(
        ensureArray(slide.views as Array<any> | undefined),
        { rewritePlaneQuery: true }
      );
      if (views.length === 0) return null;
      return {
        rank: Number(slide.rank) || index + 1,
        roi: typeof slide.roi === 'string' ? slide.roi : undefined,
        description: typeof slide.description === 'string' ? slide.description : undefined,
        score: Number.isFinite(Number(slide.score)) ? Number(slide.score) : null,
        percentage: Number.isFinite(Number(slide.percentage)) ? Number(slide.percentage) : null,
        views
      } as AttentionSlideItem;
    })
    .filter((item): item is AttentionSlideItem => Boolean(item));

  if (parsedSlides.length > 0) {
    return parsedSlides.sort((a, b) => a.rank - b.rank);
  }

  if (attentionMaps.value.length > 0) {
    return [
      {
        rank: 1,
        views: attentionMaps.value
      }
    ];
  }

  return [];
});
const regionContributions = computed(() => aiAnalysis.value?.regionContributions || []);

const patientSummary = computed(() => {
  const patientId = currentPatient.value?.id;
  if (!patientId) return null;
  return props.data?.patients?.find((patient: any) => patient.id === patientId) || null;
});

const resolveEducationYearsFrom = (source: any) =>
  asFiniteNumber(source?.pteducat) ??
  asFiniteNumber(source?.PTEDUCAT) ??
  asFiniteNumber(source?.education_years) ??
  asFiniteNumber(source?.educationYears) ??
  asFiniteNumber(source?.education) ??
  asFiniteNumber(source?.demographics?.pteducat) ??
  asFiniteNumber(source?.demographics?.PTEDUCAT) ??
  asFiniteNumber(source?.demographics?.education_years) ??
  asFiniteNumber(source?.demographics?.educationYears) ??
  asFiniteNumber(source?.demographics?.education);

const patientAge = computed(() =>
  asFiniteNumber(patientSummary.value?.age) ??
  asFiniteNumber(currentPatient.value?.age) ??
  asFiniteNumber(props.data?.age)
);

const patientEducationYears = computed(() =>
  resolveEducationYearsFrom(patientSummary.value) ??
  resolveEducationYearsFrom(currentPatient.value) ??
  resolveEducationYearsFrom(props.data)
);

const dataAvailability = computed<DataAvailability>(() => {
  const base = (props.data?.dataAvailability ?? {}) as Partial<DataAvailability>;
  const clinical = clinicalTrends.value ?? {};
  const hasADAS = base.hasADAS ?? ensureArray(clinical.adasCog13).length > 0;
  const hasFAQ = base.hasFAQ ?? ensureArray(clinical.faq).length > 0;
  const hasMMSE =
    base.hasMMSE ??
    (ensureArray(clinical.mmse).length > 0 || patientSummary.value?.mmse != null);
  const hasMoCA =
    base.hasMoCA ??
    (ensureArray(clinical.moca).length > 0 || patientSummary.value?.moca != null);
  const hasCognitiveTests = base.hasCognitiveTests ?? (hasADAS || hasFAQ || hasMMSE || hasMoCA);

  const biomarkerSource = props.data?.biomarkerResults ?? props.data?.biomarkers ?? {};
  const hasBiomarkers =
    base.hasBiomarkers ??
    Object.values(biomarkerSource).some((value) => value !== null && value !== undefined);

  const participationCounts = ensureArray(dailyParticipation.value?.counts);
  const hasVoiceUploads = base.hasVoiceUploads ?? (participationCounts.length > 0);
  const hasVoiceData = base.hasVoiceData ?? (acousticFeatures.value.length > 0);
  const voiceDataStatus =
    (base.voiceDataStatus as DataAvailability['voiceDataStatus'] | undefined) ??
    (hasVoiceData ? 'ready' : hasVoiceUploads ? 'processing' : 'none');

  const reportCount = mriAnalysis.value?.reports ? Object.keys(mriAnalysis.value.reports).length : 0;
  const visitImageCount = ensureArray(visits.value).filter((visit) => visit?.imageId).length;
  const hasMriPayload = Boolean(
    mriAnalysis.value?.scanDate ||
      mriAnalysis.value?.classification ||
      mriAnalysis.value?.aiAnalysis
  );
  const inferredMriCount = hasMriPayload || base.hasMRI ? 1 : 0;
  const mriCount = base.mriCount ?? Math.max(reportCount, visitImageCount, inferredMriCount);
  const hasMRI = base.hasMRI ?? (mriCount > 0 || hasMriPayload);

  const voiceDataDays = base.voiceDataDays ?? (participationCounts.length ? participationCounts.length * 7 : 0);

  const lastUpdateDate =
    base.lastUpdateDate ??
    mriAnalysis.value?.scanDate ??
    getLatestDate([
      ...ensureArray(visits.value).map((visit) => visit?.examDate),
      currentPatient.value?.examDate,
      currentPatient.value?.lastVisit
    ]);

  const daysSinceLastUpdate = calculateDaysSince(lastUpdateDate ?? null);
  const hasFullCognitive = hasMMSE && hasMoCA && hasADAS && hasFAQ;

  let dataCompleteness: DataAvailability['dataCompleteness'] = 'none';
  if (!hasCognitiveTests && !hasVoiceData && !hasMRI) {
    dataCompleteness = 'none';
  } else if (hasVoiceData && !hasCognitiveTests && !hasMRI) {
    dataCompleteness = 'minimal';
  } else if (hasCognitiveTests && hasVoiceData && hasMRI && hasFullCognitive) {
    dataCompleteness = 'complete';
  } else {
    dataCompleteness = 'partial';
  }

  return {
    hasCognitiveTests,
    hasMMSE,
    hasMoCA,
    hasADAS,
    hasFAQ,
    hasBiomarkers,
    hasMRI,
    mriCount,
    hasVoiceUploads,
    hasVoiceData,
    voiceDataStatus,
    voiceDataDays,
    dataCompleteness,
    lastUpdateDate: lastUpdateDate ?? null,
    daysSinceLastUpdate
  };
});

const mriSliceSliderEnabled = computed(() => dataAvailability.value.hasMRI);
const mriSyncEnabled = ref(true);

const toggleMriSync = () => {
  mriSyncEnabled.value = !mriSyncEnabled.value;
};

const hasClinicalData = computed(() => dataAvailability.value.hasCognitiveTests);
const hasVoiceData = computed(() => dataAvailability.value.hasVoiceData);
const hasMRIData = computed(() => dataAvailability.value.hasMRI);

const showClinicalEmpty = computed(() => !hasClinicalData.value);
const showVoicePending = computed(
  () => !hasVoiceData.value && dataAvailability.value.voiceDataStatus === 'processing'
);
const showVoiceEmpty = computed(() => !hasVoiceData.value);
const showMriEmpty = computed(() => !hasMRIData.value);

const isDataStale = computed(() => dataAvailability.value.daysSinceLastUpdate >= STALE_THRESHOLD_DAYS);
const dataStaleMessage = computed(() => {
  if (!isDataStale.value) return '';
  const lastDate = dataAvailability.value.lastUpdateDate;
  const days = dataAvailability.value.daysSinceLastUpdate;
  if (lastDate) {
    return `마지막 업데이트가 ${lastDate} (${days}일 전)입니다. 최신 검사 및 음성 데이터를 업데이트해 주세요.`;
  }
  return '최근 데이터가 업데이트되지 않았습니다. 최신 검사 및 음성 데이터를 업데이트해 주세요.';
});

const userType = computed<UserType>(() => {
  if (props.data?.userType) return props.data.userType as UserType;
  if (isDataStale.value) return 'inactive';
  switch (dataAvailability.value.dataCompleteness) {
    case 'minimal':
      return 'prevention';
    case 'partial':
      return 'suspected';
    case 'complete':
      return 'confirmed';
    default:
      return 'prevention';
  }
});

const emptyStateContent = computed<Record<'clinical' | 'voice' | 'mri', EmptyStateContent>>(() => {
  const staleNote = dataStaleMessage.value || undefined;

  switch (userType.value) {
    case 'prevention':
      return {
        clinical: {
          type: 'no-data',
          icon: 'clipboard',
          title: '인지 검사 데이터가 없습니다',
          description: '예방형 환자는 음성 데이터 중심으로 모니터링합니다.',
          benefits: ['필요 시 MMSE · MoCA 검사를 추가해 주세요.']
        },
        voice: {
          type: 'warning',
          icon: 'microphone',
          title: '음성 데이터가 없습니다',
          description: '예방형 리포트는 음성 데이터가 필요합니다.',
          benefits: ['일상 대화 참여 데이터가 누적되면 추세를 확인할 수 있습니다.']
        },
        mri: {
          type: 'not-applicable',
          icon: 'brain',
          title: 'MRI는 선택 항목입니다',
          description: '현재 MRI 촬영 기록이 없습니다.',
          note: '필요 시 촬영을 진행해 주세요.'
        }
      };
    case 'suspected':
      return {
        clinical: {
          type: 'partial-data',
          icon: 'chart',
          title: '인지 검사 데이터가 일부만 있습니다',
          description: 'MMSE · MoCA 중심으로 확인할 수 있습니다.',
          benefits: ['ADAS-cog13 및 FAQ를 추가하면 더 정확한 판단이 가능합니다.']
        },
        voice: {
          type: 'no-data',
          icon: 'microphone',
          title: '음성 데이터가 없습니다',
          description: '음성·대화 데이터가 있어야 변화 추이를 확인할 수 있습니다.'
        },
        mri: {
          type: 'no-data',
          icon: 'brain',
          title: 'MRI 데이터가 없습니다',
          description: 'MRI 촬영 후 분석 결과가 표시됩니다.'
        }
      };
    case 'inactive':
      return {
        clinical: {
          type: 'warning',
          icon: 'alert',
          title: '인지 검사 기록이 오래되었습니다',
          description: '최근 인지 검사 데이터가 없습니다.',
          note: staleNote
        },
        voice: {
          type: 'warning',
          icon: 'alert',
          title: '음성 데이터가 오래되었습니다',
          description: '최근 음성 데이터가 없습니다.',
          note: staleNote
        },
        mri: {
          type: 'warning',
          icon: 'alert',
          title: 'MRI 데이터가 오래되었습니다',
          description: '최근 MRI 촬영 기록이 없습니다.',
          note: staleNote
        }
      };
    default:
      return {
        clinical: {
          type: 'no-data',
          icon: 'clipboard',
          title: '인지 검사 데이터가 없습니다',
          description: '등록된 인지 검사 결과가 없습니다.'
        },
        voice: {
          type: 'no-data',
          icon: 'microphone',
          title: '음성 데이터가 없습니다',
          description: '등록된 음성 데이터가 없습니다.'
        },
        mri: {
          type: 'no-data',
          icon: 'brain',
          title: 'MRI 데이터가 없습니다',
          description: '촬영 기록이 없습니다.'
        }
      };
  }
});

const clinicalEmptyState = computed(() => emptyStateContent.value.clinical);
const voiceEmptyState = computed(() => emptyStateContent.value.voice);
const mriEmptyState = computed(() => emptyStateContent.value.mri);

const adasSeries = computed(() => clinicalTrends.value?.adasCog13 || []);
const faqSeries = computed(() => clinicalTrends.value?.faq || []);

const mmseValue = computed(() => {
  const series = ensureArray(clinicalTrends.value?.mmse);
  if (series.length) return series.at(-1) ?? null;
  return patientSummary.value?.mmse ?? null;
});

const mocaValue = computed(() => {
  const series = ensureArray(clinicalTrends.value?.moca);
  if (series.length) return series.at(-1) ?? null;
  return patientSummary.value?.moca ?? null;
});

const adasValue = computed(() => adasSeries.value.at(-1) ?? null);
const faqValue = computed(() => faqSeries.value.at(-1) ?? null);
const ldelTotalIndicatorValue = computed(
  () =>
    asFiniteNumber(patientSummary.value?.ldelTotal) ??
    asFiniteNumber(patientSummary.value?.ldeltotal) ??
    asFiniteNumber(currentPatient.value?.ldelTotal) ??
    asFiniteNumber(currentPatient.value?.ldeltotal)
);
const cdrSbIndicatorValue = computed(
  () =>
    asFiniteNumber(patientSummary.value?.cdrSB) ??
    asFiniteNumber(patientSummary.value?.cdr_sb) ??
    asFiniteNumber(currentPatient.value?.cdrSB) ??
    asFiniteNumber(currentPatient.value?.cdr_sb)
);
const nxauditoIndicatorValue = computed(
  () =>
    asFiniteNumber(patientSummary.value?.nxaudito) ??
    asFiniteNumber(currentPatient.value?.nxaudito)
);

const cognitiveScoreCards = computed(() => {
  const rangeLabel = '참고 범위';
  const mmseCriteria = resolveScoreCriteria('mmse', patientAge.value, patientEducationYears.value);
  const mocaCriteria = resolveScoreCriteria('moca', patientAge.value, patientEducationYears.value);
  const adasCriteria = resolveScoreCriteria('adas', patientAge.value, patientEducationYears.value);
  const ldelCriteria = resolveScoreCriteria('ldeltotal', patientAge.value, patientEducationYears.value);

  return [
    makeScoreCard({
      id: 'mmse',
      label: 'MMSE',
      value: mmseValue.value,
      unit: '점',
      max: 30,
      direction: 'higher-better',
      goodThreshold: mmseCriteria.goodThreshold,
      warnThreshold: mmseCriteria.warnThreshold,
      referenceRange: mmseCriteria.referenceRange,
      referenceMin: mmseCriteria.referenceMin,
      referenceMax: mmseCriteria.referenceMax,
      rangeLabel
    }),
    makeScoreCard({
      id: 'moca',
      label: 'MoCA',
      value: mocaValue.value,
      unit: '점',
      max: 30,
      direction: 'higher-better',
      goodThreshold: mocaCriteria.goodThreshold,
      warnThreshold: mocaCriteria.warnThreshold,
      referenceRange: mocaCriteria.referenceRange,
      referenceMin: mocaCriteria.referenceMin,
      referenceMax: mocaCriteria.referenceMax,
      rangeLabel
    }),
    makeScoreCard({
      id: 'adas',
      label: 'ADAS-cog13',
      value: adasValue.value,
      unit: '점',
      max: 85,
      direction: 'higher-worse',
      goodThreshold: adasCriteria.goodThreshold,
      warnThreshold: adasCriteria.warnThreshold,
      referenceRange: adasCriteria.referenceRange,
      referenceMin: adasCriteria.referenceMin,
      referenceMax: adasCriteria.referenceMax,
      rangeLabel,
      digits: 1
    }),
    makeScoreCard({
      id: 'ldeltotal',
      label: 'LDELTOTAL',
      value: ldelTotalIndicatorValue.value,
      unit: '점',
      max: 25,
      direction: 'higher-better',
      goodThreshold: ldelCriteria.goodThreshold,
      warnThreshold: ldelCriteria.warnThreshold,
      referenceRange: ldelCriteria.referenceRange,
      referenceMin: ldelCriteria.referenceMin,
      referenceMax: ldelCriteria.referenceMax,
      rangeLabel
    })
  ];
});

const functionalClinicalScoreCards = computed(() => {
  const rangeLabel = '참고 범위';
  const faqCriteria = resolveScoreCriteria('faq', patientAge.value, patientEducationYears.value);
  const cdrCriteria = resolveScoreCriteria('cdrsb', patientAge.value, patientEducationYears.value);
  return [
    makeScoreCard({
      id: 'faq',
      label: 'FAQ',
      value: faqValue.value,
      unit: '점',
      max: 30,
      direction: 'higher-worse',
      goodThreshold: faqCriteria.goodThreshold,
      warnThreshold: faqCriteria.warnThreshold,
      referenceRange: faqCriteria.referenceRange,
      referenceMin: faqCriteria.referenceMin,
      referenceMax: faqCriteria.referenceMax,
      rangeLabel
    }),
    makeScoreCard({
      id: 'cdr-sb',
      label: 'CDR-SB',
      value: cdrSbIndicatorValue.value,
      unit: '점',
      max: 18,
      direction: 'higher-worse',
      goodThreshold: cdrCriteria.goodThreshold,
      warnThreshold: cdrCriteria.warnThreshold,
      referenceRange: cdrCriteria.referenceRange,
      referenceMin: cdrCriteria.referenceMin,
      referenceMax: cdrCriteria.referenceMax,
      rangeLabel,
      digits: 1
    })
  ];
});

const behavioralClinicalIndicators = computed(() => {
  const value = nxauditoIndicatorValue.value;
  const statusLabel = value === 1 ? '정상' : value === 2 ? '이상' : '-';
  return [
    {
      id: 'nxaudito',
      label: 'NXAUDITO',
      displayValue: statusLabel,
      unit: '',
      status: value === 2 ? 'abnormal' : 'normal'
    }
  ];
});

const apoe4Biomarker = computed(() => {
  const value =
    asFiniteNumber(patientSummary.value?.apoe4) ??
    asFiniteNumber(currentPatient.value?.apoe4) ??
    asFiniteNumber(props.data?.apoe4);
  const normalizedCount = value === null || value === undefined ? null : Math.round(value);
  return {
    label: 'APOE4',
    value: normalizedCount,
    unit: '',
    displayValue: normalizedCount === null ? '-' : String(normalizedCount),
    hasValue: normalizedCount !== null
  };
});

const biomarkerResults = computed<BiomarkerResults | null>(() =>
  props.data?.biomarkerResults ?? props.data?.biomarkers ?? null
);

const biomarkerPlatformText = computed(() => '검사 플랫폼: Roche Elecsys (CSF)');

const biomarkerItems = computed<BiomarkerItem[]>(() => {
  const base = biomarkerResults.value || {};
  const abeta42 = base.abeta42 ?? null;
  const abeta40 = base.abeta40 ?? null;
  const ptau = base.ptau ?? null;
  const tau = base.tau ?? null;
  const ratioAb42Ab40 = base.ratioAb42Ab40 ?? (abeta42 && abeta40 ? abeta42 / abeta40 : null);
  const ratioPtauAb42 = base.ratioPtauAb42 ?? (ptau && abeta42 ? ptau / abeta42 : null);
  const ratioTtauAb42 = base.ratioTtauAb42 ?? base.ratioPtauTau ?? (tau && abeta42 ? tau / abeta42 : null);

  const platformKey = String(props.data?.biomarkerMeta?.platform || '').toUpperCase();
  const isElecsys = platformKey.includes('ELECSYS');
  const ab42Ab40Range = isElecsys ? '참고 범위: ≥ 0.057' : '참고 범위: 플랫폼 기준 확인 필요';
  const ptauAb42Range = isElecsys ? '참고 범위: ≤ 0.023' : '참고 범위: 플랫폼 기준 확인 필요';
  const ttauAb42Range = isElecsys ? '참고 범위: ≤ 0.28' : '참고 범위: 플랫폼 기준 확인 필요';

  return [
    {
      id: 'abeta42',
      label: 'ABETA42',
      value: abeta42,
      unit: 'pg/mL',
      range: '',
      isRatio: false
    },
    {
      id: 'abeta40',
      label: 'ABETA40',
      value: abeta40,
      unit: 'pg/mL',
      range: '',
      isRatio: false
    },
    {
      id: 'ptau',
      label: 'PTAU',
      value: ptau,
      unit: 'pg/mL',
      range: '',
      isRatio: false
    },
    {
      id: 'tau',
      label: 'TAU',
      value: tau,
      unit: 'pg/mL',
      range: '',
      isRatio: false
    },
    {
      id: 'ratio-ab42-ab40',
      label: 'AB42/40',
      value: ratioAb42Ab40,
      unit: '',
      range: ab42Ab40Range,
      isRatio: true,
      referenceType: isElecsys ? 'gte' : 'unknown',
      referenceMin: isElecsys ? 0.057 : null,
      referenceMax: null
    },
    {
      id: 'ratio-ptau-ab42',
      label: 'PTAU/AB42',
      value: ratioPtauAb42,
      unit: '',
      range: ptauAb42Range,
      isRatio: true,
      referenceType: isElecsys ? 'lte' : 'unknown',
      referenceMin: null,
      referenceMax: isElecsys ? 0.023 : null
    },
    {
      id: 'ratio-ttau-ab42',
      label: 'TTAU/AB42',
      value: ratioTtauAb42,
      unit: '',
      range: ttauAb42Range,
      isRatio: true,
      referenceType: isElecsys ? 'lte' : 'unknown',
      referenceMin: null,
      referenceMax: isElecsys ? 0.28 : null
    }
  ];
});

const biomarkerRatioItems = computed(() => biomarkerItems.value.filter((item) => item.isRatio));
const biomarkerRawItems = computed(() => biomarkerItems.value.filter((item) => !item.isRatio));
const biomarkerRatioDisplayItems = computed<BiomarkerRatioDisplayItem[]>(() =>
  biomarkerRatioItems.value.map((item) => {
    const hasValue = item.value !== null && item.value !== undefined && !Number.isNaN(item.value);
    const referenceType = item.referenceType ?? 'unknown';
    const referenceMin = item.referenceMin ?? null;
    const referenceMax = item.referenceMax ?? null;
    const hasReference = referenceType !== 'unknown' && (referenceMin !== null || referenceMax !== null);

    const scaleCandidates = [0.1];
    if (referenceMin !== null) scaleCandidates.push(referenceMin * 1.8);
    if (referenceMax !== null) scaleCandidates.push(referenceMax * 1.8);
    if (hasValue) scaleCandidates.push((item.value as number) * 1.2);
    const scaleMax = Math.max(...scaleCandidates.filter((value) => Number.isFinite(value) && value > 0));

    const toPercent = (value: number) => (scaleMax > 0 ? (clamp(value, 0, scaleMax) / scaleMax) * 100 : 0);

    let referenceStartPercent = 0;
    let referenceEndPercent = 0;
    if (referenceType === 'gte' && referenceMin !== null) {
      referenceStartPercent = toPercent(referenceMin);
      referenceEndPercent = 100;
    } else if (referenceType === 'lte' && referenceMax !== null) {
      referenceStartPercent = 0;
      referenceEndPercent = toPercent(referenceMax);
    } else if (referenceType === 'between' && referenceMin !== null && referenceMax !== null) {
      referenceStartPercent = toPercent(Math.min(referenceMin, referenceMax));
      referenceEndPercent = toPercent(Math.max(referenceMin, referenceMax));
    }
    const referenceWidthPercent = Math.max(referenceEndPercent - referenceStartPercent, 0);

    let inReference = false;
    if (hasValue) {
      if (referenceType === 'gte' && referenceMin !== null) inReference = (item.value as number) >= referenceMin;
      else if (referenceType === 'lte' && referenceMax !== null) inReference = (item.value as number) <= referenceMax;
      else if (referenceType === 'between' && referenceMin !== null && referenceMax !== null) {
        inReference = (item.value as number) >= Math.min(referenceMin, referenceMax) && (item.value as number) <= Math.max(referenceMin, referenceMax);
      } else {
        inReference = true;
      }
    }

    let markerColor = '#b0b8c2';
    let statusKey: 'good' | 'warn' | 'bad' | 'empty' = 'empty';
    if (hasValue) {
      if (!hasReference || inReference) {
        markerColor = '#198f63';
        statusKey = 'good';
      } else {
        let severeOutlier = false;
        if (referenceType === 'gte' && referenceMin !== null) severeOutlier = (item.value as number) < referenceMin * 0.8;
        if (referenceType === 'lte' && referenceMax !== null) severeOutlier = (item.value as number) > referenceMax * 1.2;
        if (referenceType === 'between' && referenceMin !== null && referenceMax !== null) {
          const low = Math.min(referenceMin, referenceMax);
          const high = Math.max(referenceMin, referenceMax);
          severeOutlier = (item.value as number) < low * 0.8 || (item.value as number) > high * 1.2;
        }
        markerColor = severeOutlier ? '#ef4444' : '#f59e0b';
        statusKey = severeOutlier ? 'bad' : 'warn';
      }
    }

    return {
      ...item,
      hasReference,
      referenceStartPercent,
      referenceWidthPercent,
      markerPercent: hasValue ? toPercent(item.value as number) : 0,
      markerColor,
      statusKey,
      scaleMinLabel: '0',
      scaleMaxLabel: formatRatioScaleValue(scaleMax)
    };
  })
);

const hasBiomarkerData = computed(() =>
  biomarkerItems.value.some((item) => item.value !== null && item.value !== undefined) || apoe4Biomarker.value.hasValue
);

const findFeature = (keywords: string[]) =>
  acousticFeatures.value.find((feature: any) =>
    keywords.some((keyword) => String(feature?.label ?? '').includes(keyword))
  );

const latestVoiceAssessment = computed<VoiceAssessmentPoint | null>(() => {
  const direct = props.data?.latestVoiceAssessment as VoiceAssessmentPoint | undefined;
  if (direct && (direct.cognitiveScore != null || direct.mciProbability != null || direct.flag)) {
    return direct;
  }
  if (!voiceAssessments.value.length) return null;
  return voiceAssessments.value.at(-1) ?? null;
});

const latestVoiceFlagKey = computed(() => {
  const raw = String(latestVoiceAssessment.value?.flag || '').trim().toLowerCase();
  if (raw === 'critical') return 'critical';
  if (raw === 'warning') return 'warning';
  if (raw === 'normal') return 'normal';
  return 'unknown';
});

const latestVoiceFlagLabel = computed(() => {
  if (latestVoiceFlagKey.value === 'critical') return 'CRITICAL';
  if (latestVoiceFlagKey.value === 'warning') return 'WARNING';
  if (latestVoiceFlagKey.value === 'normal') return 'NORMAL';
  return 'UNKNOWN';
});

const latestVoiceProbabilityText = computed(() => {
  const value = asFiniteNumber(latestVoiceAssessment.value?.mciProbability);
  if (value === null) return '-';
  const percent = value <= 1 ? value * 100 : value;
  return `${clamp(percent, 0, 100).toFixed(1)}%`;
});

const latestVoiceScoreText = computed(() => {
  const value = asFiniteNumber(latestVoiceAssessment.value?.cognitiveScore);
  if (value === null) return '-';
  return value.toFixed(2);
});

const latestVoiceAssessedAtText = computed(() =>
  formatDateOnly(latestVoiceAssessment.value?.timepoint ?? null)
);

const buildSeries = (values: number[] | undefined) =>
  ensureArray(values)
    .map((value) => Number(value))
    .filter((value) => !Number.isNaN(value));

const voiceMetrics = computed(() => {
  const utteranceFeature = findFeature(['발화', '속도', '빈도']);
  const lengthFeature = findFeature(['길이', 'length', '발화 길이']) || findFeature(['강도']);
  const pauseFeature = findFeature(['멈춤', 'pause']);
  const timelineLength = voiceChartDailyParticipation.value.labels.length;
  const clipByTimeline = (values: number[]) => (timelineLength ? values.slice(0, timelineLength) : values);

  const metrics = [
    {
      id: 'utterance',
      label: '발화 빈도',
      color: '#4cb7b7',
      values: clipByTimeline(buildSeries(utteranceFeature?.trend))
    },
    {
      id: 'length',
      label: '평균 발화 길이',
      color: '#8bc34a',
      values: clipByTimeline(buildSeries(lengthFeature?.trend))
    },
    {
      id: 'pause',
      label: 'Pause 비율',
      color: '#ffb74d',
      values: clipByTimeline(buildSeries(pauseFeature?.trend))
    },
    {
      id: 'participation',
      label: '대화 참여 빈도',
      color: '#ff8a80',
      values: buildSeries(voiceChartDailyParticipation.value.counts)
    }
  ];

  return metrics.map((metric) => ({
    ...metric,
    hasData: metric.values.length > 1
  }));
});

const normalizedVoiceMetrics = computed(() =>
  voiceMetrics.value.map((metric) => ({
    ...metric,
    normalized: normalizeVoiceSeries(metric.id, metric.values)
  }))
);

const activeVoiceMetricIds = ref<string[]>([]);

const activeVoiceMetrics = computed(() =>
  normalizedVoiceMetrics.value.filter(
    (metric) => activeVoiceMetricIds.value.includes(metric.id) && metric.normalized.length > 1
  )
);

const voiceTimelineLabels = computed(() => {
  const labels = voiceChartDailyParticipation.value.labels;
  if (labels?.length) return labels;
  const maxLength = Math.max(0, ...normalizedVoiceMetrics.value.map((metric) => metric.normalized.length));
  return Array.from({ length: maxLength }, (_, index) => `T${index + 1}`);
});

const voiceTimelineTickLabels = computed(() => {
  const labels = voiceTimelineLabels.value;
  if (labels.length <= VOICE_CHART_MAX_TICK_LABELS) return labels;

  const step = Math.ceil((labels.length - 1) / (VOICE_CHART_MAX_TICK_LABELS - 1));
  return labels.map((label, index) => {
    const isLast = index === labels.length - 1;
    return index % step === 0 || isLast ? label : '';
  });
});

const voiceSummary = computed(() => {
  if (!activeVoiceMetrics.value.length) {
    return {
      statusKey: 'empty',
      statusLabel: '선택 필요',
      detail: '표시할 지표를 선택해 주세요.'
    };
  }

  const directions = activeVoiceMetrics.value.map((metric) => getTrendDirection(metric.id, metric.values));
  const hasIncrease = directions.includes('increase');
  const hasDecrease = directions.includes('decrease');

  if (hasIncrease && hasDecrease) {
    return {
      statusKey: 'mixed',
      statusLabel: '혼합',
      detail: '이전 대비 혼합 변화 경향입니다.'
    };
  }

  if (hasIncrease) {
    return {
      statusKey: 'increase',
      statusLabel: '증가',
      detail: '이전 대비 증가 경향입니다.'
    };
  }

  if (hasDecrease) {
    return {
      statusKey: 'decrease',
      statusLabel: '감소',
      detail: '이전 대비 감소 경향입니다.'
    };
  }

  return {
    statusKey: 'stable',
    statusLabel: '안정',
    detail: '이전 대비 안정적인 흐름입니다.'
  };
});

const mriScanDate = computed(() => formatDateOnly(mriAnalysis.value?.scanDate || dataAvailability.value.lastUpdateDate));
const mriScanCount = computed(() => (dataAvailability.value.mriCount ? `${dataAvailability.value.mriCount}건` : '-'));
const mriClassificationRaw = computed(() => String(mriAnalysis.value?.classification || '').trim());
const normalizeMriToken = (value: unknown) => String(value || '').toUpperCase().replace(/[\s_-]/g, '');
const mriClassificationToken = computed(() => normalizeMriToken(mriClassificationRaw.value));
const mriXaiTargetToken = computed(() => normalizeMriToken(aiAnalysis.value?.xai?.targetLabel));

const resolveMriMajorClass = (token: string) => {
  if (token === 'CN') return 'CN';
  if (token === 'AD') return 'AD';
  if (token === 'MCI' || token === 'SMCI' || token === 'PMCI' || token === 'EMCI' || token === 'LMCI') {
    return 'MCI';
  }
  return '';
};

const resolveMciSubtypeClass = (token: string) => {
  if (token === 'SMCI' || token === 'EMCI') return 'sMCI';
  if (token === 'PMCI' || token === 'LMCI') return 'pMCI';
  return '';
};

const mriMajorDisplay = computed(() => {
  const fromXai = resolveMriMajorClass(mriXaiTargetToken.value);
  if (fromXai) return fromXai;
  const fromClassification = resolveMriMajorClass(mriClassificationToken.value);
  if (fromClassification) return fromClassification;
  return '-';
});

const mriSubtypeDisplay = computed(() => {
  const subtype = resolveMciSubtypeClass(mriClassificationToken.value);
  if (subtype) return subtype;
  if (mriMajorDisplay.value === 'MCI') return '-';
  return '-';
});

const mriMajorBadgeKey = computed(() => {
  const label = mriMajorDisplay.value;
  if (label === 'CN') return 'cn';
  if (label === 'AD') return 'ad';
  if (label === 'MCI') return 'mci';
  return 'unknown';
});

const mriSubtypeBadgeKey = computed(() => {
  const label = mriSubtypeDisplay.value;
  if (label === 'sMCI') return 'smci';
  if (label === 'pMCI') return 'pmci';
  return 'unknown';
});

const showMciSubtypeRow = computed(() => mriMajorDisplay.value === 'MCI' || mriSubtypeDisplay.value !== '-');

const mriClassificationConfidenceLabel = computed(() => {
  const confidence = asFiniteNumber(mriAnalysis.value?.confidence);
  if (confidence === null) return '';
  const percent = confidence <= 1 ? confidence * 100 : confidence;
  const rounded = Math.round(clamp(percent, 0, 100));
  if (mriSubtypeDisplay.value !== '-') return `세부분류 신뢰도: ${rounded}%`;
  if (mriMajorDisplay.value !== '-') return `MRI 분류 신뢰도: ${rounded}%`;
  return '';
});

const handleDiagnosisSubmit = (data: any) => {
  console.log('진단 데이터 제출:', data);
};

watch(
  () => visitOptions.value,
  (value) => {
    if (!value?.length) {
      selectedVisitImageId.value = '';
      return;
    }
    if (
      selectedVisitImageId.value &&
      value.some((visit) => visit.optionValue === selectedVisitImageId.value)
    ) {
      return;
    }
    selectedVisitImageId.value = value.at(-1)?.optionValue || '';
  },
  { immediate: true }
);

watch(
  () => normalizedVoiceMetrics.value,
  (metrics) => {
    const available = metrics.filter((metric) => metric.hasData).map((metric) => metric.id);
    if (!available.length) {
      activeVoiceMetricIds.value = [];
      return;
    }
    const current = activeVoiceMetricIds.value.filter((id) => available.includes(id));
    if (current.length) {
      activeVoiceMetricIds.value = current;
      return;
    }
    activeVoiceMetricIds.value = available.slice(0, 2);
  },
  { immediate: true }
);
</script>
<template>
  <div class="doctor-history">
    <div v-if="isLoading" class="loading">
      <div class="skeleton-line"></div>
      <div class="skeleton-card"></div>
      <div class="skeleton-card"></div>
    </div>

    <transition v-else name="fade" mode="out-in">
      <div :key="currentTab" class="tab-panel">
        <section class="patient-banner" aria-label="환자 기본 정보">
          <div>
            <h3>{{ currentPatient?.name }}</h3>
            <p>{{ currentPatient?.id }}</p>
          </div>
          <span class="patient-meta">{{ currentPatient?.age }}세 · {{ currentPatient?.gender === 'F' ? '여' : '남' }}</span>
        </section>

        <AlertBanner
          v-if="isDataStale"
          class="data-alert"
          type="warning"
          :message="dataStaleMessage"
          :dismissible="true"
          :action-required="true"
          aria-label="데이터 신선도 경고"
        />

        <section v-if="currentTab === 'clinical'" class="panel clinical-panel" aria-label="인지 검사">
          <EmptyState
            v-if="showClinicalEmpty"
            class="empty-state-slot"
            :type="clinicalEmptyState.type"
            :icon="clinicalEmptyState.icon"
            :title="clinicalEmptyState.title"
            :description="clinicalEmptyState.description"
            :benefits="clinicalEmptyState.benefits"
            :note="clinicalEmptyState.note"
            :action-text="clinicalEmptyState.actionText"
            aria-label="인지 검사 데이터 비어 있음"
          />

          <template v-else>
            <div class="section-main-header">
              <h4>최근 측정 결과</h4>
              <div class="section-subheader section-subheader--stack">
                <span class="section-chip">현재 인지 상태</span>
                <p>연령과 교육연수를 반영한 참고 기준으로 현재 인지 상태를 확인합니다.</p>
              </div>
            </div>

            <div class="score-grid">
              <article
                v-for="card in cognitiveScoreCards"
                :key="card.id"
                class="score-card"
                :class="`score-${card.status}`"
              >
                <div class="score-header">
                  <span class="score-label">{{ card.label }}</span>
                  <div class="score-value">
                    <strong>{{ card.displayValue }}</strong>
                    <span v-if="card.displayValue !== '-'" class="score-unit">{{ card.unit }}</span>
                  </div>
                </div>
                <p class="score-sub">최근 측정 결과</p>
                <div class="score-bar" aria-hidden="true">
                  <div class="score-bar__track"></div>
                  <div
                    class="score-bar__reference"
                    :style="{ left: `${card.referenceStartPercent}%`, width: `${card.referenceWidthPercent}%` }"
                  ></div>
                  <div
                    class="score-bar__marker"
                    :style="{ '--marker-percent': `${card.percent}%`, '--marker-color': card.activeColor }"
                  ></div>
                </div>
                <div class="score-scale">
                  <span>0</span>
                  <span>{{ card.max }}</span>
                </div>
                <div class="score-meta">
                  <span class="score-range">{{ card.rangeLabel }}: {{ card.referenceRange }}</span>
                  <span class="score-interpretation">점수 기반 참고 해석: {{ card.interpretation }}</span>
                </div>
              </article>
            </div>

            <div class="clinical-group-section">
              <div class="section-subheader">
                <span class="section-chip section-chip--sub">기능/임상 단계</span>
              </div>

              <div class="score-grid">
                <article
                  v-for="card in functionalClinicalScoreCards"
                  :key="card.id"
                  class="score-card"
                  :class="`score-${card.status}`"
                >
                  <div class="score-header">
                    <span class="score-label">{{ card.label }}</span>
                    <div class="score-value">
                      <strong>{{ card.displayValue }}</strong>
                      <span v-if="card.displayValue !== '-'" class="score-unit">{{ card.unit }}</span>
                    </div>
                  </div>
                  <p class="score-sub">최근 측정 결과</p>
                  <div class="score-bar" aria-hidden="true">
                    <div class="score-bar__track"></div>
                    <div
                      class="score-bar__reference"
                      :style="{ left: `${card.referenceStartPercent}%`, width: `${card.referenceWidthPercent}%` }"
                    ></div>
                    <div
                      class="score-bar__marker"
                      :style="{ '--marker-percent': `${card.percent}%`, '--marker-color': card.activeColor }"
                    ></div>
                  </div>
                  <div class="score-scale">
                    <span>0</span>
                    <span>{{ card.max }}</span>
                  </div>
                  <div class="score-meta">
                    <span class="score-range">{{ card.rangeLabel }}: {{ card.referenceRange }}</span>
                    <span class="score-interpretation">점수 기반 참고 해석: {{ card.interpretation }}</span>
                  </div>
                </article>
              </div>

            </div>

            <div class="clinical-group-section">
              <div class="section-subheader">
                <span class="section-chip section-chip--sub">추가 임상(행동·정신증상)</span>
              </div>
              <div class="clinical-extra-grid">
                <div
                  v-for="item in behavioralClinicalIndicators"
                  :key="item.id"
                  class="clinical-extra-item"
                  :class="{ 'clinical-extra-item--warn': item.status === 'abnormal' }"
                >
                  <span class="clinical-extra-label">{{ item.label }}</span>
                  <strong class="clinical-extra-value">
                    {{ item.displayValue }}
                    <span v-if="item.displayValue !== '-' && item.unit" class="clinical-extra-unit">{{ item.unit }}</span>
                  </strong>
                </div>
              </div>
            </div>

            <div class="biomarker-section">
              <div class="section-subheader section-subheader--stack section-subheader--biomarker">
                <span class="section-chip section-chip--sub">바이오마커 결과</span>
                <p>체액/유전 바이오마커 지표를 정적 정보로 요약합니다.</p>
              </div>

              <div v-if="hasBiomarkerData" class="biomarker-content">
                <div class="biomarker-grid biomarker-grid--genetic">
                  <div class="biomarker-item biomarker-item--genetic">
                    <span class="biomarker-label">{{ apoe4Biomarker.label }}</span>
                    <strong class="biomarker-value">
                      {{ apoe4Biomarker.displayValue }}
                      <span v-if="apoe4Biomarker.displayValue !== '-'" class="biomarker-unit">{{ apoe4Biomarker.unit }}</span>
                    </strong>
                  </div>
                </div>

                <div class="biomarker-grid biomarker-grid--ratio">
                  <div
                    v-for="item in biomarkerRatioDisplayItems"
                    :key="item.id"
                    class="biomarker-item"
                    :class="`biomarker-item--${item.statusKey}`"
                  >
                    <span class="biomarker-label">{{ item.label }}</span>
                    <strong class="biomarker-value">
                      {{ formatBiomarkerValue(item.value, item.unit ? 1 : 2) }}
                      <span v-if="item.unit" class="biomarker-unit">{{ item.unit }}</span>
                    </strong>
                    <div class="biomarker-ratio-bar" aria-hidden="true">
                      <div class="biomarker-ratio-bar__track"></div>
                      <div
                        v-if="item.hasReference"
                        class="biomarker-ratio-bar__reference"
                        :style="{ left: `${item.referenceStartPercent}%`, width: `${item.referenceWidthPercent}%` }"
                      ></div>
                      <div
                        class="biomarker-ratio-bar__marker"
                        :style="{
                          '--ratio-marker-percent': `${item.markerPercent}%`,
                          '--ratio-marker-color': item.markerColor
                        }"
                      ></div>
                    </div>
                    <div class="biomarker-ratio-scale">
                      <span>{{ item.scaleMinLabel }}</span>
                      <span>{{ item.scaleMaxLabel }}</span>
                    </div>
                    <span class="biomarker-range">{{ item.range }}</span>
                  </div>
                </div>

                <details class="biomarker-raw-details">
                  <summary>원값 지표 보기 (ABETA42, ABETA40, PTAU, TAU)</summary>
                  <div class="biomarker-grid biomarker-grid--raw">
                    <div v-for="item in biomarkerRawItems" :key="item.id" class="biomarker-item">
                      <span class="biomarker-label">{{ item.label }}</span>
                      <strong class="biomarker-value">
                        {{ formatBiomarkerValue(item.value, item.unit ? 1 : 2) }}
                        <span v-if="item.unit" class="biomarker-unit">{{ item.unit }}</span>
                      </strong>
                      <span v-if="item.range" class="biomarker-range">{{ item.range }}</span>
                    </div>
                  </div>
                </details>

                <p class="biomarker-meta-line">{{ biomarkerPlatformText }}</p>
              </div>

              <p v-else class="biomarker-empty">바이오마커 데이터가 없습니다.</p>
            </div>
          </template>
        </section>

        <section v-else-if="currentTab === 'voice'" class="panel voice-panel" aria-label="음성 대화">
          <EmptyState
            v-if="showVoicePending"
            class="empty-state-slot"
            type="info"
            icon="microphone"
            title="음성 분석 처리 중입니다"
            description="녹음은 업로드되었습니다. 분석이 완료되면 가장 최근 결과가 자동으로 반영됩니다."
            :benefits="['분석 완료 전에는 이전 완료 데이터만 표시됩니다.']"
            note="페이지를 유지하면 자동 갱신으로 결과가 표시됩니다."
            aria-label="음성 데이터 처리 중"
          />

          <EmptyState
            v-else-if="showVoiceEmpty"
            class="empty-state-slot"
            :type="voiceEmptyState.type"
            :icon="voiceEmptyState.icon"
            :title="voiceEmptyState.title"
            :description="voiceEmptyState.description"
            :benefits="voiceEmptyState.benefits"
            :note="voiceEmptyState.note"
            :action-text="voiceEmptyState.actionText"
            aria-label="음성 데이터 비어 있음"
          />

          <template v-else>
            <div v-if="latestVoiceAssessment" class="card voice-result-card">
              <div class="voice-result-header">
                <div>
                  <h4>최근 음성 분석 결과</h4>
                  <p>가장 최근 완료된 음성 평가 기준</p>
                </div>
                <span class="voice-result-badge" :class="`voice-result-${latestVoiceFlagKey}`">
                  {{ latestVoiceFlagLabel }}
                </span>
              </div>
              <div class="voice-result-grid">
                <div class="voice-result-item">
                  <span>MCI 확률</span>
                  <strong>{{ latestVoiceProbabilityText }}</strong>
                </div>
                <div class="voice-result-item">
                  <span>인지 점수</span>
                  <strong>{{ latestVoiceScoreText }}</strong>
                </div>
                <div class="voice-result-item">
                  <span>평가 시각</span>
                  <strong>{{ latestVoiceAssessedAtText }}</strong>
                </div>
              </div>
            </div>

            <div class="card voice-summary-card">
              <div>
                <h4>최근 4주간 변화 관찰</h4>
                <p>정규화된 지표 기준으로 변화 방향을 요약합니다.</p>
              </div>
              <div class="voice-summary-status" :class="`voice-status-${voiceSummary.statusKey}`">
                <span>이전 대비</span>
                <strong>{{ voiceSummary.statusLabel }}</strong>
                <small>{{ voiceSummary.detail }}</small>
              </div>
            </div>

            <div class="card voice-chart-card">
              <div class="voice-chart-header">
                <div>
                  <h4>음성 대화 지표 변화</h4>
                  <p>시간 축 기준 정규화된 지표 (percentile / index)</p>
                </div>
                <div class="voice-toggle-group">
                  <label
                    v-for="metric in normalizedVoiceMetrics"
                    :key="metric.id"
                    class="voice-toggle"
                    :class="{ disabled: !metric.hasData }"
                  >
                    <input
                      type="checkbox"
                      :value="metric.id"
                      v-model="activeVoiceMetricIds"
                      :disabled="!metric.hasData"
                    />
                    <span class="voice-toggle__dot" :style="{ background: metric.color }"></span>
                    <span class="voice-toggle__label">{{ metric.label }}</span>
                  </label>
                </div>
              </div>

              <div class="voice-chart">
                <svg :viewBox="`0 0 ${VOICE_CHART_WIDTH} ${VOICE_CHART_HEIGHT}`" role="img" aria-label="음성 지표 통합 그래프">
                  <g class="voice-grid">
                    <line v-for="y in [40, 80, 120, 160]" :key="y" :x1="0" :x2="VOICE_CHART_WIDTH" :y1="y" :y2="y" />
                  </g>
                  <path
                    v-for="metric in activeVoiceMetrics"
                    :key="metric.id"
                    :d="getNormalizedPath(metric.normalized)"
                    :stroke="metric.color"
                    stroke-width="3"
                    fill="none"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
                <div class="voice-chart-labels">
                  <span
                    v-for="(label, index) in voiceTimelineTickLabels"
                    :key="`${index}-${label}`"
                    :class="{ 'is-empty': !label }"
                  >
                    {{ label }}
                  </span>
                </div>
              </div>

              <p v-if="activeVoiceMetrics.length === 0" class="voice-chart-empty">
                표시할 지표를 선택해 주세요.
              </p>
            </div>
          </template>
        </section>

        <section v-else class="panel mri-analysis-panel" aria-label="MRI 분석">
          <EmptyState
            v-if="showMriEmpty"
            class="empty-state-slot"
            :type="mriEmptyState.type"
            :icon="mriEmptyState.icon"
            :title="mriEmptyState.title"
            :description="mriEmptyState.description"
            :benefits="mriEmptyState.benefits"
            :note="mriEmptyState.note"
            :action-text="mriEmptyState.actionText"
            aria-label="MRI 데이터 비어 있음"
          />

          <template v-else>
            <div class="card mri-context-card">
              <div>
                <h4>공간적 해석 요약</h4>
                <p>관심 영역 하이라이트와 기여도 분석을 중심으로 구조적 원인을 해석합니다.</p>
                <div class="mri-summary-lines">
                  <div class="mri-summary-row">
                    <span class="mri-summary-text">MRI 기반 분류:</span>
                    <span class="mri-summary-badge" :class="`mri-summary-badge-${mriMajorBadgeKey}`">
                      {{ mriMajorDisplay }}
                    </span>
                  </div>
                  <div
                    v-if="showMciSubtypeRow"
                    class="mri-summary-row"
                  >
                    <span class="mri-summary-text">인지검사·바이오마커 기반 분류:</span>
                    <span class="mri-summary-badge" :class="`mri-summary-badge-${mriSubtypeBadgeKey}`">
                      {{ mriSubtypeDisplay }}
                    </span>
                  </div>
                  <p v-if="mriClassificationConfidenceLabel" class="mri-summary-confidence">{{ mriClassificationConfidenceLabel }}</p>
                </div>
              </div>
              <div class="mri-context-meta">
                <div class="mri-meta-item">
                  <span>최근 촬영일</span>
                  <strong>{{ mriScanDate }}</strong>
                </div>
                <div class="mri-meta-item">
                  <span>MRI 기록</span>
                  <strong>{{ mriScanCount }}</strong>
                </div>
              </div>
            </div>

            <div class="mri-analysis-grid">
              <div class="card mri-images-card">
                <div class="mri-images-header">
                  <h4>MRI 이미지</h4>
                  <button
                    type="button"
                    class="mri-sync-toggle"
                    :aria-pressed="mriSyncEnabled"
                    :aria-label="mriSyncEnabled ? '원본/Attention 동기 이동 끄기' : '원본/Attention 동기 이동 켜기'"
                    :title="mriSyncEnabled ? '동기 이동 켜짐' : '동기 이동 꺼짐'"
                    @click="toggleMriSync"
                  >
                    {{ mriSyncEnabled ? '🔗' : '⛓️‍💥' }}
                  </button>
                </div>
                <MRIImageDisplay
                  :original-image="originalImage"
                  :original-maps="originalMaps"
                  :attention-map="attentionMap"
                  :attention-maps="attentionMaps"
                  :attention-slides="attentionSlides"
                  :original-slice-slider-enabled="mriSliceSliderEnabled"
                  :attention-slice-slider-enabled="mriSliceSliderEnabled"
                  :sync-navigation="mriSyncEnabled"
                  :loading="isLoading"
                />
                <div v-if="visitOptions.length > 0" class="visit-selector-row">
                  <label for="visit-selector">방문 기록</label>
                  <select id="visit-selector" v-model="selectedVisitImageId">
                    <option v-for="visit in visitOptions" :key="visit.optionKey" :value="visit.optionValue">
                      {{ String(visit.viscode2 || 'VISIT').toUpperCase() }} · {{ visit.examDate }}
                    </option>
                  </select>
                </div>
              </div>

              <div class="card contribution-card">
                <RegionContributionChart
                  :contributions="regionContributions"
                  :loading="isLoading"
                />
              </div>
            </div>

            <DoctorDiagnosisForm
              v-if="currentPatient?.id"
              :patient-id="currentPatient.id"
              doctor-id="doctor_001"
              @submit="handleDiagnosisSubmit"
            />

          </template>
        </section>
      </div>
    </transition>
  </div>
</template>
<style scoped>
.doctor-history {
  display: flex;
  flex-direction: column;
  gap: 24px;
  min-height: 0;
  font-size: 18px;
}

.data-alert {
  margin-top: 4px;
}

.empty-state-slot {
  width: 100%;
}

.loading {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.skeleton-line {
  height: 26px;
  border-radius: 14px;
  background: linear-gradient(90deg, #e0e5ec 25%, #f0f3f6 50%, #e0e5ec 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

.skeleton-card {
  height: 180px;
  border-radius: 24px;
  background: linear-gradient(90deg, #e0e5ec 25%, #f0f3f6 50%, #e0e5ec 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.tab-panel {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 12px 4px 24px;
}

.patient-banner {
  background: #f5f6f7;
  border-radius: 24px;
  padding: 20px 24px;
  box-shadow: 10px 10px 20px #d1d9e6, -10px -10px 20px #ffffff;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.patient-banner h3 {
  font-size: 22px;
  font-weight: 800;
  margin: 0 0 6px;
}

.patient-banner p {
  margin: 0;
  font-weight: 700;
  color: #4cb7b7;
}

.patient-meta {
  font-weight: 700;
  color: #555;
}

.panel {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.section-main-header {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-main-header h4 {
  margin: 0;
  font-size: 20px;
  font-weight: 800;
  color: #2e2e2e;
}

.section-subheader {
  margin-top: 0;
}

.section-subheader--stack {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.section-subheader--stack p {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: #777;
}

.section-chip {
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(76, 183, 183, 0.15);
  color: #2e7d7d;
  font-size: 13px;
  font-weight: 800;
}

.card {
  background: #f5f6f7;
  padding: 24px;
  border-radius: 26px;
  box-shadow: 12px 12px 24px #d1d9e6, -12px -12px 24px #ffffff;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.card h4 {
  font-size: 20px;
  font-weight: 800;
  margin: 0;
  color: #2e2e2e;
}

.voice-result-card {
  gap: 14px;
}

.voice-result-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.voice-result-header p {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
  color: #666;
}

.voice-result-badge {
  border-radius: 999px;
  padding: 7px 12px;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0.4px;
}

.voice-result-badge.voice-result-normal {
  background: rgba(25, 143, 99, 0.16);
  color: #0f7d56;
}

.voice-result-badge.voice-result-warning {
  background: rgba(245, 158, 11, 0.18);
  color: #b45309;
}

.voice-result-badge.voice-result-critical {
  background: rgba(239, 68, 68, 0.16);
  color: #b91c1c;
}

.voice-result-badge.voice-result-unknown {
  background: rgba(148, 163, 184, 0.2);
  color: #475569;
}

.voice-result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.voice-result-item {
  border-radius: 16px;
  background: #ffffff;
  box-shadow: inset 4px 4px 10px rgba(209, 217, 230, 0.6), inset -4px -4px 10px #ffffff;
  padding: 14px 16px;
  display: grid;
  gap: 6px;
}

.voice-result-item span {
  font-size: 13px;
  font-weight: 700;
  color: #6b7280;
}

.voice-result-item strong {
  font-size: 22px;
  font-weight: 900;
  color: #1f2937;
}

.score-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}

.score-card {
  background: #ffffff;
  border-radius: 20px;
  padding: 18px;
  box-shadow: inset 4px 4px 10px rgba(209, 217, 230, 0.6), inset -4px -4px 10px #ffffff;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.score-card.score-good {
  border: 1px solid rgba(76, 183, 183, 0.35);
}

.score-card.score-warn {
  border: 1px solid rgba(255, 183, 77, 0.45);
}

.score-card.score-bad {
  border: 1px solid rgba(255, 138, 128, 0.45);
}

.score-card.score-empty {
  border: 1px dashed rgba(0, 0, 0, 0.08);
}

.score-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.score-label {
  font-size: 16px;
  font-weight: 800;
  color: #2e2e2e;
}

.score-value {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 22px;
  font-weight: 900;
  color: #2e2e2e;
}

.score-unit {
  font-size: 12px;
  font-weight: 700;
  color: #999;
}

.score-sub {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  color: #777;
}

.score-bar {
  position: relative;
  height: 12px;
  border-radius: 999px;
  background: transparent;
  overflow: hidden;
}

.score-bar__track {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid rgba(196, 205, 216, 0.7);
  border-radius: 999px;
  z-index: 1;
}

.score-bar__reference {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 0;
  border-radius: 999px;
  background: rgba(118, 216, 168, 0.58);
  z-index: 2;
}

.score-bar__marker {
  --marker-percent: 0%;
  --marker-color: #198f63;
  --marker-width: 14px;
  position: absolute;
  left: clamp(calc(var(--marker-width) / 2), var(--marker-percent), calc(100% - (var(--marker-width) / 2)));
  top: 50%;
  width: var(--marker-width);
  height: 8px;
  border-radius: 999px;
  transform: translate(-50%, -50%);
  background: var(--marker-color);
  border: 1px solid rgba(255, 255, 255, 0.92);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
  transition: background-color 0.2s ease, box-shadow 0.2s ease;
  z-index: 4;
}

.score-scale {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  font-weight: 500;
  color: #a8adb3;
  margin-top: -2px;
}

.score-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  font-weight: 700;
  color: #777;
}

.clinical-group-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 2px 2px;
}

.clinical-extra-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
  padding-top: 6px;
}

.clinical-extra-item {
  background: #ffffff;
  border-radius: 14px;
  padding: 12px;
  box-shadow: inset 3px 3px 8px rgba(209, 217, 230, 0.45), inset -3px -3px 8px #ffffff;
  border: 1px solid rgba(76, 183, 183, 0.14);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.clinical-extra-item--warn {
  border-color: rgba(245, 158, 11, 0.5);
}

.clinical-extra-label {
  font-size: 12px;
  font-weight: 800;
  color: #8d9298;
}

.clinical-extra-value {
  font-size: 18px;
  font-weight: 900;
  color: #2e2e2e;
}

.clinical-extra-unit {
  margin-left: 4px;
  font-size: 12px;
  font-weight: 700;
  color: #999;
}

.biomarker-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 2px 2px;
}

.biomarker-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.biomarker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
}

.biomarker-grid--genetic {
  grid-template-columns: minmax(180px, 1fr);
}

.biomarker-item--genetic {
  border: 1px solid rgba(76, 183, 183, 0.24);
}

.biomarker-raw-details {
  border-radius: 14px;
  background: #f7f9fc;
  padding: 8px 10px 10px;
}

.biomarker-raw-details summary {
  cursor: pointer;
  list-style: none;
  font-size: 12px;
  font-weight: 800;
  color: #59626b;
}

.biomarker-raw-details summary::-webkit-details-marker {
  display: none;
}

.biomarker-raw-details summary::after {
  content: '▾';
  float: right;
  color: #7c8792;
}

.biomarker-raw-details[open] summary::after {
  content: '▴';
}

.biomarker-grid--raw {
  margin-top: 10px;
}

.biomarker-meta-line {
  margin: 0;
  font-size: 11px;
  font-weight: 700;
  color: #8b9199;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.biomarker-item {
  background: #ffffff;
  border-radius: 16px;
  padding: 14px;
  box-shadow: inset 3px 3px 8px rgba(209, 217, 230, 0.5), inset -3px -3px 8px #ffffff;
  border: 1px solid rgba(76, 183, 183, 0.24);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.biomarker-item--good {
  border-color: rgba(76, 183, 183, 0.35);
}

.biomarker-item--warn {
  border-color: rgba(255, 183, 77, 0.45);
}

.biomarker-item--bad {
  border-color: rgba(255, 138, 128, 0.45);
}

.biomarker-item--empty {
  border-style: dashed;
  border-color: rgba(0, 0, 0, 0.08);
}

.biomarker-label {
  font-size: 12px;
  font-weight: 800;
  color: #888;
}

.biomarker-value {
  font-size: 18px;
  font-weight: 900;
  color: #2e2e2e;
}

.biomarker-unit {
  font-size: 12px;
  font-weight: 700;
  color: #999;
  margin-left: 4px;
}

.biomarker-range {
  font-size: 12px;
  font-weight: 700;
  color: #777;
}

.biomarker-ratio-bar {
  position: relative;
  height: 12px;
  border-radius: 999px;
  background: transparent;
  overflow: hidden;
}

.biomarker-ratio-bar__track {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(196, 205, 216, 0.72);
  border-radius: 999px;
  z-index: 1;
}

.biomarker-ratio-bar__reference {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 0;
  border-radius: 999px;
  background: rgba(118, 216, 168, 0.56);
  z-index: 2;
}

.biomarker-ratio-bar__marker {
  --ratio-marker-percent: 0%;
  --ratio-marker-color: #198f63;
  --ratio-marker-width: 12px;
  position: absolute;
  left: clamp(calc(var(--ratio-marker-width) / 2), var(--ratio-marker-percent), calc(100% - (var(--ratio-marker-width) / 2)));
  top: 50%;
  width: var(--ratio-marker-width);
  height: 8px;
  border-radius: 999px;
  transform: translate(-50%, -50%);
  background: var(--ratio-marker-color);
  border: 1px solid rgba(255, 255, 255, 0.92);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.16);
  z-index: 4;
}

.biomarker-ratio-scale {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 10px;
  font-weight: 600;
  color: #a1a8b1;
  margin-top: 2px;
}

.biomarker-empty {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: #777;
}

.voice-summary-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
}

.voice-summary-card p {
  margin: 6px 0 0;
  font-size: 14px;
  font-weight: 700;
  color: #777;
}

.voice-summary-status {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 18px;
  border-radius: 18px;
  background: #ffffff;
  box-shadow: inset 4px 4px 8px rgba(209, 217, 230, 0.5), inset -4px -4px 8px #ffffff;
  min-width: 140px;
  text-align: center;
  font-weight: 800;
  color: #2e2e2e;
}

.voice-summary-status strong {
  font-size: 20px;
}

.voice-summary-status small {
  font-size: 12px;
  font-weight: 700;
  color: #777;
}

.voice-status-increase {
  color: #2e2e2e;
  border: 1px solid rgba(255, 183, 77, 0.45);
}

.voice-status-decrease {
  color: #2e2e2e;
  border: 1px solid rgba(255, 138, 128, 0.45);
}

.voice-status-stable {
  color: #2e2e2e;
  border: 1px solid rgba(76, 183, 183, 0.45);
}

.voice-status-mixed {
  color: #2e2e2e;
  border: 1px solid rgba(120, 120, 120, 0.35);
}

.voice-status-empty {
  color: #2e2e2e;
  border: 1px dashed rgba(0, 0, 0, 0.12);
}

.voice-chart-card {
  gap: 18px;
}

.voice-chart-header {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 12px;
}

.voice-chart-header p {
  margin: 6px 0 0;
  font-size: 14px;
  font-weight: 700;
  color: #777;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.voice-toggle-group {
  display: grid;
  grid-template-columns: repeat(2, max-content);
  gap: 10px;
  justify-content: flex-end;
  align-self: flex-end;
  width: fit-content;
  max-width: 100%;
}

.voice-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: inset 2px 2px 6px rgba(209, 217, 230, 0.4), inset -2px -2px 6px #ffffff;
  font-size: 13px;
  font-weight: 800;
  color: #2e2e2e;
  white-space: nowrap;
}

.voice-toggle input {
  width: 14px;
  height: 14px;
}

.voice-toggle.disabled {
  opacity: 0.4;
  pointer-events: none;
}

.voice-toggle__dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.voice-chart {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.voice-chart svg {
  width: 100%;
  height: 220px;
}

.voice-grid line {
  stroke: rgba(0, 0, 0, 0.06);
  stroke-width: 1;
}

.voice-chart-labels {
  display: flex;
  justify-content: space-between;
  font-weight: 700;
  color: #999;
  font-size: 12px;
}

.voice-chart-labels span {
  flex: 1;
  text-align: center;
  white-space: nowrap;
}

.voice-chart-labels span:first-child {
  text-align: left;
}

.voice-chart-labels span:last-child {
  text-align: right;
}

.voice-chart-labels .is-empty {
  visibility: hidden;
}

.voice-chart-empty {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: #777;
}

.mri-analysis-panel {
  gap: 24px;
}

.mri-context-card {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
}

.mri-context-card p {
  margin: 6px 0 0;
  font-size: 14px;
  font-weight: 700;
  color: #777;
}

.mri-summary-lines {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
}

.mri-summary-row {
  display: inline-flex;
  justify-content: flex-start;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mri-summary-text {
  font-size: 13px;
  font-weight: 900;
  color: #656b74;
  letter-spacing: -0.01em;
}

.mri-summary-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 3px 12px;
  border-radius: 999px;
  border: 1px solid rgba(178, 186, 196, 0.45);
  background: #eef2f6;
  color: #5f6670;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: -0.01em;
  line-height: 1.25;
  flex: 0 0 auto;
}

.mri-summary-badge-cn {
  border-color: rgba(76, 183, 183, 0.38);
  background: rgba(76, 183, 183, 0.12);
  color: #1f7d7d;
}

.mri-summary-badge-smci {
  border-color: rgba(100, 181, 246, 0.44);
  background: rgba(100, 181, 246, 0.14);
  color: #2a6ea0;
}

.mri-summary-badge-pmci {
  border-color: rgba(245, 158, 11, 0.46);
  background: rgba(245, 158, 11, 0.18);
  color: #9a6210;
}

.mri-summary-badge-ad {
  border-color: rgba(239, 68, 68, 0.5);
  background: rgba(239, 68, 68, 0.14);
  color: #a43131;
}

.mri-summary-badge-mci {
  border-color: rgba(255, 183, 77, 0.52);
  background: rgba(255, 183, 77, 0.16);
  color: #9a6616;
}

.mri-summary-badge-unknown {
  border-color: rgba(178, 186, 196, 0.45);
  background: #eef2f6;
  color: #6f7781;
}

.mri-summary-confidence {
  margin: 0;
  font-size: 13px;
  font-weight: 900;
  color: #656b74;
  letter-spacing: -0.01em;
}

.mri-context-meta {
  display: flex;
  gap: 16px;
}

.mri-meta-item {
  background: #ffffff;
  border-radius: 16px;
  padding: 12px 14px;
  box-shadow: inset 3px 3px 8px rgba(209, 217, 230, 0.5), inset -3px -3px 8px #ffffff;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 120px;
}

.mri-meta-item span {
  font-size: 12px;
  font-weight: 800;
  color: #888;
}

.mri-meta-item strong {
  font-size: 16px;
  font-weight: 900;
  color: #2e2e2e;
}

.mri-analysis-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}

.mri-images-card {
  gap: 20px;
}

.mri-images-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.mri-images-card h4 {
  margin-bottom: 4px;
}

.mri-sync-toggle {
  width: 44px;
  height: 44px;
  border-radius: 999px;
  border: 1px solid #bfd0e1;
  background: #f5f8fb;
  box-shadow: inset 2px 2px 5px rgba(209, 217, 230, 0.45), inset -2px -2px 5px #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}

.contribution-card {
  padding: 0;
  background: transparent;
  box-shadow: none;
}

.visit-selector-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid #e5e7eb;
}

.visit-selector-row label {
  font-size: 15px;
  font-weight: 800;
  color: #777;
  white-space: nowrap;
}

.visit-selector-row select {
  flex: 1;
  padding: 10px 14px;
  border-radius: 12px;
  border: none;
  background: #f5f6f7;
  box-shadow: inset 3px 3px 6px rgba(209, 217, 230, 0.6), inset -3px -3px 6px #ffffff;
  font-size: 15px;
  font-weight: 700;
  color: #2e2e2e;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 520px) {
  .patient-banner {
    flex-direction: column;
    align-items: flex-start;
  }

  .section-main-header {
    gap: 8px;
  }

  .clinical-extra-grid {
    grid-template-columns: 1fr;
  }

  .voice-summary-card {
    flex-direction: column;
    align-items: flex-start;
  }

  .voice-summary-status {
    align-self: center;
  }

  .voice-chart-header {
    flex-direction: column;
    align-items: stretch;
  }

  .voice-toggle-group {
    align-self: flex-end;
  }

  .mri-analysis-grid {
    grid-template-columns: 1fr;
  }

  .mri-context-card {
    flex-direction: column;
  }

  .mri-context-meta {
    width: 100%;
    justify-content: space-between;
  }

  .visit-selector-row {
    flex-direction: column;
    align-items: stretch;
  }
}

@media (max-width: 1024px) {
  .mri-analysis-grid {
    grid-template-columns: 1fr;
  }
}
</style>
