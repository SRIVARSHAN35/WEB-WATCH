import { useEffect, useRef, useState } from "react";
import { apiFetch, readJsonResponse } from "./api.js";
import { GOOGLE_CLIENT_ID, mountGoogleButton } from "./googleIdentity.js";
import { applyTheme } from "./theme.js";
import BrandLogo from "./BrandLogo.jsx";

const themeModes = ["system", "light", "dark"];

function initialGuestTheme() {
  try {
    const lastUserId = window.sessionStorage.getItem("webwatch_theme_user_id");
    const accountTheme = lastUserId && window.localStorage.getItem(`webwatch_theme_preference_${lastUserId}`);
    return ["light", "dark", "system"].includes(accountTheme)
      ? accountTheme
      : ["light", "dark", "system"].includes(window.localStorage.getItem("webwatch_guest_theme"))
        ? window.localStorage.getItem("webwatch_guest_theme")
        : "system";
  } catch {
    return "system";
  }
}

function payloadErrorMessage(payload) {
  if (typeof payload.detail === "string") {
    return payload.detail;
  }

  const firstField = Object.values(payload).flat(Infinity).find((value) => typeof value === "string");
  return firstField || "";
}

function httpErrorMessage(payload, statusCode) {
  const detail = payloadErrorMessage(payload);
  if (statusCode === 401) {
    return detail || "Your sign-in has expired. Please sign in again.";
  }
  if (statusCode === 403) {
    return detail || "WebWatch denied this request. Check your account permissions.";
  }
  if (statusCode === 404) {
    return "The WebWatch API route was not found. Check Django's /api routes and the Vite /api proxy target.";
  }
  if (statusCode >= 500) {
    return `WebWatch encountered a server error (HTTP ${statusCode}). Please try again later.`;
  }
  return detail || "Unable to sign in. Check your details and try again.";
}

function googleErrorMessage(payload, statusCode) {
  switch (payload.code) {
    case "google_not_configured":
    case "google_client_mismatch":
    case "google_audience_mismatch":
      return "Google sign-in is temporarily unavailable. Please try again later.";
    case "google_issuer_invalid":
      return "Google returned an invalid sign-in issuer. Please retry Google Sign-In.";
    case "google_verifier_unavailable":
      return "Google sign-in verification is temporarily unavailable. Please try again.";
    case "google_token_invalid":
      return "Google rejected this sign-in token. Please retry Google Sign-In.";
    case "google_email_unverified":
      return "Google did not provide a verified email for this account.";
    default:
      if (statusCode === 503) {
        return "Google sign-in is temporarily unavailable. Please try again later.";
      }
      return httpErrorMessage(payload, statusCode);
  }
}

function AuthPage({ path, navigate, onAuthenticated, notice = "" }) {
  const isRegister = path === "/register";
  const googleButtonRef = useRef(null);
  const busyRef = useRef(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [googleReady, setGoogleReady] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [theme, setTheme] = useState(initialGuestTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const cycleTheme = () => {
    const nextTheme = themeModes[(themeModes.indexOf(theme) + 1) % themeModes.length];
    setTheme(nextTheme);
    try {
      window.localStorage.setItem("webwatch_guest_theme", nextTheme);
    } catch {
      // The current page still updates when browser storage is unavailable.
    }
  };

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID || !googleButtonRef.current) {
      return undefined;
    }

    const handleCredential = async ({ credential }) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      setError("");
      try {
        const response = await apiFetch("/auth/google/", {
          method: "POST",
          body: JSON.stringify({ credential }),
        });
        const payload = await readJsonResponse(response);
        if (!response.ok) {
          throw new Error(googleErrorMessage(payload, response.status));
        }
        await onAuthenticated(payload.token);
      } catch (authError) {
        setError(authError instanceof TypeError
          ? "Could not connect to WebWatch. Check your connection and try again."
          : authError.message || "Google sign-in failed. Please try again.");
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    };
    return mountGoogleButton(
      googleButtonRef.current,
      handleCredential,
      { width: 190, type: "standard", shape: "rectangular" },
      () => setGoogleReady(true),
    );
  }, [onAuthenticated]);

  const submit = async (event) => {
    event.preventDefault();
    if (busyRef.current) return;
    if (isRegister && password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError("");

    try {
      const endpoint = isRegister ? "/auth/register/" : "/auth/login/";
      const body = isRegister
        ? { name, email, password, confirm_password: confirmPassword }
        : { email, password };
      const response = await apiFetch(endpoint, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const payload = await readJsonResponse(response);
      if (!response.ok) {
        throw new Error(httpErrorMessage(payload, response.status));
      }
      await onAuthenticated(payload.token);
    } catch (authError) {
      setError(authError instanceof TypeError
        ? "Could not connect to WebWatch. Check your connection and try again."
        : authError.message || "Could not connect to WebWatch.");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <main className="auth-screen">
      <button className="auth-theme-toggle" type="button" onClick={cycleTheme}
        aria-label={`Theme: ${theme}. Activate to switch theme`} title={`Theme: ${theme}`}>
        <span aria-hidden="true">{theme === "light" ? "☀" : theme === "dark" ? "☾" : "◐"}</span>
        <span>{theme[0].toUpperCase() + theme.slice(1)}</span>
      </button>
      <div className="auth-shell">
      <aside className="auth-showcase" aria-label="About WebWatch">
        <a className="auth-brand" href="/login" onClick={(event) => { event.preventDefault(); navigate("/login"); }}>
          <BrandLogo />
        </a>
        <div className="auth-showcase-copy">
          <span className="auth-showcase-eyebrow"><span aria-hidden="true">✦</span> STAY AHEAD OF EVERY CHANGE</span>
          <h2>Keep watch.<br /><span>Stay in the know.</span></h2>
          <p>Get a clear view of the websites that matter, with meaningful updates when something changes.</p>
        </div>
        <div className="auth-preview" aria-hidden="true">
          <div className="auth-preview-top"><span className="auth-preview-dot" /><span>Monitoring overview</span><span className="auth-preview-live">● Live</span></div>
          <div className="auth-preview-stat"><span>Websites monitored</span><strong>12</strong><span className="auth-preview-line"><i /></span></div>
          <div className="auth-preview-item"><span className="auth-preview-favicon">W</span><span><b>webwatch.app</b><small>All quiet · checked just now</small></span><span className="auth-preview-check">✓</span></div>
          <div className="auth-preview-item"><span className="auth-preview-favicon auth-preview-favicon-blue">D</span><span><b>docs.example.com</b><small>Monitoring is active</small></span><span className="auth-preview-check">✓</span></div>
        </div>
        <p className="auth-showcase-footnote"><span aria-hidden="true">✳</span> A little more peace of mind, every day.</p>
      </aside>
      <section className="auth-panel" aria-labelledby="auth-title">
        <a className="auth-brand" href="/login" onClick={(event) => { event.preventDefault(); navigate("/login"); }}>
          <span aria-hidden="true">●</span> WebWatch
        </a>
        <p className="auth-caption">WEBSITE MONITORING</p>
        <h1 id="auth-title">{isRegister ? "Create your account" : "Welcome back"}</h1>
        <p className="auth-intro">
          {isRegister ? "Keep an eye on the sites that matter." : "Sign in to your monitoring dashboard."}
        </p>
        {!isRegister && notice && <p className="auth-success" role="status">{notice}</p>}

        <form className="auth-form" onSubmit={submit}>
          {isRegister && (
            <div className="auth-field">
              <label htmlFor="auth-name">Name</label>
              <input
                id="auth-name"
                type="text"
                autoComplete="name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
          )}
          <div className="auth-field">
            <label htmlFor="auth-email">Email</label>
            <input
              id="auth-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>
          <div className="auth-field">
            <label htmlFor="auth-password">Password</label>
            <div className="auth-password-control"><input
              id="auth-password"
              type={showPassword ? "text" : "password"}
              autoComplete={isRegister ? "new-password" : "current-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            /><button type="button" className="password-visibility" aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword} onClick={() => setShowPassword((shown) => !shown)}>{showPassword ? "Hide" : "Show"}</button></div>
          </div>
          {isRegister && (
            <div className="auth-field">
              <label htmlFor="auth-confirm-password">Confirm password</label>
              <div className="auth-password-control"><input
                id="auth-confirm-password"
                type={showConfirmPassword ? "text" : "password"}
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                required
              /><button type="button" className="password-visibility confirm-visibility" aria-label={showConfirmPassword ? "Hide confirmation password" : "Show confirmation password"}
                aria-pressed={showConfirmPassword} onClick={() => setShowConfirmPassword((shown) => !shown)}>{showConfirmPassword ? "Hide" : "Show"}</button></div>
            </div>
          )}
          {error && (
            <div className="auth-error" role="alert">
              <p>{error}</p>
              {isRegister && error === "An account with this email already exists. Please log in." && (
                <button
                  className="auth-error-login"
                  type="button"
                  onClick={() => {
                    setError("");
                    navigate("/login");
                  }}
                >
                  Log in
                </button>
              )}
            </div>
          )}
          <button className="auth-submit" type="submit" disabled={busy}>
            {busy ? "Please wait..." : isRegister ? "Create account" : "Login"}
          </button>
          {!isRegister && <button className="forgot-password-link" type="button" onClick={() => navigate("/forgot-password")}>Forgot password?</button>}
        </form>

        <div className="auth-divider"><span>or</span></div>
        {GOOGLE_CLIENT_ID ? (
          <div className={`google-button-wrap auth-google-button${googleReady ? " is-ready" : ""}`} ref={googleButtonRef} />
        ) : (
          <div className="google-unavailable">
            <button type="button" disabled={busy}>Continue with Google</button>
            <p>Google sign-in is not configured.</p>
          </div>
        )}
        <p className="auth-switch">
          {isRegister ? "Already have an account?" : "Don't have an account?"}{" "}
          <button type="button" onClick={() => { setError(""); navigate(isRegister ? "/login" : "/register"); }}>
            {isRegister ? "Login" : "Create account"}
          </button>
        </p>
      </section>
      </div>
    </main>
  );
}

export default AuthPage;
