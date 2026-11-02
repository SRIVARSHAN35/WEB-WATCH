import assert from "node:assert/strict";
import test from "node:test";

import { clearStoredAuthState, requestAccountDeletion } from "../src/authState.js";

class MemoryStorage {
  values = new Map();
  removeItem(key) { this.values.delete(key); }
}

test("successful account deletion clears the token and only that user's cached theme", async () => {
  const storage = {
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
  };
  storage.sessionStorage.values.set("webwatch_auth_token", "token");
  storage.sessionStorage.values.set("webwatch_theme_user_id", "42");
  storage.localStorage.values.set("webwatch_theme_preference_42", "dark");
  storage.localStorage.values.set("webwatch_theme_preference_43", "light");

  let request;
  let loggedOut = false;
  await requestAccountDeletion(async (...args) => {
    request = args;
    return { ok: true, status: 204, json: async () => { throw new Error("No content"); } };
  }, () => {
    clearStoredAuthState(storage, 42);
    loggedOut = true;
  });

  assert.deepEqual(request, ["/auth/delete-account/", { method: "DELETE", body: JSON.stringify({ confirmation: "DELETE" }) }]);
  assert.equal(loggedOut, true);
  assert.equal(storage.sessionStorage.values.has("webwatch_auth_token"), false);
  assert.equal(storage.sessionStorage.values.has("webwatch_theme_user_id"), false);
  assert.equal(storage.localStorage.values.has("webwatch_theme_preference_42"), false);
  assert.equal(storage.localStorage.values.get("webwatch_theme_preference_43"), "light");
});

test("failed account deletion preserves authentication state", async () => {
  const storage = { sessionStorage: new MemoryStorage(), localStorage: new MemoryStorage() };
  storage.sessionStorage.values.set("webwatch_auth_token", "token");
  let loggedOut = false;

  await assert.rejects(
    requestAccountDeletion(async () => ({ ok: false, json: async () => ({ detail: "Deletion failed." }) }), () => {
      clearStoredAuthState(storage, 42);
      loggedOut = true;
    }),
    /Deletion failed\./,
  );

  assert.equal(loggedOut, false);
  assert.equal(storage.sessionStorage.values.get("webwatch_auth_token"), "token");
});
