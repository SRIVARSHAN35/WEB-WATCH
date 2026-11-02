import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./api.js";
import { requestAccountDeletion } from "./authState.js";
const formatDateTime = (value) => {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unavailable" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
};

export default function AccountPages({ page, onThemeChange, onProfileUpdated, onPasswordChanged, onAccountDeleted }) {
  const [profile, setProfile] = useState(null);
  const [settings, setSettings] = useState(null);
  const [whatsappSettings, setWhatsappSettings] = useState(null);
  const [whatsappHistory, setWhatsappHistory] = useState([]);
  const [whatsappPhoneDraft, setWhatsappPhoneDraft] = useState("");
  const [whatsappEnabledDraft, setWhatsappEnabledDraft] = useState(false);
  const [whatsappBusy, setWhatsappBusy] = useState(false);
  const [whatsappFeedback, setWhatsappFeedback] = useState("");
  const [whatsappFeedbackType, setWhatsappFeedbackType] = useState("");
  const [history, setHistory] = useState([]);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyPage, setHistoryPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [retryCount, setRetryCount] = useState(0);
  const [retryAction, setRetryAction] = useState(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deletingAccount, setDeletingAccount] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    let active = true;
    const path = page === "profile" ? "/account/profile/" : page === "settings" ? "/account/settings/" : "/account/history/";
    apiFetch(path).then(async (response) => {
      if (!response.ok) {
        const messages = {
          401: "Your session has expired. Please sign in again.",
          403: "You are not allowed to view this account information.",
          404: "This account page is unavailable. Please refresh WebWatch and try again.",
        };
        throw new Error(messages[response.status] || (response.status >= 500
          ? "WebWatch could not load this page because the server is unavailable. Please try again."
          : "WebWatch could not load this page. Please check your connection and try again."));
      }
      const data = await response.json();
      if (!active) return;
      if (page === "profile") {
        if (!data || typeof data.email !== "string" || typeof data.first_name !== "string") throw new Error("WebWatch returned incomplete profile information. Please retry.");
        setProfile(data); setFirstName(data.first_name || ""); setLastName(data.last_name || "");
      } else if (page === "settings") {
        if (!data || typeof data.email_alerts_enabled !== "boolean" || !["light", "dark", "system"].includes(data.theme)) throw new Error("WebWatch returned incomplete settings. Please retry.");
        setSettings(data); onThemeChange(data.theme);
        const whatsappResponse = await apiFetch("/notifications/whatsapp/settings/");
        const whatsappData = await whatsappResponse.json();
        if (!whatsappResponse.ok || typeof whatsappData.enabled !== "boolean" || typeof whatsappData.phone_number !== "string") throw new Error(whatsappData.detail || "WhatsApp settings could not be loaded.");
        setWhatsappSettings(whatsappData);
        setWhatsappPhoneDraft(whatsappData.phone_number);
        setWhatsappEnabledDraft(whatsappData.enabled);
        const historyResponse = await apiFetch("/notifications/whatsapp/history/");
        const historyData = await historyResponse.json();
        if (historyResponse.ok && Array.isArray(historyData)) setWhatsappHistory(historyData);
      } else {
        if (!data || !Array.isArray(data.results)) throw new Error("WebWatch returned incomplete activity history. Please retry.");
        setHistory(data.results); setHistoryHasMore(data.has_more); setHistoryPage(data.page);
      }
    }).catch((requestError) => {
      if (!active) return;
      setError(requestError instanceof TypeError
        ? "Could not reach the WebWatch server. Check that the backend is running and try again."
        : requestError instanceof SyntaxError
          ? "WebWatch returned an unreadable response. Please retry, and contact support if this continues."
          : requestError.message || "This page could not be loaded. Please try again.");
    })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, onThemeChange, retryCount]);

  const retryPageLoad = useCallback(() => {
    if (retryAction) {
      retryAction();
      return;
    }
    setLoading(true);
    setError("");
    setRetryCount((count) => count + 1);
  }, [retryAction]);

  const saveProfile = async (event) => {
    event.preventDefault(); setSaving(true); setError(""); setNotice("");
    try {
      const response = await apiFetch("/account/profile/", {
        method: "PATCH", body: JSON.stringify({ first_name: firstName, last_name: lastName }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Profile could not be saved.");
      setProfile(data); onProfileUpdated(data); setNotice("Profile updated.");
    } catch (requestError) { setError(requestError.message || "Profile could not be saved."); }
    finally { setSaving(false); }
  };

  const saveSetting = async (patch) => {
    const previous = settings;
    const optimistic = { ...settings, ...patch };
    setSettings(optimistic);
    if (patch.theme) onThemeChange(patch.theme);
    setSaving(true); setError(""); setNotice("");
    try {
      const response = await apiFetch("/account/settings/", { method: "PATCH", body: JSON.stringify(patch) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Setting could not be saved.");
      setSettings(data); onThemeChange(data.theme); setNotice("Settings saved.");
    } catch (requestError) {
      setSettings(previous); onThemeChange(previous.theme);
      setError(requestError.message || "Setting could not be saved.");
    } finally { setSaving(false); }
  };

  const saveWhatsAppSettings = async (event) => {
    event.preventDefault();
    setWhatsappBusy(true); setWhatsappFeedback(""); setWhatsappFeedbackType("");
    if (whatsappEnabledDraft && !/^\+[1-9]\d{7,14}$/.test(whatsappPhoneDraft.trim())) {
      setWhatsappFeedback("Enter a phone number in international format, for example +14155552671.");
      setWhatsappFeedbackType("error"); setWhatsappBusy(false); return;
    }
    try {
      const response = await apiFetch("/notifications/whatsapp/settings/", {
        method: "PATCH",
        body: JSON.stringify({ enabled: whatsappEnabledDraft, phone_number: whatsappPhoneDraft.trim() }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.phone_number || data.enabled || data.detail || "WhatsApp settings could not be saved.");
      setWhatsappSettings(data); setWhatsappPhoneDraft(data.phone_number); setWhatsappEnabledDraft(data.enabled);
      setWhatsappFeedback("WhatsApp settings saved."); setWhatsappFeedbackType("success");
    } catch (requestError) {
      setWhatsappFeedback(requestError.message || "WhatsApp settings could not be saved."); setWhatsappFeedbackType("error");
    } finally { setWhatsappBusy(false); }
  };

  const sendWhatsAppTest = async () => {
    setWhatsappBusy(true); setWhatsappFeedback(""); setWhatsappFeedbackType("");
    try {
      const response = await apiFetch("/notifications/whatsapp/test/", { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Test WhatsApp message could not be sent.");
      setWhatsappFeedback(data.detail || "Meta accepted the message for processing; recipient delivery is unconfirmed."); setWhatsappFeedbackType("success");
      const historyResponse = await apiFetch("/notifications/whatsapp/history/");
      if (historyResponse.ok) setWhatsappHistory(await historyResponse.json());
    } catch (requestError) {
      setWhatsappFeedback(requestError.message || "Test WhatsApp message could not be sent."); setWhatsappFeedbackType("error");
      const historyResponse = await apiFetch("/notifications/whatsapp/history/");
      if (historyResponse.ok) setWhatsappHistory(await historyResponse.json());
    } finally { setWhatsappBusy(false); }
  };

  const changePassword = async (event) => {
    event.preventDefault(); setSaving(true); setError(""); setNotice("");
    try {
      const response = await apiFetch("/auth/password/change/", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword, confirm_password: confirmPassword }),
      });
      const data = await response.json();
      if (!response.ok) {
        const message = Object.values(data).flat(Infinity).find((item) => typeof item === "string");
        throw new Error(message || "Password could not be changed.");
      }
      setCurrentPassword(""); setNewPassword(""); setConfirmPassword("");
      onPasswordChanged();
    } catch (requestError) { setError(requestError.message || "Password could not be changed."); }
    finally { setSaving(false); }
  };

  const deleteAccount = async (event) => {
    event.preventDefault();
    if (deleteConfirmation !== "DELETE" || deletingAccount) return;
    setDeletingAccount(true); setDeleteError("");
    try {
      await requestAccountDeletion(apiFetch, onAccountDeleted);
    } catch (requestError) {
      setDeleteError(requestError.message || "Your account could not be deleted. Please try again.");
      setDeletingAccount(false);
    }
  };

  const loadMore = async () => {
    setSaving(true); setError("");
    setRetryAction(() => loadMore);
    try {
      const response = await apiFetch(`/account/history/?page=${historyPage + 1}`);
      if (!response.ok) throw new Error(response.status >= 500
        ? "WebWatch could not load more activity because the server is unavailable. Please retry."
        : "More activity could not be loaded. Check your connection and retry.");
      const data = await response.json();
      setHistory((items) => [...items, ...data.results]); setHistoryHasMore(data.has_more); setHistoryPage(data.page);
      setRetryAction(null);
    } catch (requestError) { setError(requestError.message || "History could not be loaded. Please try again."); }
    finally { setSaving(false); }
  };

  return <main className="account-page">
    {page === "profile" && <>
      {loading ? <div className="account-skeleton" role="status" aria-label="Loading profile information"><span /><span /><span /></div> : profile && <>
        <section className="account-card account-profile-card">
          <div className="account-avatar account-avatar-large" aria-hidden="true">{`${firstName[0] || profile.email[0] || "W"}${lastName[0] || ""}`.toUpperCase()}</div>
          <div><h2>{profile.name}</h2><p>{profile.email}</p></div>
        </section>
        <form className="account-card account-form" onSubmit={saveProfile}>
          <h2>Edit profile</h2>
          <label>First name<input value={firstName} maxLength={150} onChange={(event) => setFirstName(event.target.value)} /></label>
          <label>Last name<input value={lastName} maxLength={150} onChange={(event) => setLastName(event.target.value)} /></label>
          <label>Email<input value={profile.email} readOnly aria-describedby="email-help" /></label>
          <small id="email-help">Email changes require account verification and are not available here.</small>
        <div className="profile-meta"><span>Sign-in method</span><strong>{profile.account_type}</strong><span>Member since</span><strong>{formatDateTime(profile.created_at)}</strong></div>
          <button className="add-button" disabled={saving}>{saving ? "Saving..." : "Save profile"}</button>
        </form>
        <section className="account-card account-form">
          <h2>Change password</h2>
          {profile.has_usable_password ? <form className="account-password-form" onSubmit={changePassword}>
            <label>Current password<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label>
            <label>New password<input type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label>
            <label>Confirm new password<input type="password" autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required /></label>
            <p>After a successful change, you’ll be asked to sign in again.</p>
            <button className="add-button" disabled={saving}>{saving ? "Updating..." : "Change password"}</button>
          </form> : <p>This account uses Google Sign-In and has no WebWatch password. Continue with Google to access your account.</p>}
        </section>
      </>}
    </>}
    {page === "settings" && <>
      {loading ? <div className="account-skeleton" role="status" aria-label="Loading account settings"><span /><span /></div> : settings && <>
        <section className="account-card account-form">
          <h2>Email alerts</h2><p>Choose whether WebWatch emails you when a monitored website changes. In-app alerts stay on either way.</p>
          <label className="setting-toggle"><input type="checkbox" checked={settings.email_alerts_enabled} disabled={saving}
            onChange={(event) => saveSetting({ email_alerts_enabled: event.target.checked })} />
            <span><strong>Email change alerts</strong><small>{settings.email_alerts_enabled ? "Enabled" : "Disabled"}</small></span></label>
        </section>
        {whatsappSettings && <section className="account-card account-form">
          <h2>WhatsApp alerts</h2>
          <p>Send website-change alerts to your WhatsApp account. Enter a number that can receive WhatsApp messages, including + and your country code.</p>
          <p className={`whatsapp-config-status ${whatsappSettings.integration_enabled && whatsappSettings.configured ? "ready" : "not-ready"}`} role="status">
            {!whatsappSettings.integration_enabled
              ? "WhatsApp delivery is disabled by the WebWatch administrator."
              : !whatsappSettings.configured
                ? `WhatsApp delivery needs administrator configuration: ${whatsappSettings.missing_configuration.join(", ")}.`
                : whatsappSettings.template_configured
                  ? "Cloud API configured. Meta will validate the template approval, language, and parameters when sending."
                  : "Cloud API configured. Outside the customer-service window, Meta requires an approved template."}
          </p>
          <form className="whatsapp-settings-form" onSubmit={saveWhatsAppSettings}>
            <label className="setting-toggle">
              <input type="checkbox" checked={whatsappEnabledDraft} disabled={whatsappBusy}
                onChange={(event) => setWhatsappEnabledDraft(event.target.checked)} />
              <span><strong>WhatsApp change alerts</strong><small>{whatsappEnabledDraft ? "Enabled" : "Disabled"}</small></span>
            </label>
            <label>WhatsApp recipient number
              <input type="tel" inputMode="tel" autoComplete="tel" maxLength={16}
                placeholder="+14155552671" value={whatsappPhoneDraft} disabled={whatsappBusy}
                pattern="\+[1-9][0-9]{7,14}"
                onChange={(event) => setWhatsappPhoneDraft(event.target.value)} />
            </label>
            <small>This is your destination number. It can be different from the Meta sender/test number.</small>
            <div className="whatsapp-actions">
              <button type="submit" className="add-button" disabled={whatsappBusy}>{whatsappBusy ? "Saving..." : "Save WhatsApp settings"}</button>
              <button type="button" className="secondary-button" onClick={sendWhatsAppTest}
                disabled={whatsappBusy || !whatsappSettings.integration_enabled || !whatsappSettings.configured || !whatsappPhoneDraft.trim()}>
                {whatsappBusy ? "Working..." : "Send test message"}
              </button>
            </div>
          </form>
          {whatsappFeedback && <p className={whatsappFeedbackType === "error" ? "account-error" : "account-notice"} role={whatsappFeedbackType === "error" ? "alert" : "status"}>{whatsappFeedback}</p>}
          <div className="whatsapp-history">
            <h3>Recent WhatsApp delivery</h3>
            {whatsappHistory.length ? whatsappHistory.slice(0, 5).map((delivery) => <div className="whatsapp-history-row" key={delivery.id}>
              <span>{delivery.website_name} · {delivery.recipient} · {delivery.status}</span>
              <time>{formatDateTime(delivery.accepted_at || delivery.created_at)}</time>
              {delivery.error_detail && <small>{delivery.error_detail}</small>}
            </div>) : <small>No WhatsApp messages yet.</small>}
          </div>
        </section>}
        <section className="account-card account-form">
          <h2>Theme</h2><p>Choose the appearance used throughout WebWatch.</p>
          <div className="theme-options" role="radiogroup" aria-label="Theme">
            {["light", "dark", "system"].map((theme) => <label key={theme} className="theme-option">
              <input type="radio" name="theme" value={theme} checked={settings.theme === theme} disabled={saving}
                onChange={() => saveSetting({ theme })} /> {theme[0].toUpperCase() + theme.slice(1)}
            </label>)}
          </div>
        </section>
        <section className="account-card account-form account-delete-section">
          <h2>Delete account</h2>
          <p>Permanently delete your WebWatch account, monitored websites, credentials, alerts, and account history.</p>
          <button type="button" className="confirm-delete-button account-delete-button"
            onClick={() => { setDeleteError(""); setDeleteConfirmation(""); setShowDeleteDialog(true); }}>
            Delete Account
          </button>
        </section>
      </>}
    </>}
    {page === "history" && <>
      {loading ? <div className="account-skeleton" role="status" aria-label="Loading activity history"><span /><span /><span /></div> : history.length === 0 ?
        <div className="account-card account-empty"><h2>No activity yet</h2><p>Sign-ins, website changes, and monitoring activity will appear here.</p></div> :
        <div className="activity-list">{history.map((event) => <article className="account-card activity-card" key={event.id}>
          <span className="activity-marker" aria-hidden="true" />
          <div><strong>{event.description}</strong><time>{formatDateTime(event.created_at)}</time>
            {event.website_url && /^https?:\/\//i.test(event.website_url) && <a href={event.website_url} target="_blank" rel="noopener noreferrer">{event.website_name}</a>}
          </div>
        </article>)}</div>}
      {!loading && historyHasMore && <button className="secondary-button account-load-more" onClick={loadMore} disabled={saving}>{saving ? "Loading..." : "Load more"}</button>}
    </>}
    {error && <div className="account-error" role="alert"><span>{error}</span><button type="button" className="secondary-button" onClick={retryPageLoad} disabled={loading || saving}>Retry</button></div>}
    {notice && <p className="account-notice" role="status">{notice}</p>}
    {showDeleteDialog && <div className="modal-overlay" onClick={() => !deletingAccount && setShowDeleteDialog(false)}>
      <section className="modal delete-modal account-delete-dialog" role="dialog" aria-modal="true"
        aria-labelledby="account-delete-title" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header"><div>
          <h2 id="account-delete-title">Permanently delete your account?</h2>
          <p>This action cannot be undone. You will lose access to WebWatch and all associated websites, monitoring credentials, change history, alerts, and preferences.</p>
        </div></div>
        <form onSubmit={deleteAccount}>
          <label htmlFor="account-delete-confirmation">Type DELETE to confirm</label>
          <input id="account-delete-confirmation" autoFocus autoComplete="off" value={deleteConfirmation}
            onChange={(event) => setDeleteConfirmation(event.target.value)} disabled={deletingAccount} />
          {deleteError && <p className="delete-error" role="alert">{deleteError}</p>}
          <div className="modal-actions">
            <button type="button" className="cancel-button" onClick={() => setShowDeleteDialog(false)} disabled={deletingAccount}>Cancel</button>
            <button type="submit" className="confirm-delete-button" disabled={deleteConfirmation !== "DELETE" || deletingAccount}>
              {deletingAccount ? "Deleting account..." : "Permanently delete"}
            </button>
          </div>
        </form>
      </section>
    </div>}
  </main>;
}
