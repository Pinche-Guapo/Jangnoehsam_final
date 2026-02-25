import { getTrendDateRange, type TrendRangeKey } from "@/composables/useTrendRange";
import { useAuthStore } from "@/stores/auth";
import { buildCaregiverRecordModel } from "../buildCaregiverRecordModel";
import type {
  CaregiverRecordPageModel,
  CaregiverRecordProvider,
  DailyTrendLevel,
  DailyTrendPoint,
  ObservationItem,
  ObservationSeverity,
} from "../types";

type TrendFlag = "ANOMALY" | "LOW_SAMPLE" | "MISSING";

interface FamilyPatientResponse {
  user_id?: number;
  subject_id?: string;
}

interface FamilyTrendPointResponse {
  date: string;
  value: number | null;
  sampleCount: number;
  confidence: number;
  flags: TrendFlag[];
}

interface FamilyTrendResponse {
  startDate: string;
  endDate: string;
  points: FamilyTrendPointResponse[];
  summary?: {
    statusLabel?: "안정 흐름" | "관찰 필요" | "유지 중";
    reason?: string;
    avg?: number | null;
    deltaPercent?: number | null;
  };
}

const mean = (values: number[]) => (values.length ? values.reduce((acc, value) => acc + value, 0) / values.length : 0);

const standardDeviation = (values: number[]) => {
  if (values.length < 2) return 0;
  const avg = mean(values);
  const variance = values.reduce((acc, value) => acc + (value - avg) ** 2, 0) / Math.max(values.length - 1, 1);
  return Math.sqrt(variance);
};

const toMonthDay = (isoDate: string) => {
  const date = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) return isoDate;
  return `${date.getMonth() + 1}.${date.getDate()}`;
};

const levelFromPoint = (point: FamilyTrendPointResponse, avg: number, std: number): DailyTrendLevel => {
  if (!point || point.sampleCount <= 0 || point.value === null) return "missing";

  const downStrong = Math.max(std * 0.8, 4);
  const downSoft = Math.max(std * 0.4, 2);
  const hasAnomaly = point.flags.includes("ANOMALY");
  const hasLowSample = point.flags.includes("LOW_SAMPLE");

  if (point.value <= avg - downStrong || (hasAnomaly && point.value <= avg - downSoft)) return "decline";
  if (point.value <= avg - downSoft || hasAnomaly || hasLowSample) return "change";
  return "stable";
};

const toDailyPoint = (point: FamilyTrendPointResponse, avg: number, std: number): DailyTrendPoint => {
  const level = levelFromPoint(point, avg, std);
  const interruptions = level === "decline" ? 4 : level === "change" ? 2 : level === "missing" ? 0 : 1;
  const responseDelayPercent = level === "decline" ? 21 : level === "change" ? 11 : level === "missing" ? 0 : 4;

  return {
    date: point.date,
    level,
    flags: point.flags ?? [],
    metrics: {
      interruptions,
      responseDelayPercent,
      participationGap: level === "missing",
    },
  };
};

const buildSummaryStatus = (
  points: DailyTrendPoint[],
  backendLabel?: "안정 흐름" | "관찰 필요" | "유지 중"
): CaregiverRecordPageModel["periodSummary"]["status"] => {
  const missingDays = points.filter((point) => point.level === "missing").length;
  const declineDays = points.filter((point) => point.level === "decline").length;
  const changeDays = points.filter((point) => point.level === "change").length;
  const validDays = points.length - missingDays;

  if (validDays === 0 || missingDays >= Math.ceil(points.length * 0.5)) return "미참여";
  if (backendLabel === "관찰 필요" || declineDays >= Math.max(2, Math.ceil(validDays * 0.25))) return "저하";
  if (changeDays >= Math.max(2, Math.ceil(validDays * 0.25)) || backendLabel === "유지 중") return "변화";
  return "안정";
};

const buildEvidenceMetricText = (
  summary: FamilyTrendResponse["summary"] | undefined,
  dailyTrendPoints: DailyTrendPoint[]
) => {
  const values = dailyTrendPoints
    .filter((point) => point.level !== "missing")
    .map((point) => point.metrics.responseDelayPercent);
  const delayAvg = values.length ? Math.round(mean(values)) : 0;
  const avgText =
    typeof summary?.avg === "number" && Number.isFinite(summary.avg) ? `평균 점수 ${summary.avg.toFixed(1)}` : "평균 점수 -";
  const deltaText =
    typeof summary?.deltaPercent === "number" && Number.isFinite(summary.deltaPercent)
      ? `변화율 ${summary.deltaPercent >= 0 ? "+" : ""}${summary.deltaPercent.toFixed(1)}%`
      : "변화율 -";
  return `${avgText}, ${deltaText}, 반응지연 +${delayAvg}%`;
};

const buildComparisonRows = (points: DailyTrendPoint[]) => {
  const half = Math.max(1, Math.floor(points.length / 2));
  const previous = points.slice(0, half);
  const current = points.slice(half);

  const scoreFromPoint = (point: DailyTrendPoint) => (point.level === "stable" ? 2 : point.level === "change" ? 1 : 0);
  const avgOf = (items: DailyTrendPoint[]) => {
    const valid = items.filter((item) => item.level !== "missing");
    if (!valid.length) return null;
    return Number((valid.map(scoreFromPoint).reduce((acc, value) => acc + value, 0) / valid.length).toFixed(2));
  };
  const missingOf = (items: DailyTrendPoint[]) => items.filter((item) => item.level === "missing").length;
  const interruptionsOf = (items: DailyTrendPoint[]) => items.reduce((acc, item) => acc + item.metrics.interruptions, 0);

  const currentAvg = avgOf(current);
  const previousAvg = avgOf(previous);

  return [
    {
      label: "상태 점수",
      current: currentAvg === null ? "-" : String(currentAvg),
      previous: previousAvg === null ? "-" : String(previousAvg),
    },
    {
      label: "중단 횟수",
      current: `${interruptionsOf(current)}회`,
      previous: `${interruptionsOf(previous)}회`,
    },
    {
      label: "미측정",
      current: `${missingOf(current)}일`,
      previous: `${missingOf(previous)}일`,
    },
  ];
};

const buildObservationItems = (dailyTrendPoints: DailyTrendPoint[]): ObservationItem[] => {
  const speechLogs = dailyTrendPoints
    .filter((point) => point.level === "change" || point.level === "decline")
    .slice(-5)
    .map((point) => ({
      date: point.date,
      detail: `반응지연 +${point.metrics.responseDelayPercent}%`,
    }));

  const trainingLogs = dailyTrendPoints
    .filter((point) => point.level === "decline")
    .slice(-5)
    .map((point) => ({
      date: point.date,
      detail: `중단 ${point.metrics.interruptions}회`,
    }));

  const participationLogs = dailyTrendPoints
    .filter((point) => point.level === "missing")
    .slice(-5)
    .map((point) => ({
      date: point.date,
      detail: "해당일 미측정",
    }));

  const severityOf = (count: number): ObservationSeverity => (count >= 3 ? "뚜렷함" : count >= 1 ? "주의" : "가벼움");

  return [
    {
      domain: "training",
      severity: severityOf(trainingLogs.length),
      title: "훈련 리듬 변화 관찰",
      deltaMetrics: `최근 저하 ${trainingLogs.length}일`,
      evidenceLogs: trainingLogs,
      actions: ["훈련 세션을 짧게 나눠 진행", "피로도가 낮은 시간대 우선 배치"],
    },
    {
      domain: "speech",
      severity: severityOf(speechLogs.length),
      title: "발화 반응 편차 관찰",
      deltaMetrics: `변화 신호 ${speechLogs.length}일`,
      evidenceLogs: speechLogs,
      actions: ["질문 간격을 3~5초로 유지", "한 번에 한 문장씩 천천히 안내"],
    },
    {
      domain: "participation",
      severity: severityOf(participationLogs.length),
      title: "참여 공백 확인",
      deltaMetrics: `미측정 ${participationLogs.length}일`,
      evidenceLogs: participationLogs,
      actions: ["미측정일에 생활 리듬 확인", "대화 참여 시간대를 고정해 반복"],
    },
  ];
};

export class BaseCaregiverRecordProvider implements CaregiverRecordProvider {
  async getRecord(range: TrendRangeKey): Promise<CaregiverRecordPageModel> {
    try {
      const liveRecord = await this.getRecordFromFamilyApi(range);
      if (liveRecord) return liveRecord;
    } catch (error) {
      console.warn("[CaregiverRecordProvider] API record fetch failed, fallback to mock.", error);
    }
    return buildCaregiverRecordModel(range);
  }

  private async getRecordFromFamilyApi(range: TrendRangeKey): Promise<CaregiverRecordPageModel | null> {
    const authStore = useAuthStore();
    const familyIdRaw = authStore.user?.entity_id ?? authStore.user?.id;
    const familyId = Number(familyIdRaw);
    if (!Number.isFinite(familyId) || familyId <= 0) return null;

    const headers: Record<string, string> = { Accept: "application/json" };
    if (authStore.token) {
      headers.Authorization = `Bearer ${authStore.token}`;
    }

    const patientRes = await fetch(`/api/family/patient?family_id=${encodeURIComponent(String(familyId))}`, {
      method: "GET",
      headers,
    });
    if (!patientRes.ok) return null;

    const patient = (await patientRes.json()) as FamilyPatientResponse;
    const subjectId = patient.user_id ?? patient.subject_id;
    if (!subjectId) return null;

    const { startDate, endDate } = getTrendDateRange(range);
    const trendUrl = new URL("/api/family/weekly-trend", window.location.origin);
    trendUrl.searchParams.set("subjectId", String(subjectId));
    trendUrl.searchParams.set("startDate", startDate);
    trendUrl.searchParams.set("endDate", endDate);

    const trendRes = await fetch(trendUrl.toString(), { method: "GET", headers });
    if (!trendRes.ok) return null;

    const trendData = (await trendRes.json()) as FamilyTrendResponse;
    const points = Array.isArray(trendData.points) ? trendData.points : [];
    if (!points.length) return null;

    const validValues = points
      .map((point) => point.value)
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
    const avg = mean(validValues);
    const std = standardDeviation(validValues);

    const dailyTrendPoints = points.map((point) => toDailyPoint(point, avg, std));
    const status = buildSummaryStatus(dailyTrendPoints, trendData.summary?.statusLabel);

    const stableDays = dailyTrendPoints.filter((point) => point.level === "stable").length;
    const changeDays = dailyTrendPoints.filter((point) => point.level === "change").length;
    const declineDays = dailyTrendPoints.filter((point) => point.level === "decline").length;
    const missingDays = dailyTrendPoints.filter((point) => point.level === "missing").length;

    const rawLogs = dailyTrendPoints.map((point) => ({
      date: point.date,
      detail:
        point.level === "decline"
          ? "최근 흐름에서 저하 신호가 관찰됨"
          : point.level === "change"
            ? "기존 패턴 대비 변화 신호가 관찰됨"
            : point.level === "missing"
              ? "해당일 음성 데이터 미측정"
              : "안정 범위 유지",
    }));

    const notableChanges = dailyTrendPoints
      .filter((point) => point.level !== "stable")
      .slice(-2)
      .reverse()
      .map((point) => ({
        domain: point.level === "missing" ? "participation" : point.level === "decline" ? "training" : "speech",
        whenLabel: toMonthDay(point.date),
        title:
          point.level === "missing"
            ? "해당일은 측정 공백이 있었습니다."
            : point.level === "decline"
              ? "해당일은 저하 신호가 포착되었습니다."
              : "해당일은 이전 대비 변화가 있었습니다.",
        actionTip:
          point.level === "missing"
            ? "같은 시간대 측정 루틴을 유지해 공백을 줄여주세요."
            : point.level === "decline"
              ? "생활 리듬과 컨디션을 함께 점검해 주세요."
              : "최근 일정과 대화 환경을 함께 확인해 주세요.",
      }));

    return {
      range,
      periodSummary: {
        status,
        evidenceMetrics: buildEvidenceMetricText(trendData.summary, dailyTrendPoints),
        recommendedAction:
          status === "저하"
            ? "최근 저하 신호가 있어 생활 리듬과 측정 환경을 함께 점검해 주세요."
            : status === "변화"
              ? "변화가 반복되면 일정/컨디션 기록을 함께 확인해 주세요."
              : status === "미참여"
                ? "미측정일이 많아 동일 시간대 측정 루틴 재정비가 필요합니다."
                : "현재 흐름은 비교적 안정적입니다. 동일 루틴을 유지해 주세요.",
        dateRangeLabel: `${trendData.startDate} ~ ${trendData.endDate}`,
        quickStats: [`안정 ${stableDays}일`, `변화/저하 ${changeDays + declineDays}일`, `미측정 ${missingDays}일`],
        comparisonRows: buildComparisonRows(dailyTrendPoints),
      },
      dailyTrendPoints,
      observationItems: buildObservationItems(dailyTrendPoints),
      notableChanges,
      rawLogs,
    };
  }
}
