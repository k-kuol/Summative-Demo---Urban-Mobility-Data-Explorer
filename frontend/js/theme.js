/**
 * theme.js - the light/dark toggle in the header.
 *
 * With no choice saved, the page follows the operating system (CSS
 * prefers-color-scheme). Clicking the button sets data-theme on <html>,
 * which the stylesheet's :root[data-theme] rules pick up, and remembers the
 * choice. Charts use CSS variables, so they recolour without a redraw.
 * The saved choice is applied by a tiny inline script in index.html before
 * first paint, so the page never flashes the wrong theme.
 */

const STORAGE_KEY = "theme";
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

function currentTheme() {
  return document.documentElement.dataset.theme || (systemDark.matches ? "dark" : "light");
}

function save(theme) {
  try { localStorage.setItem(STORAGE_KEY, theme); } catch { /* private mode: just don't remember */ }
}

export function initThemeToggle(button) {
  const render = () => {
    const dark = currentTheme() === "dark";
    button.setAttribute("aria-pressed", String(dark));
    button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    button.querySelector(".theme-label").textContent = dark ? "Light" : "Dark";
    button.querySelector(".theme-icon").textContent = dark ? "☀" : "☾";
  };

  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    save(next);
    render();
  });
  // If the visitor never chose, keep the label in step with the OS setting.
  systemDark.addEventListener("change", render);
  render();
}
