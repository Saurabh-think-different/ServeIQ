<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, ref } from 'vue'
import SupportWorkspace from './SupportWorkspace.vue'

const path = ref(window.location.pathname)
const isEvaluations = computed(() => path.value.replace(/\/+$/, '') === '/evals')
const EvalDashboard = defineAsyncComponent(() => import('./components/EvalDashboard.vue'))
function navigate() { path.value = window.location.pathname }
window.addEventListener('popstate', navigate)
onBeforeUnmount(() => window.removeEventListener('popstate', navigate))
</script>

<template>
  <EvalDashboard v-if="isEvaluations" />
  <SupportWorkspace v-else />
</template>
