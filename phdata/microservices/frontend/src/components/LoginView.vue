<script setup>
import { ref, useId } from 'vue'

const props = defineProps({ busy: Boolean, error: { type: String, default: '' } })
const emit = defineEmits(['login'])
const id = useId()
const email = ref('')
const password = ref('')
const showPassword = ref(false)
function login() {
  if (!props.busy && email.value.trim() && password.value) {
    emit('login', { email: email.value.trim(), password: password.value })
  }
}
</script>

<template>
  <main class="login-view">
    <section class="brand-panel" aria-label="ServeIQ operations support">
      <div class="brand" aria-label="ServeIQ">
        <svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><rect width="32" height="32" rx="9" fill="currentColor"/><path d="M23 10H13a4 4 0 0 0 0 8h6a2 2 0 0 1 0 4H9" stroke="white" stroke-width="3" stroke-linecap="round"/></svg>
        Serve<span>IQ</span>
      </div>
      <div class="brand-copy">
        <p class="eyebrow">OPERATIONS SUPPORT</p>
        <h1>Keep your team<br />moving forward.</h1>
        <p>Clear answers. Guided troubleshooting. A little less downtime.</p>
        <div class="support-note"><span aria-hidden="true">✓</span> Built for the people behind every store.</div>
      </div>
      <p class="brand-footer">Your operations. In good hands.</p>
    </section>
    <section class="form-panel" aria-labelledby="login-heading">
      <div class="login-card">
        <p class="eyebrow">WELCOME TO SERVEIQ</p>
        <h2 id="login-heading">Good to see you.</h2>
        <p class="intro">Sign in with your store manager account to get started.</p>
        <form :aria-busy="busy" @submit.prevent="login">
          <div v-if="error" :id="`${id}-error`" class="error" role="alert">{{ error }}</div>
          <label :for="`${id}-email`">Email address</label>
          <input :id="`${id}-email`" v-model="email" type="email" name="email" autocomplete="username" inputmode="email" autocapitalize="none" :spellcheck="false" placeholder="Your manager email" maxlength="320" required :disabled="busy" :aria-describedby="error ? `${id}-error` : undefined" />
          <label :for="`${id}-password`">Password</label>
          <div class="password-field">
            <input :id="`${id}-password`" v-model="password" :type="showPassword ? 'text' : 'password'" name="password" autocomplete="current-password" maxlength="1024" required :disabled="busy" :aria-describedby="error ? `${id}-error` : undefined" />
            <button type="button" class="visibility" :aria-label="showPassword ? 'Hide password' : 'Show password'" :aria-pressed="showPassword" :aria-controls="`${id}-password`" :disabled="busy" @click="showPassword = !showPassword">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path v-if="showPassword" d="m3 3 18 18"/></svg>
            </button>
          </div>
          <button class="submit" type="submit" :disabled="busy">{{ busy ? 'Signing in…' : 'Sign in' }}<span v-if="!busy" aria-hidden="true">→</span></button>
          <p class="progress" role="status">{{ busy ? 'Checking your account…' : '' }}</p>
        </form>
        <p class="help">Need access? Contact your operations administrator.</p>
      </div>
    </section>
  </main>
</template>

<style scoped>
.login-view {
  --bg: #f5f7fa; --surface: #fff; --ink: #152d35; --muted: #667a82; --line: #e2e8ed; --accent: #087f8c; --accent-soft: #e6f5f4; --danger: #c44242;
  display: grid; grid-template-columns: minmax(300px, .9fr) minmax(0, 1.1fr); min-height: 100vh; min-height: 100dvh;
  color: var(--ink); background: var(--bg); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.login-view * { box-sizing: border-box; }
.brand-panel { display: flex; flex-direction: column; padding: clamp(28px, 5vw, 72px); background: var(--ink); color: white; }
.brand { display: inline-flex; align-items: center; align-self: flex-start; gap: 10px; color: white; text-decoration: none; font-size: 27px; font-weight: 750; letter-spacing: -1px; }
.brand > span { margin-left: -10px; color: #72d4ca; }
.brand svg { width: 36px; height: 36px; margin-right: 4px; color: var(--accent); }
.brand-copy { margin: auto 0; padding: 64px 0; max-width: 430px; }
.eyebrow { color: var(--accent); font-size: 11px; font-weight: 750; letter-spacing: 1.7px; }
.brand-copy .eyebrow { color: #72d4ca; }
h1 { margin: 20px 0; font-size: clamp(30px, 3.8vw, 52px); line-height: 1.15; font-weight: 650; letter-spacing: -1.5px; }
.brand-copy > p:last-of-type { color: #b5c9cf; font-size: 16px; line-height: 1.8; }
.support-note { display: flex; align-items: center; gap: 10px; margin-top: 32px; color: #d5e6e8; font-size: 12px; line-height: 1.6; }
.support-note span { color: #72d4ca; }
.brand-footer { color: #b5c9cf; font-size: 12px; }
.form-panel { display: grid; place-items: center; min-width: 0; padding: 40px 24px; }
.login-card { width: 100%; max-width: 390px; }
h2 { margin: 12px 0; font-size: 30px; font-weight: 650; letter-spacing: -.8px; }
.intro { margin: 0 0 32px; color: var(--muted); font-size: 14px; line-height: 1.7; }
label { display: block; margin: 20px 0 8px; font-size: 13px; font-weight: 650; }
input { width: 100%; min-height: 48px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); font: inherit; font-size: 16px; }
input::placeholder { color: var(--muted); font-size: 14px; }
.password-field { position: relative; }
.password-field input { padding-right: 52px; }
button { font: inherit; cursor: pointer; }
.visibility { position: absolute; right: 3px; top: 2px; width: 44px; height: 44px; border: 0; border-radius: 8px; color: var(--muted); background: transparent; }
.visibility svg { width: 20px; height: 20px; vertical-align: middle; }
.submit { display: flex; justify-content: center; align-items: center; gap: 16px; width: 100%; min-height: 48px; margin-top: 26px; border: 0; border-radius: 10px; background: var(--accent); color: white; font-size: 14px; font-weight: 650; }
.submit:hover:not(:disabled) { background: #076d78; }
input:focus-visible, button:focus-visible, a:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
:disabled { cursor: not-allowed; opacity: .65; }
.error { padding: 12px 14px; border: 1px solid #f1cece; border-radius: 9px; background: #fff0f0; color: var(--danger); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.progress { min-height: 18px; margin: 8px 0 0; color: var(--muted); font-size: 12px; }
.help { margin: 22px 0 0; padding-top: 22px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; line-height: 1.7; }
@media (max-width: 700px) {
  .login-view { grid-template-columns: minmax(0, 1fr); }
  .brand-panel { padding: 24px; }
  .brand-copy { padding: 30px 0 4px; }
  .brand-copy h1 { font-size: 30px; }
  .brand-copy > p:last-of-type, .support-note, .brand-footer { display: none; }
  .form-panel { padding: 32px 24px; }
}
</style>
