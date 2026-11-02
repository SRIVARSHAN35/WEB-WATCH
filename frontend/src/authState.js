const TOKEN_KEY = "webwatch_auth_token";
const THEME_USER_KEY = "webwatch_theme_user_id";

export function clearStoredAuthState(storage, userId = null) {
  storage.sessionStorage.removeItem(TOKEN_KEY);
  storage.sessionStorage.removeItem(THEME_USER_KEY);
  if (userId !== null && userId !== undefined) {
    storage.localStorage.removeItem(`webwatch_theme_preference_${userId}`);
  }
}

export async function requestAccountDeletion(apiFetch, onDeleted) {
  const response = await apiFetch("/auth/delete-account/", {
    method: "DELETE",
    body: JSON.stringify({ confirmation: "DELETE" }),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    // A successful DELETE may correctly return 204 with no response body.
  }
  if (!response.ok) {
    throw new Error(data.detail || "Your account could not be deleted. Please try again.");
  }
  onDeleted();
}
