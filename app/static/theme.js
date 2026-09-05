/* Apply the saved palette before styles paint; no wallet or server state involved. */
(() => {
  "use strict";
  const key = "gonka-flow-theme";
  const root = document.documentElement;
  const system = window.matchMedia("(prefers-color-scheme: light)");
  const valid = value => value === "light" || value === "dark";
  let preference = null;
  try { preference = localStorage.getItem(key); } catch {}
  if (!valid(preference)) preference = null;

  function apply() {
    const theme = preference || (system.matches ? "light" : "dark");
    root.dataset.theme = theme;
    const toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", String(theme === "light"));
      toggle.title = theme === "light" ? "Включить тёмную тему" : "Включить светлую тему";
    }
  }
  apply();
  document.addEventListener("DOMContentLoaded", () => {
    const toggle = document.getElementById("theme-toggle");
    if (toggle) toggle.addEventListener("click", () => {
      preference = root.dataset.theme === "light" ? "dark" : "light";
      try { localStorage.setItem(key, preference); } catch {}
      apply();
    });
    apply();
  }, { once: true });
  system.addEventListener("change", () => { if (!preference) apply(); });
  window.addEventListener("storage", event => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : null;
    apply();
  });
})();
