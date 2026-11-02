import { resolveApiBaseUrl } from "./apiConfig.js";
import { clearStoredAuthState } from "./authState.js";

export const API_BASE_URL = resolveApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL,
  window.location,
);
const TOKEN_KEY = "webwatch_auth_token";
const THEME_USER_KEY = "webwatch_theme_user_id";

function safeRequestUrl(url) {
  try {
    const parsedUrl = new URL(url, window.location.origin);
    return `${parsedUrl.origin}${parsedUrl.pathname}`;
  } catch {
    return "unavailable";
  }
}

function logApiDiagnostic(category, url, status) {
  if (!import.meta.env.DEV) return;
  console.warn("[WebWatch API]", {
    category,
    url: safeRequestUrl(url),
    ...(status === undefined ? {} : { status }),
  });
}

function apiRequestError(message, category) {
  const error = new Error(message);
  error.name = "ApiRequestError";
  error.category = category;
  return error;
}

export function saveAuthToken(token) {
  window.sessionStorage.removeItem(THEME_USER_KEY);
  document.documentElement.dataset.theme = "system";
  window.sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken(userId = null) {
  clearStoredAuthState(window, userId);
  document.documentElement.dataset.theme = "system";
}

export function getAuthToken() {
  return window.sessionStorage.getItem(TOKEN_KEY);
}

export async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

export async function apiFetch(path, options = {}) {
  if (!API_BASE_URL) {
    logApiDiagnostic("api_url_not_configured", window.location.origin);
    throw apiRequestError(
      "Set VITE_API_BASE_URL to your backend's public HTTPS URL in frontend/.env.local, then restart Vite.",
      "configuration",
    );
  }

  const headers = new Headers(options.headers || {});
  const token = getAuthToken();

  if (token) {
    headers.set("Authorization", `Token ${token}`);
  }

  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const requestUrl = `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  let response;
  try {
    response = await fetch(requestUrl, {
      ...options,
      headers,
    });
  } catch {
    logApiDiagnostic("network_or_cors", requestUrl);
    throw apiRequestError(
      "Could not reach the WebWatch backend. Check that Django is running and the Vite /api proxy target is correct. If using a separate backend URL, check its address and CORS origin.",
      "network_or_cors",
    );
  }

  if (!response.ok) {
    const category = response.status === 404
      ? "api_route_not_found"
      : response.status >= 500
        ? "backend_error"
        : response.status === 401 || response.status === 403
          ? "authentication_or_permission_error"
          : response.status === 400
            ? "validation_error"
            : "http_error";
    logApiDiagnostic(category, requestUrl, response.status);
  }

  if (response.status === 401 && token) {
    window.dispatchEvent(new Event("webwatch:unauthorized"));
  }

  return response;
}
