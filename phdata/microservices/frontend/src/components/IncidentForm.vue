<script setup>
import { computed, ref, useId, watch } from 'vue'
import ModelPicker from './ModelPicker.vue'

const props = defineProps({
  busy: Boolean,
  error: { type: String, default: '' },
  models: { type: Object, default: () => ({ allowed_models: [], default_model: '' }) },
  defaultModel: { type: String, default: '' },
})
const emit = defineEmits(['submit', 'cancel'])
const id = useId()
const title = ref('')
const description = ref('')
const model = ref('')
const allowedModels = computed(() => [...new Set((props.models?.allowed_models || []).filter(value => typeof value === 'string' && value.trim()))])
watch([allowedModels, () => props.defaultModel, () => props.models?.default_model], () => {
  if (!allowedModels.value.includes(model.value)) {
    model.value = [props.defaultModel, props.models?.default_model, ...allowedModels.value]
      .find(value => allowedModels.value.includes(value)) || ''
  }
}, { immediate: true })
const canSubmit = computed(() => !props.busy && title.value.trim().length > 0 && title.value.length <= 200 && description.value.trim().length > 0 && description.value.length <= 50000 && allowedModels.value.includes(model.value))
function submit() {
  if (canSubmit.value) {
    emit('submit', { title: title.value.trim(), description: description.value.trim(), model: model.value })
  }
}
</script>

<template>
  <section class="incident-form-view" :aria-labelledby="`${id}-heading`">
    <div class="form-card">
      <div class="form-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 8v8m-4-4h8"/><rect x="3" y="3" width="18" height="18" rx="5"/></svg></div>
      <p class="eyebrow">LET’S GET IT SORTED</p>
      <h1 :id="`${id}-heading`">New incident</h1>
      <p class="intro">Tell us what’s happening. Your assistant will help you find the next step.</p>
      <form :aria-busy="busy" @submit.prevent="submit">
        <p v-if="error" :id="`${id}-error`" class="error" role="alert">{{ error }}</p>
        <fieldset :disabled="busy">
          <div class="label-row"><label :for="`${id}-title`">Issue title <span>(required)</span></label><span class="count">{{ title.length }}/200</span></div>
          <input :id="`${id}-title`" v-model="title" name="title" type="text" maxlength="200" required placeholder="e.g. Receipt printer is not responding" :aria-describedby="`${id}-title-help`" />
          <p :id="`${id}-title-help`" class="hint">A short summary so it’s easy to find later.</p>
          <div class="label-row description-label"><label :for="`${id}-description`">What’s happening? <span>(required)</span></label></div>
          <textarea :id="`${id}-description`" v-model="description" name="description" rows="6" maxlength="50000" required placeholder="Describe the issue, when it started, and anything you’ve already tried." :aria-describedby="`${id}-description-help`"></textarea>
          <div :id="`${id}-description-help`" class="description-help"><span>Include any error messages. Leave out passwords or payment details.</span><span class="count">{{ description.length.toLocaleString() }}/50,000</span></div>
          <div class="model-section"><ModelPicker v-model="model" :models="allowedModels" :busy="busy" /></div>
        </fieldset>
        <p v-if="!allowedModels.length" class="hint" role="status">Assistant models are not available yet. Please try again shortly.</p>
        <div class="actions">
          <button type="button" class="cancel" :disabled="busy" @click="emit('cancel')">Cancel</button>
          <button type="submit" class="submit" :disabled="!canSubmit">{{ busy ? 'Creating incident…' : 'Create incident' }}<svg v-if="!busy" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6"/></svg></button>
        </div>
        <p class="progress" role="status">{{ busy ? 'Setting up your incident…' : '' }}</p>
      </form>
    </div>
  </section>
</template>

<style scoped>
.incident-form-view {
  --bg: #f5f7fa; --surface: #fff; --ink: #152d35; --muted: #667a82; --line: #e2e8ed; --accent: #087f8c; --accent-soft: #e6f5f4; --danger: #c44242;
  box-sizing: border-box; min-width: 0; width: 100%; padding: clamp(24px, 5vw, 64px) 24px; background: var(--bg); color: var(--ink); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.incident-form-view * { box-sizing: border-box; }
.form-card { max-width: 680px; margin: 0 auto; padding: clamp(20px, 4vw, 40px); border: 1px solid var(--line); border-radius: 18px; background: var(--surface); box-shadow: 0 8px 32px #152d3505; }
.form-icon { display: grid; place-items: center; width: 44px; height: 44px; border-radius: 12px; background: var(--accent-soft); color: var(--accent); }
.form-icon svg { width: 25px; height: 25px; }
.eyebrow { margin: 24px 0 8px; font-size: 10px; font-weight: 750; letter-spacing: 1.6px; color: var(--accent); }
h1 { margin: 0; font-size: 29px; font-weight: 650; letter-spacing: -.8px; }
.intro { margin: 12px 0 30px; color: var(--muted); font-size: 14px; line-height: 1.7; }
fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.label-row { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 9px; }
label { font-size: 13px; font-weight: 650; }
label span { color: var(--muted); font-size: 11px; font-weight: 400; }
.count { flex-shrink: 0; color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
input, textarea { display: block; width: 100%; padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); font: inherit; font-size: 16px; line-height: 1.6; }
input { min-height: 48px; }
textarea { min-height: 160px; resize: vertical; }
input::placeholder, textarea::placeholder { color: var(--muted); font-size: 13px; }
.hint, .description-help { margin: 8px 0 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.description-label { margin-top: 25px; }
.description-help { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 6px 14px; }
.model-section { margin-top: 28px; padding-top: 24px; border-top: 1px solid var(--line); }
.actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 12px; margin-top: 30px; }
button { display: inline-flex; align-items: center; justify-content: center; gap: 12px; min-height: 46px; padding: 12px 20px; border: 1px solid var(--line); border-radius: 10px; font: inherit; font-size: 13px; font-weight: 650; cursor: pointer; }
.cancel { color: var(--ink); background: var(--surface); }
.cancel:hover:not(:disabled) { background: var(--bg); }
.submit { border-color: var(--accent); color: white; background: var(--accent); }
.submit:hover:not(:disabled) { background: #076d78; }
.submit svg { width: 17px; height: 17px; }
input:focus-visible, textarea:focus-visible, button:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
button:disabled, fieldset:disabled { opacity: .65; }
button:disabled { cursor: not-allowed; }
.error { padding: 12px 14px; border: 1px solid #f1cece; border-radius: 9px; color: var(--danger); background: #fff0f0; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.progress { min-height: 18px; margin: 10px 0 0; text-align: right; color: var(--muted); font-size: 12px; }
@media (max-width: 420px) { .incident-form-view { padding: 20px 12px; } .form-card { padding: 20px 16px; } .actions { flex-direction: column-reverse; } button { width: 100%; } }
</style>
