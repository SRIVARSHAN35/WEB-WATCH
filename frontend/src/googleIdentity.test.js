import assert from "node:assert/strict";
import test from "node:test";
import { buildGoogleIdentityConfig } from "./googleIdentityConfig.js";

test("Google Identity config enables browser-mediated button sign-in", () => {
  const callback = () => {};
  const config = buildGoogleIdentityConfig("test-web-client-id", callback);

  assert.equal(config.client_id, "test-web-client-id");
  assert.equal(config.callback, callback);
  assert.equal(config.use_fedcm_for_button, true);
});
