<script setup>
import { computed, useId } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  models: { type: Array, default: () => [] },
  locked: Boolean,
  busy: Boolean,
})
const emit = defineEmits(['update:modelValue'])
const id = useId()
const options = computed(() => [...new Set(props.models.filter(model => typeof model === 'string' && model.trim()))])
function selectModel(event) {
  if (!props.locked && !props.busy && options.value.includes(event.target.value)) {
    emit('update:modelValue', event.target.value)
  }
}
</script>

<template>
  <div class="model-picker">
    <span v-if="locked" :id="`${id}-label`" class="field-label">Assistant model</span>
    <label v-else :for="id" class="field-label">Assistant model</label>
    <div v-if="locked" class="model-pill" :aria-labelledby="`${id}-label`" :aria-describedby="`${id}-help`">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/></svg>
      <span>{{ modelValue || 'Model not available' }}</span>
      <span class="pinned">Pinned</span>
    </div>
    <select v-else :id="id" :value="modelValue" :disabled="busy || !options.length" :aria-describedby="`${id}-help`" @change="selectModel">
      <option value="" disabled>{{ options.length ? 'Choose a model' : 'No models available' }}</option>
      <option v-for="model in options" :key="model" :value="model">{{ model }}</option>
    </select>
    <p :id="`${id}-help`">{{ locked ? 'Pinned after the first run. The model cannot change between turns.' : 'Choose before the first run. The model stays pinned to this incident.' }}</p>
  </div>
</template>

<style scoped>
.model-picker {
  --surface: #fff; --ink: #152d35; --muted: #667a82; --line: #e2e8ed; --accent: #087f8c; --accent-soft: #e6f5f4;
  min-width: 0;
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.field-label { display: block; margin-bottom: 8px; font-size: 13px; font-weight: 650; }
select { box-sizing: border-box; width: 100%; min-height: 46px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); font: inherit; font-size: 14px; text-overflow: ellipsis; }
select:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
select:disabled { cursor: not-allowed; opacity: .65; }
.model-pill { display: flex; align-items: center; gap: 9px; padding: 12px; border: 1px solid #c9e8e5; border-radius: 10px; background: var(--accent-soft); font-size: 13px; }
.model-pill > span:first-of-type { min-width: 0; overflow-wrap: anywhere; }
svg { width: 17px; height: 17px; flex: 0 0 17px; color: var(--accent); }
.pinned { margin-left: auto; color: #076d65; font-size: 11px; font-weight: 650; }
p { margin: 8px 0 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
</style>
