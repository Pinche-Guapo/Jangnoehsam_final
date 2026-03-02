<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';

type AttentionMapInput = {
  plane?: string;
  label?: string;
  url?: string;
  path?: string;
  image?: string;
};

type AttentionSlideInput = {
  rank?: number;
  roi?: string;
  description?: string;
  score?: number;
  percentage?: number;
  views?: AttentionMapInput[];
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
  percentage?: number | null;
  views: AttentionMapItem[];
};

const props = defineProps<{
  originalImage?: string;
  originalMaps?: AttentionMapInput[];
  attentionMap?: string;
  attentionMaps?: AttentionMapInput[];
  attentionSlides?: AttentionSlideInput[];
  originalSliceSliderEnabled?: boolean;
  attentionSliceSliderEnabled?: boolean;
  syncNavigation?: boolean;
  loading?: boolean;
}>();

const failedOriginalUrls = ref<string[]>([]);
const failedAttentionUrls = ref<string[]>([]);
const selectedSlideIndex = ref(0);
const selectedViewIndex = ref(0);
const selectedOriginalViewIndex = ref(0);
const originalSliceIndexPercent = ref(50);
const attentionSliceIndexPercent = ref(50);
const debouncedOriginalSliceIndexPercent = ref(50);
const debouncedAttentionSliceIndexPercent = ref(50);
const displayedOriginalUrl = ref('');
const displayedAttentionUrl = ref('');
const originalFrameLoading = ref(false);
const attentionFrameLoading = ref(false);
const originalLoadToken = ref(0);
const attentionLoadToken = ref(0);
const isSyncingSlices = ref(false);
const originalSliceDebounceTimer = ref<ReturnType<typeof setTimeout> | null>(null);
const attentionSliceDebounceTimer = ref<ReturnType<typeof setTimeout> | null>(null);
const SLICE_DEBOUNCE_MS = 70;

const planeOrder: Record<AttentionMapItem['plane'], number> = {
  axial: 0,
  coronal: 1,
  sagittal: 2
};

const normalizePlane = (value: unknown): AttentionMapItem['plane'] => {
  const text = String(value || '').trim().toLowerCase();
  if (text.startsWith('ax')) return 'axial';
  if (text.startsWith('cor')) return 'coronal';
  if (text.startsWith('sag')) return 'sagittal';
  return 'axial';
};

const resolveAttentionLabel = (plane: AttentionMapItem['plane']) => {
  if (plane === 'coronal') return 'Coronal';
  if (plane === 'sagittal') return 'Sagittal';
  return 'Axial';
};

const parseMapItems = (maps: AttentionMapInput[]) => {
  const parsed = maps
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const url = String(item.url || item.path || item.image || '').trim();
      if (!url) return null;
      const plane = normalizePlane(item.plane);
      return {
        plane,
        label: resolveAttentionLabel(plane),
        url
      } as AttentionMapItem;
    })
    .filter((item): item is AttentionMapItem => Boolean(item));

  const uniqueByPlane = new Map<AttentionMapItem['plane'], AttentionMapItem>();
  parsed.forEach((item) => {
    if (!uniqueByPlane.has(item.plane)) {
      uniqueByPlane.set(item.plane, item);
    }
  });

  return Array.from(uniqueByPlane.values()).sort(
    (a, b) => planeOrder[a.plane] - planeOrder[b.plane]
  );
};

const upsertQueryParam = (url: string, key: string, value: string) => {
  const raw = String(url || '').trim();
  if (!raw || !key) return raw;
  const encodedValue = encodeURIComponent(String(value));
  const matcher = new RegExp(`([?&]${key}=)[^&]*`, 'i');
  if (matcher.test(raw)) {
    return raw.replace(matcher, `$1${encodedValue}`);
  }
  return `${raw}${raw.includes('?') ? '&' : '?'}${key}=${encodedValue}`;
};

const appendSliceIndex = (url: string, enabled: boolean | undefined, percent: number) => {
  const raw = String(url || '').trim();
  if (!raw) return '';
  if (!enabled) return raw;
  return upsertQueryParam(raw, 'slice_index', String(percent));
};

const fallbackAttentionMaps = computed<AttentionMapItem[]>(() => {
  const provided = Array.isArray(props.attentionMaps) ? props.attentionMaps : [];
  const parsed = parseMapItems(provided);
  if (parsed.length > 0) return parsed;

  const single = String(props.attentionMap || '').trim();
  if (!single) return [];
  return [{ plane: 'axial', label: 'Axial', url: single }];
});

const fallbackOriginalMaps = computed<AttentionMapItem[]>(() => {
  const provided = Array.isArray(props.originalMaps) ? props.originalMaps : [];
  const parsed = parseMapItems(provided);
  if (parsed.length > 0) return parsed;

  const single = String(props.originalImage || '').trim();
  if (!single) return [];
  return [{ plane: 'axial', label: 'Axial', url: single }];
});

const resolvedAttentionSlides = computed<AttentionSlideItem[]>(() => {
  const sourceSlides = Array.isArray(props.attentionSlides) ? props.attentionSlides : [];
  const parsed = sourceSlides
    .map((slide, index) => {
      if (!slide || typeof slide !== 'object') return null;
      const views = parseMapItems(Array.isArray(slide.views) ? slide.views : []);
      if (views.length === 0) return null;
      const rankValue = Number(slide.rank);
      return {
        rank: Number.isFinite(rankValue) ? rankValue : index + 1,
        roi: typeof slide.roi === 'string' ? slide.roi : undefined,
        description: typeof slide.description === 'string' ? slide.description : undefined,
        percentage: Number.isFinite(Number(slide.percentage)) ? Number(slide.percentage) : null,
        views
      } as AttentionSlideItem;
    })
    .filter((item): item is AttentionSlideItem => Boolean(item));

  if (parsed.length > 0) {
    return parsed.sort((a, b) => a.rank - b.rank);
  }

  if (fallbackAttentionMaps.value.length > 0) {
    return [
      {
        rank: 1,
        views: fallbackAttentionMaps.value
      }
    ];
  }
  return [];
});

const currentSlide = computed<AttentionSlideItem | null>(() => {
  if (!resolvedAttentionSlides.value.length) return null;
  const index = Math.min(
    Math.max(selectedSlideIndex.value, 0),
    resolvedAttentionSlides.value.length - 1
  );
  return resolvedAttentionSlides.value[index] || null;
});

const attentionViews = computed<AttentionMapItem[]>(() => {
  const slide = currentSlide.value;
  if (slide?.views?.length) {
    return [...slide.views].sort((a, b) => planeOrder[a.plane] - planeOrder[b.plane]);
  }
  return [...fallbackAttentionMaps.value].sort((a, b) => planeOrder[a.plane] - planeOrder[b.plane]);
});

const currentAttentionView = computed<AttentionMapItem | null>(() => {
  if (!attentionViews.value.length) return null;
  const index = Math.min(Math.max(selectedViewIndex.value, 0), attentionViews.value.length - 1);
  return attentionViews.value[index] || null;
});

const currentOriginalView = computed<AttentionMapItem | null>(() => {
  if (!fallbackOriginalMaps.value.length) return null;
  const index = Math.min(
    Math.max(selectedOriginalViewIndex.value, 0),
    fallbackOriginalMaps.value.length - 1
  );
  return fallbackOriginalMaps.value[index] || fallbackOriginalMaps.value[0];
});

const currentOriginalUrl = computed(() =>
  appendSliceIndex(
    currentOriginalView.value?.url || '',
    props.originalSliceSliderEnabled,
    debouncedOriginalSliceIndexPercent.value
  )
);

const currentAttentionUrl = computed(() =>
  appendSliceIndex(
    currentAttentionView.value?.url || '',
    props.attentionSliceSliderEnabled,
    debouncedAttentionSliceIndexPercent.value
  )
);

const findViewIndexByPlane = (
  items: AttentionMapItem[],
  plane: AttentionMapItem['plane'] | undefined
) => {
  if (!plane) return -1;
  return items.findIndex((item) => item.plane === plane);
};

const syncAttentionViewToOriginal = () => {
  if (!props.syncNavigation) return;
  const originalPlane = currentOriginalView.value?.plane;
  const targetIndex = findViewIndexByPlane(attentionViews.value, originalPlane);
  if (targetIndex >= 0) selectedViewIndex.value = targetIndex;
};

const syncOriginalViewToAttention = () => {
  if (!props.syncNavigation) return;
  const attentionPlane = currentAttentionView.value?.plane;
  const targetIndex = findViewIndexByPlane(fallbackOriginalMaps.value, attentionPlane);
  if (targetIndex >= 0) selectedOriginalViewIndex.value = targetIndex;
};

const preloadImage = (url: string) =>
  new Promise<boolean>((resolve) => {
    const raw = String(url || '').trim();
    if (!raw) {
      resolve(false);
      return;
    }
    const image = new Image();
    image.onload = () => resolve(true);
    image.onerror = () => resolve(false);
    image.src = raw;
  });

const warmupImage = (url: string) => {
  const raw = String(url || '').trim();
  if (!raw) return;
  const image = new Image();
  image.src = raw;
};

const clampPercent = (value: number) => Math.min(100, Math.max(0, Math.round(value)));

const preloadNeighborSlices = (
  baseUrl: string,
  sliderEnabled: boolean | undefined,
  percent: number
) => {
  if (!sliderEnabled || !baseUrl) return;
  [-2, -1, 1, 2].forEach((delta) => {
    const nextPercent = clampPercent(percent + delta);
    const nextUrl = appendSliceIndex(baseUrl, true, nextPercent);
    warmupImage(nextUrl);
  });
};

const preloadNeighborPlanes = (
  maps: AttentionMapItem[],
  currentIndex: number,
  sliderEnabled: boolean | undefined,
  percent: number
) => {
  if (!maps.length) return;
  const prevIndex = currentIndex - 1;
  const nextIndex = currentIndex + 1;
  [prevIndex, nextIndex].forEach((index) => {
    if (index < 0 || index >= maps.length) return;
    const nextUrl = appendSliceIndex(maps[index].url, sliderEnabled, percent);
    warmupImage(nextUrl);
  });
};

const prefetchViewByIndex = (
  maps: AttentionMapItem[],
  index: number,
  sliderEnabled: boolean | undefined,
  percent: number
) => {
  if (!maps.length || index < 0 || index >= maps.length) return;
  const map = maps[index];
  const targetUrl = appendSliceIndex(map.url, sliderEnabled, percent);
  warmupImage(targetUrl);
  preloadNeighborSlices(map.url, sliderEnabled, percent);
  preloadNeighborPlanes(maps, index, sliderEnabled, percent);
};

watch(
  () => props.syncNavigation,
  (enabled) => {
    if (!enabled) return;
    syncAttentionViewToOriginal();
    isSyncingSlices.value = true;
    attentionSliceIndexPercent.value = originalSliceIndexPercent.value;
    debouncedAttentionSliceIndexPercent.value = debouncedOriginalSliceIndexPercent.value;
    isSyncingSlices.value = false;
  },
  { immediate: true }
);

watch(originalSliceIndexPercent, (value) => {
  const prefetchUrl = appendSliceIndex(
    currentOriginalView.value?.url || '',
    props.originalSliceSliderEnabled,
    value
  );
  warmupImage(prefetchUrl);

  if (originalSliceDebounceTimer.value) {
    clearTimeout(originalSliceDebounceTimer.value);
  }
  originalSliceDebounceTimer.value = setTimeout(() => {
    debouncedOriginalSliceIndexPercent.value = value;
    originalSliceDebounceTimer.value = null;
  }, SLICE_DEBOUNCE_MS);

  if (!props.syncNavigation || isSyncingSlices.value) return;
  if (attentionSliceIndexPercent.value === value) return;
  isSyncingSlices.value = true;
  attentionSliceIndexPercent.value = value;
  isSyncingSlices.value = false;
});

watch(attentionSliceIndexPercent, (value) => {
  const prefetchUrl = appendSliceIndex(
    currentAttentionView.value?.url || '',
    props.attentionSliceSliderEnabled,
    value
  );
  warmupImage(prefetchUrl);

  if (attentionSliceDebounceTimer.value) {
    clearTimeout(attentionSliceDebounceTimer.value);
  }
  attentionSliceDebounceTimer.value = setTimeout(() => {
    debouncedAttentionSliceIndexPercent.value = value;
    attentionSliceDebounceTimer.value = null;
  }, SLICE_DEBOUNCE_MS);

  if (!props.syncNavigation || isSyncingSlices.value) return;
  if (originalSliceIndexPercent.value === value) return;
  isSyncingSlices.value = true;
  originalSliceIndexPercent.value = value;
  isSyncingSlices.value = false;
});

watch(
  () => [currentOriginalView.value?.url, debouncedOriginalSliceIndexPercent.value],
  ([baseUrl, percent]) => {
    if (!baseUrl) return;
    preloadNeighborSlices(baseUrl, props.originalSliceSliderEnabled, Number(percent));
    preloadNeighborPlanes(
      fallbackOriginalMaps.value,
      selectedOriginalViewIndex.value,
      props.originalSliceSliderEnabled,
      Number(percent)
    );
  }
);

watch(
  () => [currentAttentionView.value?.url, debouncedAttentionSliceIndexPercent.value],
  ([baseUrl, percent]) => {
    if (!baseUrl) return;
    preloadNeighborSlices(baseUrl, props.attentionSliceSliderEnabled, Number(percent));
    preloadNeighborPlanes(
      attentionViews.value,
      selectedViewIndex.value,
      props.attentionSliceSliderEnabled,
      Number(percent)
    );
  }
);

watch(
  currentOriginalUrl,
  async (url) => {
    const nextUrl = String(url || '').trim();
    if (!nextUrl) {
      displayedOriginalUrl.value = '';
      originalFrameLoading.value = false;
      return;
    }
    const token = ++originalLoadToken.value;
    originalFrameLoading.value = true;
    const ok = await preloadImage(nextUrl);
    if (token !== originalLoadToken.value) return;
    originalFrameLoading.value = false;
    if (ok) {
      displayedOriginalUrl.value = nextUrl;
      return;
    }
    if (!failedOriginalUrls.value.includes(nextUrl)) {
      failedOriginalUrls.value = [...failedOriginalUrls.value, nextUrl];
    }
  },
  { immediate: true }
);

watch(
  currentAttentionUrl,
  async (url) => {
    const nextUrl = String(url || '').trim();
    if (!nextUrl) {
      displayedAttentionUrl.value = '';
      attentionFrameLoading.value = false;
      return;
    }
    const token = ++attentionLoadToken.value;
    attentionFrameLoading.value = true;
    const ok = await preloadImage(nextUrl);
    if (token !== attentionLoadToken.value) return;
    attentionFrameLoading.value = false;
    if (ok) {
      displayedAttentionUrl.value = nextUrl;
      return;
    }
    if (!failedAttentionUrls.value.includes(nextUrl)) {
      failedAttentionUrls.value = [...failedAttentionUrls.value, nextUrl];
    }
  },
  { immediate: true }
);

watch(
  () =>
    `${props.originalImage || ''}|${(props.originalMaps || [])
      .map((item) => `${item?.plane}:${item?.url || item?.path || item?.image || ''}`)
      .join('|')}`,
  () => {
    failedOriginalUrls.value = [];
    displayedOriginalUrl.value = '';
  }
);

watch(
  () =>
    `${props.attentionMap || ''}|${(props.attentionMaps || [])
      .map((item) => `${item?.plane}:${item?.url || item?.path || item?.image || ''}`)
      .join('|')}`,
  () => {
    failedAttentionUrls.value = [];
    displayedAttentionUrl.value = '';
  }
);

watch(
  resolvedAttentionSlides,
  (slides) => {
    if (!slides.length) {
      selectedSlideIndex.value = 0;
      return;
    }
    if (selectedSlideIndex.value >= slides.length) {
      selectedSlideIndex.value = slides.length - 1;
    }
  },
  { immediate: true }
);

watch(
  () =>
    resolvedAttentionSlides.value
      .map((slide) => `${slide.rank}:${slide.views.map((view) => `${view.plane}:${view.url}`).join('|')}`)
      .join('||'),
  () => {
    failedAttentionUrls.value = [];
  }
);

watch(
  () => fallbackOriginalMaps.value.length,
  (length) => {
    if (!length) {
      selectedOriginalViewIndex.value = 0;
      return;
    }
    if (selectedOriginalViewIndex.value >= length) {
      selectedOriginalViewIndex.value = length - 1;
    }
  },
  { immediate: true }
);

watch(
  () => attentionViews.value.length,
  (length) => {
    if (!length) {
      selectedViewIndex.value = 0;
      return;
    }
    if (selectedViewIndex.value >= length) {
      selectedViewIndex.value = length - 1;
    }
  },
  { immediate: true }
);

onBeforeUnmount(() => {
  if (originalSliceDebounceTimer.value) {
    clearTimeout(originalSliceDebounceTimer.value);
    originalSliceDebounceTimer.value = null;
  }
  if (attentionSliceDebounceTimer.value) {
    clearTimeout(attentionSliceDebounceTimer.value);
    attentionSliceDebounceTimer.value = null;
  }
});

const hasOriginalImage = (url: string) => Boolean(url && !failedOriginalUrls.value.includes(url));
const hasOriginal = computed(
  () => Boolean(displayedOriginalUrl.value) && hasOriginalImage(displayedOriginalUrl.value)
);
const hasAttentionImage = computed(
  () => Boolean(displayedAttentionUrl.value) && !failedAttentionUrls.value.includes(displayedAttentionUrl.value)
);
const hasAttentionViewPaging = computed(() => attentionViews.value.length > 1);
const hasOriginalViewPaging = computed(() => fallbackOriginalMaps.value.length > 1);
const attentionViewPositionLabel = computed(() => {
  const total = attentionViews.value.length || fallbackOriginalMaps.value.length;
  if (!total) return '0 / 0';
  const current = Math.min(Math.max(selectedViewIndex.value, 0), total - 1) + 1;
  return `${current} / ${total}`;
});
const originalViewPositionLabel = computed(() => {
  const total = fallbackOriginalMaps.value.length;
  if (!total) return '0 / 0';
  const current = Math.min(Math.max(selectedOriginalViewIndex.value, 0), total - 1) + 1;
  return `${current} / ${total}`;
});

const handleOriginalError = (url: string) => {
  if (!url) return;
  if (!failedOriginalUrls.value.includes(url)) {
    failedOriginalUrls.value = [...failedOriginalUrls.value, url];
  }
};

const handleAttentionError = (url: string) => {
  if (!url) return;
  if (!failedAttentionUrls.value.includes(url)) {
    failedAttentionUrls.value = [...failedAttentionUrls.value, url];
  }
};

const goPrevAttentionView = () => {
  const total = attentionViews.value.length;
  if (!total) return;
  const targetIndex = (selectedViewIndex.value - 1 + total) % total;
  prefetchViewByIndex(
    attentionViews.value,
    targetIndex,
    props.attentionSliceSliderEnabled,
    debouncedAttentionSliceIndexPercent.value
  );
  selectedViewIndex.value = targetIndex;
  syncOriginalViewToAttention();
};

const goNextAttentionView = () => {
  const total = attentionViews.value.length;
  if (!total) return;
  const targetIndex = (selectedViewIndex.value + 1) % total;
  prefetchViewByIndex(
    attentionViews.value,
    targetIndex,
    props.attentionSliceSliderEnabled,
    debouncedAttentionSliceIndexPercent.value
  );
  selectedViewIndex.value = targetIndex;
  syncOriginalViewToAttention();
};

const goPrevOriginalView = () => {
  const total = fallbackOriginalMaps.value.length;
  if (!total) return;
  const targetIndex = (selectedOriginalViewIndex.value - 1 + total) % total;
  prefetchViewByIndex(
    fallbackOriginalMaps.value,
    targetIndex,
    props.originalSliceSliderEnabled,
    debouncedOriginalSliceIndexPercent.value
  );
  selectedOriginalViewIndex.value = targetIndex;
  syncAttentionViewToOriginal();
};

const goNextOriginalView = () => {
  const total = fallbackOriginalMaps.value.length;
  if (!total) return;
  const targetIndex = (selectedOriginalViewIndex.value + 1) % total;
  prefetchViewByIndex(
    fallbackOriginalMaps.value,
    targetIndex,
    props.originalSliceSliderEnabled,
    debouncedOriginalSliceIndexPercent.value
  );
  selectedOriginalViewIndex.value = targetIndex;
  syncAttentionViewToOriginal();
};
</script>

<template>
  <div class="mri-image-display">
    <div class="image-grid">
      <div class="image-card">
        <h4 class="image-label">원본 MRI</h4>
        <div class="image-container">
          <div v-if="loading" class="image-placeholder loading">
            <div class="spinner"></div>
            <span>MRI 이미지 로딩 중...</span>
          </div>
          <template v-else-if="hasOriginal && currentOriginalView">
            <img
              :src="displayedOriginalUrl"
              :alt="`Original MRI ${currentOriginalView.label}`"
              loading="lazy"
              @error="handleOriginalError(displayedOriginalUrl)"
            />
            <div v-if="originalFrameLoading" class="image-loading-overlay">
              <div class="spinner spinner-sm"></div>
            </div>
          </template>
          <div v-else-if="originalFrameLoading" class="image-placeholder loading">
            <div class="spinner"></div>
            <span>MRI 이미지 로딩 중...</span>
          </div>
          <div v-else class="image-placeholder">
            <span>MRI 이미지가 없습니다</span>
          </div>
        </div>
        <div class="attention-view-pager original-view-pager">
          <button
            type="button"
            class="pager-button"
            aria-label="원본 이전 단면"
            :disabled="!hasOriginalViewPaging"
            @click="goPrevOriginalView"
          >
            ‹
          </button>
          <div class="attention-view-meta original-view-meta">
            <strong>{{ currentOriginalView?.label || currentAttentionView?.label || 'Axial' }}</strong>
            <span class="pager-index">{{ originalViewPositionLabel }}</span>
          </div>
          <button
            type="button"
            class="pager-button"
            aria-label="원본 다음 단면"
            :disabled="!hasOriginalViewPaging"
            @click="goNextOriginalView"
          >
            ›
          </button>
        </div>
        <div v-if="originalSliceSliderEnabled" class="slice-slider-row">
          <input
            id="mri-slice-index-original"
            v-model.number="originalSliceIndexPercent"
            class="slice-slider-input"
            type="range"
            min="0"
            max="100"
            step="1"
          />
          <span class="slice-slider-value">{{ originalSliceIndexPercent }}%</span>
        </div>
      </div>

      <div class="image-card attention-card">
        <h4 class="image-label">Attention Map</h4>
        <div class="image-container view-container">
          <div v-if="loading" class="image-placeholder loading">
            <div class="spinner"></div>
          </div>
          <template v-else-if="currentAttentionView && hasAttentionImage">
            <img
              :class="{
                'attention-rotated': true
              }"
              :src="displayedAttentionUrl"
              :alt="`Attention Map ${currentAttentionView.label}`"
              loading="lazy"
              @error="handleAttentionError(displayedAttentionUrl)"
            />
            <div v-if="attentionFrameLoading" class="image-loading-overlay">
              <div class="spinner spinner-sm"></div>
            </div>
          </template>
          <div v-else-if="attentionFrameLoading" class="image-placeholder loading">
            <div class="spinner"></div>
          </div>
          <div v-else class="image-placeholder">
            <span>Attention Map이 없습니다</span>
          </div>
        </div>
        <div class="attention-view-pager">
          <button
            type="button"
            class="pager-button"
            aria-label="이전 단면"
            :disabled="!hasAttentionViewPaging"
            @click="goPrevAttentionView"
          >
            ‹
          </button>
          <div class="attention-view-meta">
            <strong>{{ currentAttentionView?.label || 'Axial' }}</strong>
            <span class="pager-index">{{ attentionViewPositionLabel }}</span>
          </div>
          <button
            type="button"
            class="pager-button"
            aria-label="다음 단면"
            :disabled="!hasAttentionViewPaging"
            @click="goNextAttentionView"
          >
            ›
          </button>
        </div>
        <div v-if="attentionSliceSliderEnabled" class="slice-slider-row">
          <input
            id="mri-slice-index-attention"
            v-model.number="attentionSliceIndexPercent"
            class="slice-slider-input"
            type="range"
            min="0"
            max="100"
            step="1"
          />
          <span class="slice-slider-value">{{ attentionSliceIndexPercent }}%</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mri-image-display {
  width: 100%;
}

.image-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.image-card {
  background: #ffffff;
  border-radius: 20px;
  padding: 16px;
  box-shadow: inset 4px 4px 10px rgba(209, 217, 230, 0.6), inset -4px -4px 10px #ffffff;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.attention-card {
  gap: 10px;
}

.image-label {
  font-size: 16px;
  font-weight: 800;
  color: #777;
  margin: 0;
}

.image-container {
  position: relative;
  aspect-ratio: 1 / 1;
  border-radius: 16px;
  overflow: hidden;
  background: #1f2428;
  display: flex;
  align-items: center;
  justify-content: center;
}

.view-container {
  aspect-ratio: 1 / 1;
  border-radius: 14px;
}

.image-container img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.image-container img.original-rotated-180 {
  transform: rotate(180deg);
  transform-origin: center center;
}

.view-container img.attention-rotated {
  transform: rotate(-90deg);
  transform-origin: center center;
}

.image-loading-overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    rgba(31, 36, 40, 0.0) 0%,
    rgba(31, 36, 40, 0.18) 50%,
    rgba(31, 36, 40, 0.0) 100%
  );
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.image-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: #e5e7eb;
  color: #666;
  font-weight: 700;
  font-size: 12px;
  text-align: center;
  padding: 10px;
}

.image-placeholder.loading {
  background: #f0f3f6;
}

.attention-view-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.attention-view-meta {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.attention-view-meta strong {
  font-size: 14px;
  color: #6b7280;
  font-weight: 800;
}

.pager-button {
  width: 34px;
  height: 34px;
  border: 1px solid #d7dde4;
  border-radius: 999px;
  background: #fff;
  color: #4a5563;
  font-size: 22px;
  font-weight: 700;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.pager-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.pager-index {
  font-size: 12px;
  font-weight: 700;
  color: #9aa3ad;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #e5e7eb;
  border-top-color: #4cb7b7;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.spinner-sm {
  width: 20px;
  height: 20px;
  border-width: 2px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 900px) {
  .image-grid {
    grid-template-columns: 1fr;
  }
}

.slice-slider-row {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: #e9f7f7;
  border: 1px solid #bfe3e3;
  display: flex;
  align-items: center;
  gap: 10px;
}

.slice-slider-label {
  font-size: 12px;
  font-weight: 800;
  color: #6b7280;
  min-width: 40px;
}

.slice-slider-input {
  flex: 1;
  min-width: 0;
  accent-color: #4cb7b7;
  height: 8px;
}

.slice-slider-input::-webkit-slider-runnable-track {
  height: 8px;
  border-radius: 999px;
  background: #d9ecec;
}

.slice-slider-input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  margin-top: -5px;
  border-radius: 999px;
  border: 2px solid #ffffff;
  background: #3db1b1;
  box-shadow: 0 2px 8px rgba(61, 177, 177, 0.35);
}

.slice-slider-input::-moz-range-track {
  height: 8px;
  border-radius: 999px;
  background: #d9ecec;
  border: none;
}

.slice-slider-input::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 999px;
  border: 2px solid #ffffff;
  background: #3db1b1;
  box-shadow: 0 2px 8px rgba(61, 177, 177, 0.35);
}

.slice-slider-value {
  font-size: 12px;
  font-weight: 800;
  color: #2f8488;
  min-width: 44px;
  text-align: right;
}
</style>
