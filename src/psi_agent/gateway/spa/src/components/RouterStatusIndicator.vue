<template>
  <div
    class="router-status"
    role="status"
    aria-live="polite"
    aria-atomic="true"
  >
    <span class="status-icon material-symbols-outlined" aria-hidden="true">
      {{ presentation.icon }}
    </span>

    <span class="status-copy">
      <span class="status-heading">
        <span class="status-label">{{ presentation.label }}</span>
        <span v-if="presentation.badge" class="status-chip">
          {{ presentation.badge }}
        </span>
        <span v-if="presentation.nested" class="status-chip nested-chip">
          嵌套
        </span>
      </span>
      <span class="status-message">{{ presentation.message }}</span>
    </span>

    <span class="activity-dots" aria-hidden="true">
      <span></span>
      <span></span>
      <span></span>
    </span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { describeRouterStatus } from '../routerStatus.js'

const props = defineProps({
  status: {
    type: Object,
    required: true,
  },
})

const presentation = computed(() => describeRouterStatus(props.status))
</script>

<style scoped>
.router-status {
  display: flex;
  align-items: center;
  gap: 10px;
  width: min(100%, 520px);
  max-width: 100%;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--md-primary) 28%, var(--md-outline-variant));
  border-radius: var(--md-shape-medium);
  background: color-mix(in srgb, var(--md-primary) 7%, var(--md-surface-container-high));
  color: var(--md-text-primary);
  box-shadow: var(--md-elevation-1);
}

.status-icon {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--md-shape-small);
  background: color-mix(in srgb, var(--md-primary) 14%, transparent);
  color: var(--md-primary);
  font-size: 20px;
}

.status-copy {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.status-heading {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.status-label {
  overflow: hidden;
  color: var(--md-text-primary);
  font-size: 13px;
  font-weight: 650;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-chip {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  min-height: 18px;
  padding: 1px 7px;
  border-radius: var(--md-shape-full);
  background: color-mix(in srgb, var(--md-primary) 13%, transparent);
  color: var(--md-primary);
  font-size: 10px;
  font-weight: 600;
  line-height: 1.25;
}

.nested-chip {
  border: 1px solid color-mix(in srgb, var(--md-outline) 55%, transparent);
  background: transparent;
  color: var(--md-text-secondary);
}

.status-message {
  overflow: hidden;
  color: var(--md-text-secondary);
  font-size: 12px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-dots {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  padding-inline: 2px;
}

.activity-dots span {
  width: 5px;
  height: 5px;
  border-radius: var(--md-shape-full);
  background: var(--g-spark);
  animation: router-status-pulse 1.4s ease-in-out infinite;
}

.activity-dots span:nth-child(2) {
  animation-delay: 0.18s;
}

.activity-dots span:nth-child(3) {
  animation-delay: 0.36s;
}

@keyframes router-status-pulse {
  0%, 100% {
    opacity: 0.42;
    transform: scale(0.82);
  }

  50% {
    opacity: 1;
    transform: scale(1);
  }
}

@media (max-width: 768px) {
  .router-status {
    width: 100%;
    padding: 9px 10px;
  }

  .status-heading {
    flex-wrap: wrap;
    row-gap: 3px;
  }

  .status-message {
    overflow: visible;
    text-overflow: clip;
    white-space: normal;
  }
}

@media (prefers-reduced-motion: reduce) {
  .activity-dots span {
    animation: none;
    opacity: 0.72;
    transform: none;
  }
}
</style>
