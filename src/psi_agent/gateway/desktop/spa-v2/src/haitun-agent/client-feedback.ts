export function prefersReducedMotion() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function mobileHaptic(pattern: number | number[]) {
  if (typeof window === "undefined" || prefersReducedMotion()) return;
  if (document.documentElement.dataset.haptics === "off") return;
  if (window.matchMedia("(pointer: coarse)").matches && "vibrate" in navigator) {
    navigator.vibrate(pattern);
  }
}
