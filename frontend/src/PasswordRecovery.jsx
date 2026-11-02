import { useEffect, useState } from "react";
import { apiFetch, readJsonResponse } from "./api.js";
import BrandLogo from "./BrandLogo.jsx";

function PasswordRecovery({ path, navigate }) {
  const isReset = path.startsWith("/reset-password/");
  const [, , uid = ""] = path.split("/");
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("token") || "");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState(isReset && (!uid || !token) ? "This reset link is invalid or has expired. Request a new one." : "");
  const [linkStatus, setLinkStatus] = useState(isReset ? (uid && token ? "checking" : "invalid") : "");

  useEffect(() => {
    if (!isReset) return undefined;
    let active = true;
    if (window.location.hash) window.history.replaceState({}, "", window.location.pathname);
    if (!uid || !token) return undefined;
    apiFetch("/auth/password-reset/validate/", {
      method: "POST", body: JSON.stringify({ uid, token }),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("This reset link is invalid or has expired. Request a new one.");
        if (active) setLinkStatus("valid");
      })
      .catch((requestError) => {
        if (active) { setLinkStatus("invalid"); setError(requestError.message || "Could not validate this reset link."); }
      });
    return () => { active = false; };
  }, [isReset, uid, token]);

  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try {
      const response = await apiFetch(isReset ? "/auth/password-reset/confirm/" : "/auth/password-reset/", {
        method: "POST",
        body: JSON.stringify(isReset
          ? { uid, token, new_password: password, confirm_password: confirmPassword }
          : { email }),
      });
      const payload = await readJsonResponse(response);
      if (!response.ok) {
        if (response.status === 429) throw new Error("Too many attempts. Please wait before trying again.");
        const value = Object.values(payload).flat(Infinity).find((item) => typeof item === "string");
        throw new Error(value || "The request could not be completed.");
      }
      if (isReset) setComplete(true);
      else setSubmitted(true);
    } catch (requestError) {
      setError(requestError instanceof TypeError
        ? "Could not connect to WebWatch. Check your connection and try again."
        : requestError.message || "The request could not be completed.");
    } finally { setBusy(false); }
  };

  return <main className="auth-screen">
    <section className="auth-panel recovery-panel" aria-labelledby="recovery-title">
      <a className="auth-brand" href="/login" onClick={(event) => { event.preventDefault(); navigate("/login"); }}><BrandLogo /></a>
      <p className="auth-caption">ACCOUNT SECURITY</p>
      <h1 id="recovery-title">{isReset ? "Choose a new password" : "Forgot your password?"}</h1>
      <p className="auth-intro">{isReset ? "Set a new password for your WebWatch account." : "Enter your account email and we’ll send reset instructions if it matches."}</p>
      {submitted ? <div className="auth-success" role="status">
        <p>If an account matches that email, password reset instructions will be sent.</p>
        <p>If you use Google Sign-In, continue with Google. WebWatch cannot reset your Google password.</p>
        <button type="button" className="auth-submit" onClick={() => navigate("/login")}>Back to login</button>
      </div> : complete ? <div className="auth-success" role="status">
        <p>Your password has been updated. Sign in with the new password.</p>
        <button type="button" className="auth-submit" onClick={() => navigate("/login")}>Go to login</button>
      </div> : isReset && linkStatus === "checking" ? <p className="auth-loading-message" role="status">Checking reset link...</p> : isReset && linkStatus === "invalid" ? <div className="auth-error" role="alert">
        <p>{error || "This reset link is invalid or has expired. Request a new one."}</p>
        <button type="button" className="recovery-back" onClick={() => navigate("/forgot-password")}>Request another link</button>
        <button type="button" className="recovery-back" onClick={() => navigate("/login")}>Back to login</button>
      </div> : <form className="auth-form" onSubmit={submit}>
        {isReset ? <>
          <div className="auth-field"><label htmlFor="reset-password">New password</label><div className="auth-password-control">
            <input id="reset-password" type={showPassword ? "text" : "password"} autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
            <button type="button" className="password-visibility" aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword} onClick={() => setShowPassword((value) => !value)}>{showPassword ? "Hide" : "Show"}</button>
          </div></div>
          <div className="auth-field"><label htmlFor="reset-confirm-password">Confirm new password</label><div className="auth-password-control">
            <input id="reset-confirm-password" type={showConfirm ? "text" : "password"} autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required />
            <button type="button" className="password-visibility" aria-label={showConfirm ? "Hide confirmation password" : "Show confirmation password"} aria-pressed={showConfirm} onClick={() => setShowConfirm((value) => !value)}>{showConfirm ? "Hide" : "Show"}</button>
          </div></div>
        </> : <div className="auth-field"><label htmlFor="reset-email">Email</label>
          <input id="reset-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></div>}
        {error && <div className="auth-error" role="alert"><p>{error}</p></div>}
        <button className="auth-submit" type="submit" disabled={busy}>{busy ? "Please wait..." : isReset ? "Update password" : "Send instructions"}</button>
        <button className="recovery-back" type="button" onClick={() => navigate("/login")}>Back to login</button>
      </form>}
    </section>
  </main>;
}

export default PasswordRecovery;
