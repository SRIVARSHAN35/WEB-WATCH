import test from "node:test";
import assert from "node:assert/strict";

import { resolveApiBaseUrl } from "../src/apiConfig.js";

test("uses loopback API locally and same-origin API for a remote frontend", () => {
  assert.equal(
    resolveApiBaseUrl("", {
      hostname: "localhost",
      protocol: "http:",
      origin: "http://localhost:5173",
    }),
    "http://127.0.0.1:8000/api",
  );
  assert.equal(
    resolveApiBaseUrl("", {
      hostname: "webwatch.frontend.example.test",
      protocol: "https:",
      origin: "https://webwatch.frontend.example.test",
    }),
    "https://webwatch.frontend.example.test/api",
  );
});

test("normalizes a configured remote backend URL to the API root", () => {
  assert.equal(
    resolveApiBaseUrl("https://api.example.test", {
      hostname: "webwatch.frontend.example.test",
      protocol: "https:",
    }),
    "https://api.example.test/api",
  );
  assert.equal(
    resolveApiBaseUrl("https://api.example.test/api/", {
      hostname: "webwatch.frontend.example.test",
      protocol: "https:",
    }),
    "https://api.example.test/api",
  );
});

test("refuses loopback and insecure API URLs for a remote HTTPS frontend", () => {
  const frontend = {
    hostname: "webwatch.frontend.example.test",
    protocol: "https:",
    origin: "https://webwatch.frontend.example.test",
  };
  const sameOriginApi = "https://webwatch.frontend.example.test/api";
  assert.equal(resolveApiBaseUrl("http://127.0.0.1:8000/api", frontend), sameOriginApi);
  assert.equal(resolveApiBaseUrl("http://backend.example.test/api", frontend), sameOriginApi);
});

test("rejects malformed URLs and URLs containing credentials", () => {
  const frontend = {
    hostname: "app.example.test",
    protocol: "https:",
    origin: "https://app.example.test",
  };
  const sameOriginApi = "https://app.example.test/api";
  assert.equal(resolveApiBaseUrl("not a URL", frontend), sameOriginApi);
  assert.equal(resolveApiBaseUrl("https://user:pass@backend.example.test", frontend), sameOriginApi);
});
