const LOCAL_API_BASE_URL = "http://127.0.0.1:8000/api";
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

function sameOriginApiBase(frontendLocation) {
  if (LOOPBACK_HOSTS.has((frontendLocation.hostname || "").toLowerCase())) {
    return LOCAL_API_BASE_URL;
  }
  return frontendLocation.origin
    ? `${frontendLocation.origin.replace(/\/+$/, "")}/api`
    : "";
}

export function resolveApiBaseUrl(configuredUrl, frontendLocation = {}) {
  const frontendHostname = (frontendLocation.hostname || "localhost").toLowerCase();
  const frontendProtocol = frontendLocation.protocol || "http:";
  const isLocalFrontend = LOOPBACK_HOSTS.has(frontendHostname);
  const value = typeof configuredUrl === "string" ? configuredUrl.trim() : "";

  if (!value) {
    return sameOriginApiBase(frontendLocation);
  }

  let apiUrl;
  try {
    apiUrl = new URL(value);
  } catch {
    return sameOriginApiBase(frontendLocation);
  }

  if (
    !["http:", "https:"].includes(apiUrl.protocol)
    || apiUrl.username
    || apiUrl.password
  ) {
    return sameOriginApiBase(frontendLocation);
  }

  const apiHostname = apiUrl.hostname.toLowerCase();
  if (!isLocalFrontend && LOOPBACK_HOSTS.has(apiHostname)) {
    return sameOriginApiBase(frontendLocation);
  }
  if (frontendProtocol === "https:" && apiUrl.protocol !== "https:") {
    return sameOriginApiBase(frontendLocation);
  }

  let pathname = apiUrl.pathname.replace(/\/+$/, "");
  if (!/\/api$/i.test(pathname)) {
    pathname = `${pathname}/api`;
  }

  return `${apiUrl.origin}${pathname}`;
}
