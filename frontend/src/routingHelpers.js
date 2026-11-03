export const formatDetectedAt = (timestamp) => {
  if (!timestamp) {
    return null;
  }

  const date = new Date(timestamp);

  if (Number.isNaN(date.getTime())) {
    return "Date unavailable";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
};

export const statusLabels = {
  monitoring: "Monitoring",
  stopped: "Stopped",
  error: "🔴 Monitoring Error",
  login_required: "Login Required",
};

export const loginStatusLabels = {
  not_configured: "Login not configured",
  connected: "Login connected",
  expired: "Login expired",
  failed: "Login failed",
  needs_attention: "Login needs attention",
};

export const hasRecentChange = (website) => {
  if (website.status !== "monitoring" || !website.last_changed) {
    return false;
  }

  const changedAt = new Date(website.last_changed).getTime();
  const freshnessWindow = Number(website.check_interval) * 60_000;
  const age = Date.now() - changedAt;

  return Number.isFinite(changedAt) && age >= 0 && age <= freshnessWindow;
};

export const accountPageFromRoute = (route) => ({
  "/websites": "dashboard",
  "/alerts": "alerts",
  "/profile": "profile",
  "/settings": "settings",
  "/history": "history",
  "/activity-history": "history",
}[route] || "dashboard");

export const routeFromAccountPage = (page) => page === "history"
  ? "/activity-history"
  : page === "dashboard" ? "/dashboard" : `/${page}`;

export function normalizeAppRoute(path, hasToken) {
  if (path === "/developer/login") return path;
  if (path === "/developer") return hasToken ? path : "/developer/login";
  const authenticatedRoutes = ["/dashboard", "/websites", "/alerts", "/profile", "/settings", "/history", "/activity-history"];
  const publicRoute = path === "/login" || path === "/register"
    || path === "/forgot-password" || path.startsWith("/reset-password/");
  return hasToken ? (authenticatedRoutes.includes(path) ? path : "/dashboard") : (publicRoute ? path : "/login");
}
