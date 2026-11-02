export function applyTheme(theme, userId) {
  const selected = ["light", "dark", "system"].includes(theme) ? theme : "system";
  document.documentElement.dataset.theme = selected;
  if (userId != null) {
    window.localStorage.setItem(`webwatch_theme_preference_${userId}`, selected);
    window.sessionStorage.setItem("webwatch_theme_user_id", String(userId));
  }
}
