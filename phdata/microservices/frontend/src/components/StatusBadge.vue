<script setup>
import { computed } from 'vue'

const props = defineProps({ status: { type: String, default: 'open' } })
const labels = {
  open: 'Open',
  running: 'Running',
  waiting_for_user: 'Waiting for you',
  resolved: 'Resolved',
  escalated: 'Escalated',
  failed: 'Failed',
}
const knownStatus = computed(() => Object.hasOwn(labels, props.status) ? props.status : 'unknown')
const label = computed(() => labels[knownStatus.value] || 'Unknown')
</script>

<template>
  <span class="status-badge" :class="`status-${knownStatus}`">
    <span class="status-dot" aria-hidden="true"></span>{{ label }}
  </span>
</template>

<style scoped>
.status-badge {
  --accent: #087f8c;
  --accent-soft: #e6f5f4;
  --muted: #667a82;
  --danger: #c44242;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 4px 8px;
  border-radius: 999px;
  background: #eef2f5;
  color: #4c626e;
  font: 600 11px/1.35 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  white-space: nowrap;
}
.status-dot { width: 6px; height: 6px; flex: 0 0 6px; border-radius: 50%; background: currentColor; }
.status-running, .status-resolved { color: #076d65; background: var(--accent-soft); }
.status-waiting_for_user { color: #805713; background: #fff4d9; }
.status-escalated { color: #76529a; background: #f2ecfa; }
.status-failed { color: #a63333; background: #fcecec; }
.status-running .status-dot { animation: pulse 1.8s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .4; } }
@media (prefers-reduced-motion: reduce) { .status-running .status-dot { animation: none; } }
</style>
