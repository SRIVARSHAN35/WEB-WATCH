import test from "node:test";
import assert from "node:assert/strict";
import {
  accountPageFromRoute,
  formatDetectedAt,
  hasRecentChange,
  normalizeAppRoute,
  routeFromAccountPage,
} from "../src/routingHelpers.js";

test("unauthenticated routes redirect non-public paths to login", () => {
  assert.equal(normalizeAppRoute("/dashboard", false), "/login");
  assert.equal(normalizeAppRoute("/alerts", false), "/login");
  assert.equal(normalizeAppRoute("/settings", false), "/login");
  assert.equal(normalizeAppRoute("/unknown", false), "/login");
  assert.equal(normalizeAppRoute("/login", false), "/login");
  assert.equal(normalizeAppRoute("/register", false), "/register");
  assert.equal(normalizeAppRoute("/forgot-password", false), "/forgot-password");
  assert.equal(normalizeAppRoute("/reset-password/abc123token", false), "/reset-password/abc123token");
});

test("authenticated routes redirect public login/register paths to dashboard", () => {
  assert.equal(normalizeAppRoute("/login", true), "/dashboard");
  assert.equal(normalizeAppRoute("/register", true), "/dashboard");
  assert.equal(normalizeAppRoute("/forgot-password", true), "/dashboard");
  assert.equal(normalizeAppRoute("/dashboard", true), "/dashboard");
  assert.equal(normalizeAppRoute("/websites", true), "/websites");
  assert.equal(normalizeAppRoute("/alerts", true), "/alerts");
  assert.equal(normalizeAppRoute("/settings", true), "/settings");
  assert.equal(normalizeAppRoute("/activity-history", true), "/activity-history");
});

test("account page and route conversions map bidirectionally", () => {
  assert.equal(accountPageFromRoute("/websites"), "dashboard");
  assert.equal(accountPageFromRoute("/alerts"), "alerts");
  assert.equal(accountPageFromRoute("/profile"), "profile");
  assert.equal(accountPageFromRoute("/settings"), "settings");
  assert.equal(accountPageFromRoute("/history"), "history");
  assert.equal(accountPageFromRoute("/activity-history"), "history");

  assert.equal(routeFromAccountPage("dashboard"), "/dashboard");
  assert.equal(routeFromAccountPage("alerts"), "/alerts");
  assert.equal(routeFromAccountPage("profile"), "/profile");
  assert.equal(routeFromAccountPage("settings"), "/settings");
  assert.equal(routeFromAccountPage("history"), "/activity-history");
});

test("formatDetectedAt formats valid timestamps and rejects null", () => {
  assert.equal(formatDetectedAt(null), null);
  assert.equal(formatDetectedAt(undefined), null);
  assert.equal(formatDetectedAt("not-a-date"), "Date unavailable");
  const formatted = formatDetectedAt("2026-10-28T10:00:00Z");
  assert.match(formatted, /2026/);
});

test("hasRecentChange accurately respects monitoring status and freshness interval", () => {
  const now = Date.now();
  const activeRecent = {
    status: "monitoring",
    last_changed: new Date(now - 5 * 60_000).toISOString(),
    check_interval: 15,
  };
  assert.equal(hasRecentChange(activeRecent), true);

  const activeStale = {
    status: "monitoring",
    last_changed: new Date(now - 20 * 60_000).toISOString(),
    check_interval: 15,
  };
  assert.equal(hasRecentChange(activeStale), false);

  const stoppedRecent = {
    status: "stopped",
    last_changed: new Date(now - 5 * 60_000).toISOString(),
    check_interval: 15,
  };
  assert.equal(hasRecentChange(stoppedRecent), false);
});
