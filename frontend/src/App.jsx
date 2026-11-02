import { useCallback, useEffect, useRef, useState } from "react";
import AuthPage from "./AuthPage.jsx";
import GoogleAccountLink from "./GoogleAccountLink.jsx";
import AlertsPage from "./AlertsPage.jsx";
import AccountPages from "./AccountPages.jsx";
import { applyTheme } from "./theme.js";
import PasswordRecovery from "./PasswordRecovery.jsx";
import { DeveloperConsole, DeveloperLogin } from "./DeveloperConsole.jsx";
import BrandLogo from "./BrandLogo.jsx";
import { apiFetch, clearAuthToken, getAuthToken, saveAuthToken } from "./api.js";
import "./App.css";

const formatDetectedAt = (timestamp) => {
  if (!timestamp) {
    return null;
  }

  const date = new Date(timestamp);

  if (Number.isNaN(date.getTime())) {
    return "Date unavailable";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
};

const statusLabels = {
  monitoring: "Monitoring",
  stopped: "Stopped",
  error: "🔴 Monitoring Error",
  login_required: "Login Required",
};

const loginStatusLabels = {
  not_configured: "Login not configured",
  connected: "Login connected",
  expired: "Login expired",
  failed: "Login failed",
  needs_attention: "Login needs attention",
};

const hasRecentChange = (website) => {
  if (website.status !== "monitoring" || !website.last_changed) {
    return false;
  }

  const changedAt = new Date(website.last_changed).getTime();
  const freshnessWindow = Number(website.check_interval) * 60_000;
  const age = Date.now() - changedAt;

  return Number.isFinite(changedAt) && age >= 0 && age <= freshnessWindow;
};

const accountPageFromRoute = (route) => ({
  "/websites": "dashboard",
  "/alerts": "alerts",
  "/profile": "profile",
  "/settings": "settings",
  "/history": "history",
  "/activity-history": "history",
}[route] || "dashboard");

const routeFromAccountPage = (page) => page === "history"
  ? "/activity-history"
  : page === "dashboard" ? "/dashboard" : `/${page}`;

function normalizeAppRoute(path, hasToken) {
  if (path === "/developer/login") return path;
  if (path === "/developer") return hasToken ? path : "/developer/login";
  const authenticatedRoutes = ["/dashboard", "/websites", "/alerts", "/profile", "/settings", "/history", "/activity-history"];
  const publicRoute = path === "/login" || path === "/register"
    || path === "/forgot-password" || path.startsWith("/reset-password/");
  return hasToken ? (authenticatedRoutes.includes(path) ? path : "/dashboard") : (publicRoute ? path : "/login");
}

function NavigationIcon({ name }) {
  const common = { width: 19, height: 19, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true, focusable: false };
  const drawings = {
    dashboard: <><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" /></>,
    websites: <><circle cx="12" cy="12" r="8.5" /><path d="M3.8 12h16.4M12 3.5a13 13 0 0 1 0 17M12 3.5a13 13 0 0 0 0 17" /></>,
    alerts: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></>,
    history: <><path d="M3.5 12a8.5 8.5 0 1 0 2.2-5.7L3.5 8.5" /><path d="M3.5 4.5v4h4M12 7v5l3 2" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.8 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.8-1l-1.7.7-1.4-2.4 1.4-1.1a7 7 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.8-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.8 1l1.7-.7 1.4 2.4-1.4 1.1a7 7 0 0 1-.1 2Z" /></>,
  };
  return <svg {...common}>{drawings[name]}</svg>;
}

function Dashboard({ currentUser, route, navigate, onLogout, onProfileUpdated, onPasswordChanged, onAccountDeleted }) {
  const activePage = accountPageFromRoute(route);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [unreadAlertCount, setUnreadAlertCount] = useState(0);
  const [googleLinked, setGoogleLinked] = useState(Boolean(currentUser.google_linked));
  const [websites, setWebsites] = useState([]);
  const [websitesLoading, setWebsitesLoading] = useState(true);
  const [websitesError, setWebsitesError] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [filterWebsite, setFilterWebsite] = useState(null);
  const [filterDraft, setFilterDraft] = useState("");
  const [filterSaving, setFilterSaving] = useState(false);
  const [filterError, setFilterError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deletingWebsiteId, setDeletingWebsiteId] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const [checkingWebsiteIds, setCheckingWebsiteIds] = useState(() => new Set());
  const [checkFeedback, setCheckFeedback] = useState({});
  const [credentialWebsite, setCredentialWebsite] = useState(null);
  const [credentialForm, setCredentialForm] = useState(null);
  const [credentialBusy, setCredentialBusy] = useState(false);
  const [credentialError, setCredentialError] = useState("");
  const [loginTestResult, setLoginTestResult] = useState(null);
  const [showCredentialPassword, setShowCredentialPassword] = useState(false);
  const [advancedCredentialSettings, setAdvancedCredentialSettings] = useState(false);
  const [changeHistory, setChangeHistory] = useState(null);
  const [historyNotice, setHistoryNotice] = useState("");
  const historyRequestId = useRef(0);
  const historyNewestChangeId = useRef(null);
  const websiteFetchInProgress = useRef(false);
  const deletedWebsiteIds = useRef(new Set());
  const profileTriggerRef = useRef(null);
  const profilePanelRef = useRef(null);
  const profileFirstItemRef = useRef(null);
  const credentialDialogRef = useRef(null);
  const credentialReturnFocusRef = useRef(null);
  const credentialInitialFormRef = useRef(null);

  const changeTheme = useCallback((theme) => applyTheme(theme, currentUser.id), [currentUser.id]);

  const goToPage = (page) => {
    setProfileMenuOpen(false);
    setMobileNavOpen(false);
    navigate(routeFromAccountPage(page));
  };

  const pageTitle = ({ dashboard: "Dashboard", alerts: "Alerts", profile: "Profile", settings: "Settings", history: "Activity History" })[activePage];
  const pageDescription = ({ dashboard: "Your monitoring workspace", alerts: "Website changes detected on your monitored websites", profile: "Your WebWatch account information", settings: "Manage notification delivery and appearance", history: "Recent account and website activity" })[activePage];
  const welcomeName = currentUser.name?.trim()?.split(/\s+/)[0] || currentUser.email?.split("@")[0] || "there";

  useEffect(() => {
    let active = true;
    apiFetch("/account/settings/").then(async (response) => {
      if (!response.ok) throw new Error("Settings unavailable");
      const data = await response.json();
      if (active) changeTheme(data.theme);
    }).catch(() => {});
    return () => { active = false; };
  }, [changeTheme, currentUser.id]);

  useEffect(() => {
    if (!profileMenuOpen) return undefined;
    profileFirstItemRef.current?.focus();
    const closeOnPointer = (event) => {
      if (!profilePanelRef.current?.contains(event.target) && !profileTriggerRef.current?.contains(event.target)) {
        setProfileMenuOpen(false);
      }
    };
    const closeOnKey = (event) => {
      if (event.key === "Escape") {
        setProfileMenuOpen(false);
        profileTriggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", closeOnPointer);
    document.addEventListener("keydown", closeOnKey);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointer);
      document.removeEventListener("keydown", closeOnKey);
    };
  }, [profileMenuOpen]);

  useEffect(() => {
    if (!mobileNavOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setMobileNavOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mobileNavOpen]);

  useEffect(() => {
    let active = true;
    const loadUnreadCount = async () => {
      try {
        const response = await apiFetch("/alerts/unread-count/");
        if (!response.ok) throw new Error("Unread alert count unavailable");
        const data = await response.json();
        if (active) setUnreadAlertCount(data.unread_count || 0);
      } catch {
        if (active) setUnreadAlertCount(0);
      }
    };
    void loadUnreadCount();
    const refreshInterval = window.setInterval(loadUnreadCount, 15_000);
    return () => {
      active = false;
      window.clearInterval(refreshInterval);
    };
  }, [currentUser.id]);

  const markGoogleLinked = useCallback(() => setGoogleLinked(true), []);

  const [formData, setFormData] = useState({
    name: "",
    url: "",
    requires_login: false,
    check_interval: 15,
    ignore_selectors: "",
  });

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const fetchWebsites = async () => {
    if (websiteFetchInProgress.current) {
      return;
    }

    websiteFetchInProgress.current = true;

    try {
      const response = await apiFetch("/websites/");

      if (!response.ok) {
        throw new Error(`Website request failed with status ${response.status}`);
      }

      const data = await response.json();

      if (!Array.isArray(data)) {
        throw new Error("Unexpected website list response");
      }

      setWebsites(data.filter((website) => !deletedWebsiteIds.current.has(website.id)));
      setWebsitesError("");
    } catch (error) {
      setWebsitesError("Websites could not be loaded. Check your connection and retry.");
      console.error("Error fetching websites:", error);
    } finally {
      setWebsitesLoading(false);
      websiteFetchInProgress.current = false;
    }
  };

  useEffect(() => {
    fetchWebsites();

    const pollingInterval = window.setInterval(fetchWebsites, 10_000);

    return () => window.clearInterval(pollingInterval);
  }, []);

  const openModal = () => {
    setError("");
    setShowModal(true);
  };

  const closeModal = () => {
    if (!saving) {
      setShowModal(false);
      setError("");
    }
  };

  const viewChanges = async (website) => {
    const requestId = ++historyRequestId.current;
    historyNewestChangeId.current = null;
    setHistoryNotice("");

    setChangeHistory({
      website,
      changes: [],
      loading: true,
      error: "",
    });

    try {
      const response = await apiFetch(`/websites/${website.id}/changes/`);

      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }

      const changes = await response.json();

      if (!Array.isArray(changes)) {
        throw new Error("Unexpected change history response");
      }

      if (historyRequestId.current === requestId) {
        historyNewestChangeId.current = changes[0]?.id || null;
        setChangeHistory({ website, changes, loading: false, error: "" });
      }
    } catch (fetchError) {
      console.error("Error fetching website changes:", fetchError);

      if (historyRequestId.current === requestId) {
        setChangeHistory({
          website,
          changes: [],
          loading: false,
          error: "Could not load change history. Please try again.",
        });
      }
    }
  };

  const clearChangeHistory = async () => {
    if (!changeHistory || changeHistory.loading || changeHistory.error) {
      return;
    }
    const website = changeHistory.website;
    try {
      const response = await apiFetch(`/websites/${website.id}/clear-change-history/`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }
      historyNewestChangeId.current = null;
      setHistoryNotice("");
      setChangeHistory((current) => current?.website.id === website.id
        ? { ...current, changes: [] }
        : current);
      setWebsites((current) => current.map((item) => item.id === website.id
        ? { ...item, last_changed: null }
        : item));
    } catch (error) {
      console.error("Error clearing website change history:", error);
      setHistoryNotice("Could not clear change history. Please try again.");
    }
  };

  const closeChangeHistory = () => {
    historyRequestId.current += 1;
    historyNewestChangeId.current = null;
    setHistoryNotice("");
    setChangeHistory(null);
  };

  const historyWebsiteId = changeHistory?.website?.id;
  const historyLoading = changeHistory?.loading;
  const historyError = changeHistory?.error;

  useEffect(() => {
    if (!historyWebsiteId || historyLoading || historyError) {
      return undefined;
    }

    const websiteId = historyWebsiteId;
    let requestInProgress = false;
    const refreshHistory = async () => {
      if (requestInProgress) {
        return;
      }
      requestInProgress = true;
      try {
        const response = await apiFetch(`/websites/${websiteId}/changes/`);
        if (!response.ok) {
          return;
        }
        const changes = await response.json();
        if (!Array.isArray(changes)) {
          return;
        }
        const newestId = changes[0]?.id || null;
        if (historyNewestChangeId.current && newestId && newestId !== historyNewestChangeId.current) {
          setHistoryNotice("New change detected. The updated value is shown below.");
        }
        historyNewestChangeId.current = newestId;
        setChangeHistory((current) => (
          current?.website.id === websiteId
            ? { ...current, changes }
            : current
        ));
      } catch (error) {
        console.error("Error refreshing website changes:", error);
      } finally {
        requestInProgress = false;
      }
    };

    const interval = window.setInterval(refreshHistory, 3000);
    return () => window.clearInterval(interval);
  }, [historyWebsiteId, historyLoading, historyError]);

  const deleteWebsite = async () => {
    if (!deleteTarget || deletingWebsiteId !== null) {
      return;
    }

    const website = deleteTarget;
    setDeletingWebsiteId(website.id);
    setDeleteError("");

    try {
      const response = await apiFetch(`/websites/${website.id}/`, {
        method: "DELETE",
      });

      if (response.status === 404) {
        setDeleteTarget(null);
        setDeleteError("This website could not be found. Your list has been refreshed.");
        await fetchWebsites();
        return;
      }

      if (!response.ok) {
        setDeleteError("Unable to delete this website. Please try again.");
        setDeleteTarget(null);
        return;
      }

      deletedWebsiteIds.current.add(website.id);
      setWebsites((previous) => previous.filter((item) => item.id !== website.id));
      if (changeHistory?.website.id === website.id) {
        closeChangeHistory();
      }
      setDeleteTarget(null);
    } catch (requestError) {
      console.error("Error deleting website:", requestError);
      setDeleteError("Unable to delete this website. Please try again.");
      setDeleteTarget(null);
    } finally {
      setDeletingWebsiteId(null);
    }
  };

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;

    setFormData((prev) => ({
      ...prev,
      [name]: type === "checkbox" ? checked : value,
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    setSaving(true);
    setError("");

    try {
      const response = await apiFetch("/websites/", {
        method: "POST",
        body: JSON.stringify({
          ...formData,
          check_interval: Number(formData.check_interval),
          ignore_selectors: formData.ignore_selectors.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        console.error("API Error:", data);
        setError("Unable to add website. Please check your details.");
        return;
      }

      setWebsites((prev) => [data, ...prev]);

      setFormData({
        name: "",
        url: "",
        requires_login: false,
        check_interval: 15,
        ignore_selectors: "",
      });

      setShowModal(false);
    } catch (error) {
      console.error("Error adding website:", error);
      setError("Could not connect to the Django server.");
    } finally {
      setSaving(false);
    }
  };

  const saveWebsiteFilters = async (event) => {
    event.preventDefault();
    if (!filterWebsite) return;
    setFilterSaving(true);
    setFilterError("");
    try {
      const response = await apiFetch(`/websites/${filterWebsite.id}/`, {
        method: "PATCH",
        body: JSON.stringify({
          ignore_selectors: filterDraft.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = data.ignore_selectors?.[0] || data.detail || "Could not save dynamic filters.";
        throw new Error(detail);
      }
      setWebsites((current) => current.map((item) => item.id === data.id ? data : item));
      setFilterWebsite(null);
    } catch (error) {
      setFilterError(error.message || "Could not save dynamic filters.");
    } finally {
      setFilterSaving(false);
    }
  };

  const startMonitoring = async (websiteId) => {
  try {
    const response = await apiFetch(`/websites/${websiteId}/start/`, {
      method: "POST",
    });

    if (!response.ok) {
      throw new Error("Failed to start monitoring");
    }

    const updatedWebsite = await response.json();

    setWebsites((prev) =>
      prev.map((website) =>
        website.id === websiteId ? updatedWebsite : website
      )
    );
  } catch (error) {
    console.error("Error starting monitoring:", error);
  }
};


const stopMonitoring = async (websiteId) => {
  try {
    const response = await apiFetch(`/websites/${websiteId}/stop/`, {
      method: "POST",
    });

    if (!response.ok) {
      throw new Error("Failed to stop monitoring");
    }

    const updatedWebsite = await response.json();

    setWebsites((prev) =>
      prev.map((website) =>
        website.id === websiteId ? updatedWebsite : website
      )
    );
  } catch (error) {
    console.error("Error stopping monitoring:", error);
  }
};

const checkNow = async (websiteId) => {
  if (checkingWebsiteIds.has(websiteId)) {
    return;
  }

  setCheckingWebsiteIds((previous) => new Set(previous).add(websiteId));
  setCheckFeedback((previous) => ({ ...previous, [websiteId]: null }));

  try {
    const response = await apiFetch(`/websites/${websiteId}/check-now/`, {
      method: "POST",
    });

    if (response.status === 404) {
      setCheckFeedback((previous) => ({
        ...previous,
        [websiteId]: { type: "error", message: "Website not found. Refreshing your list." },
      }));
      await fetchWebsites();
      return;
    }

    if (!response.ok) {
      setCheckFeedback((previous) => ({
        ...previous,
        [websiteId]: {
          type: "error",
          message: response.status === 401
            ? "Your session has expired. Redirecting to login."
            : response.status === 409
              ? "Start monitoring before requesting a check."
              : "Could not start the check. Please try again.",
        },
      }));
      return;
    }

    setCheckFeedback((previous) => ({
      ...previous,
      [websiteId]: { type: "success", message: "Check started." },
    }));
  } catch (requestError) {
    console.error("Error starting website check:", requestError);
    setCheckFeedback((previous) => ({
      ...previous,
      [websiteId]: { type: "error", message: "Could not start the check. Please try again." },
    }));
  } finally {
    setCheckingWebsiteIds((previous) => {
      const next = new Set(previous);
      next.delete(websiteId);
      return next;
    });
  }
};

const emptyCredentialForm = {
  login_url: "",
  username: "",
  password: "",
  username_selector: "",
  password_selector: "",
  submit_selector: "",
  success_selector: "",
  success_url: "",
};

const openCredentialModal = async (website) => {
  credentialReturnFocusRef.current = document.activeElement;
  credentialInitialFormRef.current = { ...emptyCredentialForm, login_url: website.url || "" };
  setCredentialWebsite(website);
  setCredentialForm(credentialInitialFormRef.current);
  setCredentialError("");
  setLoginTestResult(null);
  setShowCredentialPassword(false);
  setAdvancedCredentialSettings(false);
  setCredentialBusy(true);
  try {
    const response = await apiFetch(`/websites/${website.id}/credentials/`);
    if (!response.ok) {
      throw new Error("Could not load the website login configuration.");
    }
    const saved = await response.json();
    if (saved.configured) {
      const savedForm = {
        ...emptyCredentialForm,
        login_url: saved.login_url || website.url || "",
        username: saved.username || "",
        username_selector: saved.username_selector || "",
        password_selector: saved.password_selector || "",
        submit_selector: saved.submit_selector || "",
        success_selector: saved.success_selector || "",
        success_url: saved.success_url || "",
      };
      credentialInitialFormRef.current = savedForm;
      setCredentialForm(savedForm);
    }
  } catch (requestError) {
    setCredentialError(requestError.message || "Could not load login settings.");
  } finally {
    setCredentialBusy(false);
  }
};

const closeCredentialModal = useCallback(() => {
  if (credentialBusy) {
    return;
  }
  if (credentialForm && JSON.stringify(credentialForm) !== JSON.stringify(credentialInitialFormRef.current)
      && !window.confirm("Discard the login details you entered? Saved login settings will remain unchanged.")) {
    return;
  }
  setCredentialWebsite(null);
  setCredentialForm(null);
  setCredentialError("");
  setLoginTestResult(null);
  setShowCredentialPassword(false);
  setAdvancedCredentialSettings(false);
  requestAnimationFrame(() => credentialReturnFocusRef.current?.focus?.());
}, [credentialBusy, credentialForm]);

useEffect(() => {
  if (!credentialWebsite) return undefined;
  credentialDialogRef.current?.focus();
  const closeOnEscape = (event) => {
    if (event.key === "Escape" && !credentialBusy) closeCredentialModal();
  };
  document.addEventListener("keydown", closeOnEscape);
  return () => document.removeEventListener("keydown", closeOnEscape);
}, [credentialWebsite, credentialBusy, closeCredentialModal]);

const updateCredentialField = (event) => {
  const { name, value } = event.target;
  setCredentialForm((previous) => ({ ...previous, [name]: value }));
  setLoginTestResult(null);
};

const testWebsiteLogin = async () => {
  if (!credentialWebsite || !credentialForm) {
    return;
  }
  setCredentialBusy(true);
  setCredentialError("");
  setLoginTestResult(null);
  try {
    const response = await apiFetch(`/websites/${credentialWebsite.id}/credentials/test/`, {
      method: "POST",
      body: JSON.stringify(credentialForm),
    });
    if (!response.ok) {
      setCredentialTestError(response.status);
      return;
    }
    setLoginTestResult(await response.json());
  } catch (requestError) {
    console.error("Website login test request failed:", requestError);
    setLoginTestResult({ success: false, message: "Unable to verify login." });
  } finally {
    setCredentialBusy(false);
  }
};

const setCredentialTestError = (responseStatus) => {
  if (responseStatus === 404) {
    setCredentialError("This website is no longer available.");
  } else if (responseStatus === 401) {
    setCredentialError("Your session has expired. Redirecting to login.");
  } else {
    setCredentialError("Unable to test login. Check the settings and try again.");
  }
};

const saveWebsiteCredentials = async (event) => {
  event.preventDefault();
  if (!credentialWebsite || !credentialForm) {
    return;
  }
  setCredentialBusy(true);
  setCredentialError("");
  try {
    const method = credentialWebsite.credentials_configured ? "PUT" : "POST";
    const response = await apiFetch(`/websites/${credentialWebsite.id}/credentials/`, {
      method,
      body: JSON.stringify(credentialForm),
    });
    if (!response.ok) {
      setCredentialError(response.status === 404
        ? "This website is no longer available."
        : "Unable to save login settings. Please check the fields and try again.");
      return;
    }
    setWebsites((previous) => previous.map((website) => (
      website.id === credentialWebsite.id
        ? { ...website, requires_login: true, credentials_configured: true, login_status: "connected" }
        : website
    )));
    setCredentialWebsite(null);
    setCredentialForm(null);
    credentialInitialFormRef.current = null;
    setLoginTestResult(null);
  } catch (requestError) {
    console.error("Website login settings could not be saved:", requestError);
    setCredentialError("Unable to save login settings. Please try again.");
  } finally {
    setCredentialBusy(false);
  }
};

const removeWebsiteCredentials = async () => {
  if (!credentialWebsite || !credentialWebsite.credentials_configured) {
    return;
  }
  if (!window.confirm("Remove the saved login for this website?")) {
    return;
  }
  setCredentialBusy(true);
  setCredentialError("");
  try {
    const response = await apiFetch(`/websites/${credentialWebsite.id}/credentials/`, {
      method: "DELETE",
    });
    if (!response.ok) {
      setCredentialError("Unable to remove login settings. Please try again.");
      return;
    }
    setWebsites((previous) => previous.map((website) => (
      website.id === credentialWebsite.id
        ? { ...website, credentials_configured: false, status: "login_required" }
        : website
    )));
    setCredentialWebsite(null);
    setCredentialForm(null);
    setLoginTestResult(null);
  } catch (requestError) {
    console.error("Website login settings could not be removed:", requestError);
    setCredentialError("Unable to remove login settings. Please try again.");
  } finally {
    setCredentialBusy(false);
  }
};

  return (
    <div className="app app-shell">
      {mobileNavOpen && <button className="sidebar-backdrop" aria-label="Close navigation menu" onClick={() => setMobileNavOpen(false)} />}
      <aside id="primary-sidebar" className={`app-sidebar ${mobileNavOpen ? "is-open" : ""}`} aria-label="Main navigation">
        <button className="sidebar-brand" type="button" onClick={() => goToPage("dashboard")}>
          <BrandLogo className="sidebar-brand-logo" />
        </button>
        <div className="sidebar-label">WORKSPACE</div>
        <nav className="sidebar-navigation" aria-label="Workspace">
          <button type="button" className={`sidebar-link ${route === "/dashboard" ? "is-active" : ""}`} aria-current={route === "/dashboard" ? "page" : undefined} onClick={() => goToPage("dashboard")}><NavigationIcon name="dashboard" /><span>Dashboard</span></button>
          <button type="button" className={`sidebar-link ${route === "/websites" ? "is-active" : ""}`} aria-current={route === "/websites" ? "page" : undefined} onClick={() => { setProfileMenuOpen(false); setMobileNavOpen(false); navigate("/websites"); window.setTimeout(() => document.getElementById("websites")?.scrollIntoView({ behavior: "smooth" }), 80); }}><NavigationIcon name="websites" /><span>Websites</span></button>
          <button type="button" className={`sidebar-link ${activePage === "alerts" ? "is-active" : ""}`} aria-current={activePage === "alerts" ? "page" : undefined} onClick={() => goToPage("alerts")}><NavigationIcon name="alerts" /><span>Alerts</span>{unreadAlertCount > 0 && <span className="sidebar-count">{unreadAlertCount > 99 ? "99+" : unreadAlertCount}</span>}</button>
          <button type="button" className={`sidebar-link ${activePage === "history" ? "is-active" : ""}`} aria-current={activePage === "history" ? "page" : undefined} onClick={() => goToPage("history")}><NavigationIcon name="history" /><span>Activity History</span></button>
          <button type="button" className={`sidebar-link ${activePage === "settings" ? "is-active" : ""}`} aria-current={activePage === "settings" ? "page" : undefined} onClick={() => goToPage("settings")}><NavigationIcon name="settings" /><span>Settings</span></button>
        </nav>
        <div className="sidebar-note"><span className="sidebar-note-orb" aria-hidden="true">✦</span><p>Keep an eye on what matters.</p><small>Monitoring, made simple.</small></div>
      </aside>
      <div className="app-main-column">
      <header className="topbar">
        <button className="mobile-nav-toggle" type="button" aria-label="Open navigation menu" aria-expanded={mobileNavOpen} aria-controls="primary-sidebar" onClick={() => setMobileNavOpen((open) => !open)}><span /><span /><span /></button>
        <div className="topbar-page-title"><span className="topbar-eyebrow">WEBWATCH <span aria-hidden="true">/</span> WORKSPACE</span><h1>{pageTitle}</h1><p>{pageDescription}</p></div>
        <div className="navbar-account">
          <GoogleAccountLink email={currentUser.email} linked={googleLinked} onLinked={markGoogleLinked} />
          <div className="profile-menu-wrap">
            <button ref={profileTriggerRef} className="profile-avatar-button" type="button"
              aria-label={`Open profile menu for ${currentUser.name || currentUser.email}`}
              aria-haspopup="menu" aria-expanded={profileMenuOpen}
              onClick={() => setProfileMenuOpen((open) => !open)}>
              {`${currentUser.name?.trim()?.split(/\s+/).map((part) => part[0]).slice(0, 2).join("") || currentUser.email?.[0] || "W"}`.toUpperCase()}
            </button>
            {profileMenuOpen && <section className="profile-floating-panel" ref={profilePanelRef} aria-label="Account menu">
              <div className="profile-menu-heading"><span className="account-avatar" aria-hidden="true">{`${currentUser.name?.trim()?.split(/\s+/).map((part) => part[0]).slice(0, 2).join("") || currentUser.email?.[0] || "W"}`.toUpperCase()}</span>
                <span><strong>{currentUser.name || currentUser.email}</strong><small>{currentUser.email}</small></span></div>
              <div className="profile-menu-items" role="menu" onKeyDown={(event) => {
                if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const items = Array.from(profilePanelRef.current.querySelectorAll('[role="menuitem"]'));
                const current = items.indexOf(document.activeElement);
                const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 :
                  (current + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
                items[next]?.focus();
              }}>
                {[["profile", "Profile"], ["settings", "Settings"], ["history", "Activity History"]].map(([page, label], index) =>
                  <button key={page} ref={index === 0 ? profileFirstItemRef : undefined} role="menuitem" type="button"
                    onClick={() => goToPage(page)}>
                    {label}
                  </button>)}
                <button role="menuitem" type="button" className="profile-logout" onClick={() => {
                  onLogout().finally(() => setProfileMenuOpen(false));
                }}>Logout</button>
              </div>
            </section>}
          </div>
        </div>
      </header>

      {activePage === "alerts" ? <AlertsPage key={currentUser.id} onUnreadCount={setUnreadAlertCount} navigate={navigate} /> : ["profile", "settings", "history"].includes(activePage) ?
        <AccountPages key={`${currentUser.id}-${activePage}`} page={activePage} onThemeChange={changeTheme}
          onProfileUpdated={onProfileUpdated} onPasswordChanged={onPasswordChanged} onAccountDeleted={onAccountDeleted} /> : <main className="page-content dashboard-content">
        <section className="welcome-hero">
          <div className="welcome-copy">
            <p className="welcome-kicker"><span className="welcome-kicker-dot" /> MONITORING OVERVIEW</p>
            <h2>Welcome back, {welcomeName}</h2>
            <p>Keep track of the pages that matter. We’ll let you know when something meaningfully changes.</p>
            <button className="add-button" onClick={openModal}><span aria-hidden="true">＋</span> Add Website</button>
          </div>
          <div className="welcome-art" aria-hidden="true"><span className="welcome-art-glow" /><span className="welcome-art-orbit orbit-one" /><span className="welcome-art-orbit orbit-two" /><span className="welcome-art-center"><NavigationIcon name="websites" /></span><span className="welcome-art-spark spark-one">✦</span><span className="welcome-art-spark spark-two">✧</span></div>
        </section>

        <section className="stats">
          <article className="stat-card"><span className="stat-icon stat-icon-violet"><NavigationIcon name="websites" /></span><div><p>Total websites</p><h3>{websitesLoading ? <span className="metric-skeleton" /> : websites.length}</h3></div></article>
          <article className="stat-card"><span className="stat-icon stat-icon-green"><span aria-hidden="true">✓</span></span><div><p>Monitoring</p><h3>{websitesLoading ? <span className="metric-skeleton" /> : websites.filter((website) => website.status === "monitoring").length}</h3></div></article>
          <article className="stat-card"><span className="stat-icon stat-icon-amber"><span aria-hidden="true">↗</span></span><div><p>Recent changes</p><h3>{websitesLoading ? <span className="metric-skeleton" /> : websites.filter(hasRecentChange).length}</h3></div></article>
          <article className="stat-card"><span className="stat-icon stat-icon-rose"><span aria-hidden="true">!</span></span><div><p>Needs attention</p><h3>{websitesLoading ? <span className="metric-skeleton" /> : websites.filter((website) => website.status === "error" || website.status === "login_required").length}</h3></div></article>
        </section>

        <aside className="monitoring-tips" aria-labelledby="monitoring-tips-title">
          <div className="monitoring-tips-heading">
            <span className="monitoring-tips-icon" aria-hidden="true">💡</span>
            <h2 id="monitoring-tips-title">A little WebWatch tip ✨</h2>
          </div>
          <p className="monitoring-tips-intro">
            When adding a website to WebWatch, choose a stable page link whenever possible. Avoid pages with live timers, countdowns, auto-refreshing content, or continuously moving and scrolling elements, as these can create unnecessary detected changes.
          </p>
          <div className="monitoring-tips-guides">
            <section className="monitoring-tips-guide" aria-labelledby="tips-good-title">
              <h3 id="tips-good-title"><span aria-hidden="true">✓</span> Good to monitor</h3>
              <ul>
                <li>Product and information pages.</li>
                <li>Documentation and announcements.</li>
                <li>Stable pages where content changes are meaningful.</li>
              </ul>
            </section>
            <section className="monitoring-tips-guide avoid" aria-labelledby="tips-avoid-title">
              <h3 id="tips-avoid-title"><span aria-hidden="true">↗</span> Avoid when possible</h3>
              <ul>
                <li>Pages with live clocks or countdown timers.</li>
                <li>Pages with continuously changing numbers or auto-refreshing content.</li>
                <li>Pages where scrolling animations or dynamic effects change the captured page repeatedly.</li>
              </ul>
            </section>
          </div>
          <p className="monitoring-tips-footer">💡 Tip: Use the most direct URL for the specific content you want to track.</p>
        </aside>

        <section className="websites" id="websites">
          <div className="section-header">
            <div>
              <h2>Monitored Websites</h2>
              <p>Websites currently connected to WebWatch</p>
            </div>

            <button className="secondary-button" onClick={openModal}>
              + Add Website
            </button>
          </div>

          {deleteError && (
            <p className="delete-error" role="alert">
              {deleteError}
            </p>
          )}

          {websitesLoading ? (
            <div className="website-list-skeleton" role="status" aria-label="Loading websites"><span /><span /></div>
          ) : websitesError && websites.length === 0 ? (
            <div className="dashboard-load-error" role="alert"><span>{websitesError}</span><button type="button" onClick={fetchWebsites}>Retry</button></div>
          ) : websites.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon"><NavigationIcon name="websites" /></div>

              <h3>No websites yet</h3>

              <p>
                Add a website to start monitoring new job opportunities.
              </p>
            </div>
          ) : (
            <div className="website-list">
              {websites.map((website) => (
                <div className="website-card" key={website.id}>
                  <div className="website-card-main">
                    <div className="website-card-heading">
                      <div className="website-title-group">
                        <h3>{website.name}</h3>
                        <span className={`status ${website.status}`}>
                          <span className="status-indicator" aria-hidden="true" />
                          {website.requires_login && website.status === "login_required"
                            ? "🔐 Login Required"
                            : statusLabels[website.status] || website.status}
                        </span>
                      </div>
                      {hasRecentChange(website) && (
                        <span className="change-detected">
                          <span aria-hidden="true">!</span>
                          Change detected
                        </span>
                      )}
                    </div>

                    <a className="website-url" href={website.url} target="_blank" rel="noreferrer">
                      {website.url}
                    </a>
                    <p className="monitor-scope">Monitoring full page text and form values</p>
                    {website.snapshot_diagnostics && (
                      <p className="monitor-scope" aria-live="polite">
                        Snapshot: {website.snapshot_diagnostics.decision || "waiting"}
                        {website.snapshot_diagnostics.candidate_regions != null
                          ? ` · ${website.snapshot_diagnostics.candidate_regions} dynamic candidates`
                          : ""}
                        {website.snapshot_diagnostics.configured_ignored_regions != null
                          ? ` · ${website.snapshot_diagnostics.configured_ignored_regions} ignored by your selectors`
                          : ""}
                      </p>
                    )}
                    <dl className="website-activity">
                      <div>
                        <dt>Check interval</dt>
                        <dd>Every {website.check_interval} min</dd>
                      </div>
                      <div>
                        <dt>Last checked</dt>
                        <dd>
                          {formatDetectedAt(website.last_checked) || "Never"}
                        </dd>
                      </div>
                      <div>
                        <dt>Next due</dt>
                        <dd>
                          {formatDetectedAt(website.next_due_at) || "Not scheduled"}
                        </dd>
                      </div>
                      <div>
                        <dt>Last changed</dt>
                        <dd>
                          {formatDetectedAt(website.last_changed) || "No changes detected"}
                        </dd>
                      </div>
                    </dl>
                    {website.status === "error" && website.last_error && (
                      <p className="monitor-error" role="alert">
                        {website.last_error}
                      </p>
                    )}
                  </div>

                  <div className="website-actions">
                    <button
                      className="monitor-button history-button"
                      onClick={() => {
                        setFilterWebsite(website);
                        setFilterDraft((website.ignore_selectors || []).join("\n"));
                        setFilterError("");
                      }}
                    >
                      Detection filters
                    </button>
                    <button
                      className="monitor-button history-button"
                      onClick={() => viewChanges(website)}
                    >
                      View Changes
                    </button>

                    {website.status === "monitoring" ? (
                      <button
                        className="monitor-button stop"
                        onClick={() => stopMonitoring(website.id)}
                      >
                        Stop
                      </button>
                    ) : (
                      <button
                        className="monitor-button start"
                        onClick={() => startMonitoring(website.id)}
                      >
                        Start
                      </button>
                    )}

                    <button
                      className="monitor-button check-now-button"
                      onClick={() => checkNow(website.id)}
                      disabled={website.status === "stopped" || website.status === "login_required" || checkingWebsiteIds.has(website.id)}
                      title={website.status === "stopped" ? "Start monitoring to check this website" : undefined}
                    >
                      {checkingWebsiteIds.has(website.id) ? "Checking..." : "Check Now"}
                    </button>

                    {website.requires_login && (
                      <>
                        <button
                          className="monitor-button history-button credential-button"
                          onClick={() => openCredentialModal(website)}
                        >
                          {website.credentials_configured ? "Edit Login" : "Configure Login"}
                        </button>
                        {website.credentials_configured && (
                          <span className={`credential-configured credential-status-${website.login_status || "connected"}`} role="status">
                            {loginStatusLabels[website.login_status] || "Login configured"}
                          </span>
                        )}
                      </>
                    )}

                    <button
                      className="monitor-button delete-button"
                      onClick={() => {
                        setDeleteError("");
                        setDeleteTarget(website);
                      }}
                    >
                      Delete
                    </button>
                  </div>
                  {checkFeedback[website.id] && (
                    <p
                      className={`check-feedback ${checkFeedback[website.id].type}`}
                      role={checkFeedback[website.id].type === "error" ? "alert" : "status"}
                    >
                      {checkFeedback[website.id].message}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </main>}

      {/* Add Website Modal */}
      {showModal && (
        <div className="modal-overlay" onClick={closeModal}>
          <div
            className="modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <h2>Add Website</h2>
                <p>
                  Connect a website to start monitoring it.
                </p>
              </div>

              <button
                className="close-button"
                onClick={closeModal}
                disabled={saving}
              >
                ×
              </button>
            </div>

            <form onSubmit={handleSubmit}>
              <div className="form-group">
                <label htmlFor="name">Website Name</label>

                <input
                  id="name"
                  name="name"
                  type="text"
                  placeholder="Example: LinkedIn Jobs"
                  value={formData.name}
                  onChange={handleChange}
                  required
                />
              </div>

              <div className="form-group">
                <label htmlFor="ignore_selectors">Ignore dynamic regions (optional)</label>
                <textarea
                  id="ignore_selectors"
                  name="ignore_selectors"
                  rows="3"
                  placeholder={".countdown\n#live-ticker"}
                  value={formData.ignore_selectors}
                  onChange={handleChange}
                />
                <small className="field-help">
                  One CSS selector per line. Ignored regions are excluded from change detection.
                  Prices, stock, and other page text remain monitored by default.
                </small>
              </div>

              <div className="form-group">
                <label htmlFor="url">Website URL</label>

                <input
                  id="url"
                  name="url"
                  type="url"
                  placeholder="https://example.com/jobs"
                  value={formData.url}
                  onChange={handleChange}
                  required
                />
                <small className="field-help">
                  WebWatch checks text and form values across the full page.
                </small>
              </div>

              <div className="form-group">
                <label htmlFor="check_interval">
                  Check Interval
                </label>

                <div className="interval-input">
                  <input
                    id="check_interval"
                    name="check_interval"
                    type="number"
                    min="1"
                    value={formData.check_interval}
                    onChange={handleChange}
                    required
                  />

                  <span>minutes</span>
                </div>
              </div>

              <label className="login-option">
                <input
                  type="checkbox"
                  name="requires_login"
                  checked={formData.requires_login}
                  onChange={handleChange}
                />

                <span>
                  <strong>Requires login</strong>
                  <small>
                    Enable this if the website requires an account
                    to access its content.
                  </small>
                </span>
              </label>

              {error && (
                <div className="form-error">
                  {error}
                </div>
              )}

              <div className="modal-actions">
                <button
                  type="button"
                  className="cancel-button"
                  onClick={closeModal}
                  disabled={saving}
                >
                  Cancel
                </button>

                <button
                  type="submit"
                  className="save-button"
                  disabled={saving}
                >
                  {saving ? "Adding..." : "Add Website"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {filterWebsite && (
        <div className="modal-overlay" onClick={() => !filterSaving && setFilterWebsite(null)}>
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="filter-title" onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h2 id="filter-title">Detection filters</h2>
                <p>Choose dynamic regions to exclude from {filterWebsite.name}.</p>
              </div>
              <button className="close-button" type="button" onClick={() => setFilterWebsite(null)} disabled={filterSaving}>×</button>
            </div>
            <form onSubmit={saveWebsiteFilters}>
              <div className="form-group">
                <label htmlFor="website-ignore-selectors">CSS selectors, one per line</label>
                <textarea
                  id="website-ignore-selectors"
                  rows="4"
                  value={filterDraft}
                  onChange={(event) => setFilterDraft(event.target.value)}
                  placeholder={".countdown\n#live-ticker\n[data-dynamic-content=\"true\"]"}
                />
                <small className="field-help">Simple class, ID, and attribute selectors are supported. A filter change starts a fresh baseline. Price and stock changes remain monitored unless their region is explicitly excluded.</small>
              </div>
              {filterError && <div className="form-error" role="alert">{filterError}</div>}
              <div className="modal-actions">
                <button type="button" className="cancel-button" onClick={() => setFilterWebsite(null)} disabled={filterSaving}>Cancel</button>
                <button type="submit" className="save-button" disabled={filterSaving}>{filterSaving ? "Saving..." : "Save filters"}</button>
              </div>
            </form>
          </section>
        </div>
      )}

      {changeHistory && (
        <div className="modal-overlay" onClick={closeChangeHistory}>
          <section
            className="modal history-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="history-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-header history-header">
              <div>
                <p className="history-kicker">CHANGE HISTORY</p>
                <h2 id="history-title">{changeHistory.website.name}</h2>
                {!changeHistory.loading && !changeHistory.error && (
                  <p className="history-total">
                    {changeHistory.changes.length} detected {changeHistory.changes.length === 1 ? "change" : "changes"}
                  </p>
                )}
              </div>

              <button
                className="close-button"
                onClick={closeChangeHistory}
                aria-label="Close change history"
              >
                ×
              </button>
            </div>

            <div className="history-content" aria-live="polite">
              {historyNotice && <p className="history-update-notice" role="status">{historyNotice}</p>}
              {changeHistory.loading ? (
                <p className="history-message" role="status">
                  Loading change history...
                </p>
              ) : changeHistory.error ? (
                <div className="history-error" role="alert">
                  <p>{changeHistory.error}</p>
                  <button
                    className="history-retry"
                    onClick={() => viewChanges(changeHistory.website)}
                  >
                    Try again
                  </button>
                </div>
              ) : changeHistory.changes.length === 0 ? (
                <p className="history-message">
                  No changes detected yet.
                </p>
              ) : (
                <>
                  <div className="history-summary">
                    <span>Total detected changes</span>
                    <strong>{changeHistory.changes.length}</strong>
                  </div>

                  <div className="history-entries">
                    {changeHistory.changes.map((change, index) => (
                      <article className="history-entry" key={change.id}>
                        <div className="history-entry-heading">
                          <strong>
                            Change {changeHistory.changes.length - index}
                          </strong>
                          <time dateTime={change.detected_at}>
                            {formatDetectedAt(change.detected_at)}
                          </time>
                        </div>

                        <div className="history-values">
                          <div>
                            <span>Previous value</span>
                            <pre>{change.previous_value || "Unavailable for earlier changes"}</pre>
                          </div>
                          <div>
                            <span>New value</span>
                            <pre>{change.new_value || "No visible text or value"}</pre>
                            {change.previous_value && change.previous_value === change.new_value && (
                              <small className="history-value-note">
                                The displayed value stayed the same; the element HTML or attributes changed.
                              </small>
                            )}
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                </>
              )}
            </div>

            <div className="modal-actions history-actions">
              {!changeHistory.loading && !changeHistory.error && changeHistory.changes.length > 0 && (
                <button
                  type="button"
                  className="history-clear-button"
                  onClick={clearChangeHistory}
                >
                  Clear History
                </button>
              )}
              <button
                type="button"
                className="cancel-button"
                onClick={closeChangeHistory}
              >
                Close
              </button>
            </div>
          </section>
        </div>
      )}

      {credentialWebsite && credentialForm && (
        <div
          className="modal-overlay"
          onClick={() => !credentialBusy && closeCredentialModal()}
        >
          <section
            className="modal credential-modal"
            ref={credentialDialogRef}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-labelledby="credential-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <h2 id="credential-title">Configure Website Login</h2>
                <p className="credential-site-name">{credentialWebsite.name}</p>
                {credentialWebsite.credentials_configured && (
                  <p className="credential-saved-note"><span aria-hidden="true">✓</span> Login configured <span aria-hidden="true">·</span> Saved password is never displayed</p>
                )}
              </div>
              <button
                className="close-button"
                onClick={closeCredentialModal}
                disabled={credentialBusy}
                aria-label="Close login configuration"
              >
                ×
              </button>
            </div>

            {credentialBusy && !credentialForm.login_url ? (
              <p className="history-message" role="status">Loading login settings...</p>
            ) : (
              <form className="credential-form" onSubmit={saveWebsiteCredentials}>
                <div className="form-group">
                  <label htmlFor="credential-login-url">Login page URL</label>
                  <input id="credential-login-url" name="login_url" type="url" placeholder="https://example.com/login" value={credentialForm.login_url} onChange={updateCredentialField} required />
                </div>
                <div className="form-group">
                  <label htmlFor="credential-username">Username / Email</label>
                  <input id="credential-username" name="username" type="text" autoComplete="username" placeholder="Enter your username or email" value={credentialForm.username} onChange={updateCredentialField} required />
                </div>
                <div className="form-group">
                  <label htmlFor="credential-password">Password</label>
                  <div className="credential-password-control">
                    <input id="credential-password" name="password" type={showCredentialPassword ? "text" : "password"} autoComplete="new-password" placeholder={credentialWebsite.credentials_configured ? "Enter a new password to replace it" : "Enter your password"} value={credentialForm.password} onChange={updateCredentialField} required />
                    <button type="button" className="credential-password-toggle" onClick={() => setShowCredentialPassword((shown) => !shown)} aria-label={showCredentialPassword ? "Hide password" : "Show password"} aria-pressed={showCredentialPassword}>{showCredentialPassword ? "Hide" : "Show"}</button>
                  </div>
                  {credentialWebsite.credentials_configured && <small className="credential-password-note">Credentials are saved. Passwords are never displayed. Enter a new password only to replace the saved one.</small>}
                </div>
                <button type="button" className="credential-advanced-toggle" aria-expanded={advancedCredentialSettings} onClick={() => setAdvancedCredentialSettings((open) => !open)}>{advancedCredentialSettings ? "−" : "+"} Advanced Settings</button>
                {advancedCredentialSettings && <div className="credential-advanced-panel"><p>Use these CSS selectors only if automatic login form detection does not work. Leave fields blank to let WebWatch detect them.</p>
                  <div className="form-group"><label htmlFor="credential-username-selector">Username selector</label><input id="credential-username-selector" name="username_selector" type="text" placeholder='e.g. input[name="email"]' value={credentialForm.username_selector} onChange={updateCredentialField} /></div>
                  <div className="form-group"><label htmlFor="credential-password-selector">Password selector</label><input id="credential-password-selector" name="password_selector" type="text" placeholder='e.g. input[name="password"]' value={credentialForm.password_selector} onChange={updateCredentialField} /></div>
                  <div className="form-group"><label htmlFor="credential-submit-selector">Submit selector</label><input id="credential-submit-selector" name="submit_selector" type="text" placeholder='e.g. button[type="submit"]' value={credentialForm.submit_selector} onChange={updateCredentialField} /></div>
                  <div className="form-group"><label htmlFor="credential-success-selector">Optional success selector</label><small>Element that appears after successful sign-in.</small><input id="credential-success-selector" name="success_selector" type="text" value={credentialForm.success_selector} onChange={updateCredentialField} /></div>
                  <div className="form-group"><label htmlFor="credential-success-url">Optional success URL</label><small>URL pattern reached after successful sign-in.</small><input id="credential-success-url" name="success_url" type="url" value={credentialForm.success_url} onChange={updateCredentialField} /></div>
                </div>}

                {credentialError && <p className="form-error" role="alert">{credentialError}</p>}
                {loginTestResult && (
                  <p className={`login-test-result ${loginTestResult.success ? "success" : "error"}`} role="status">
                    {loginTestResult.success ? "✓" : "✕"} {loginTestResult.message}
                  </p>
                )}

                <div className="modal-actions credential-actions">
                  {credentialWebsite.credentials_configured && (
                    <button type="button" className="credential-remove" onClick={removeWebsiteCredentials} disabled={credentialBusy}>
                      Remove Login
                    </button>
                  )}
                  <button type="button" className="cancel-button" onClick={testWebsiteLogin} disabled={credentialBusy}>
                    {credentialBusy ? "Testing login..." : "Test Login"}
                  </button>
                  <button type="submit" className="save-button" disabled={credentialBusy || !credentialForm.login_url || !credentialForm.username || !credentialForm.password || !loginTestResult?.success}>
                    {credentialBusy ? "Please wait..." : "Save Login"}
                  </button>
                  <button type="button" className="cancel-button" onClick={closeCredentialModal} disabled={credentialBusy}>
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </section>
        </div>
      )}

      {deleteTarget && (
        <div
          className="modal-overlay"
          onClick={() => deletingWebsiteId === null && setDeleteTarget(null)}
        >
          <section
            className="modal delete-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <h2 id="delete-title">Delete this website?</h2>
                <p>
                  Are you sure you want to delete {deleteTarget.name}? Its change
                  history will also be deleted.
                </p>
              </div>
            </div>
            <div className="modal-actions">
              <button
                type="button"
                className="cancel-button"
                onClick={() => setDeleteTarget(null)}
                disabled={deletingWebsiteId !== null}
              >
                Cancel
              </button>
              <button
                type="button"
                className="confirm-delete-button"
                onClick={deleteWebsite}
                disabled={deletingWebsiteId !== null}
              >
                {deletingWebsiteId === deleteTarget.id ? "Deleting..." : "Delete"}
              </button>
            </div>
          </section>
        </div>
      )}
      </div>
    </div>
  );
}

function App() {
  const [route, setRoute] = useState(() => {
    const currentPath = window.location.pathname;
    const normalizedPath = normalizeAppRoute(currentPath, Boolean(getAuthToken()));
    if (normalizedPath !== currentPath) window.history.replaceState({}, "", normalizedPath);
    return normalizedPath;
  });
  const [currentUser, setCurrentUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(() => Boolean(getAuthToken()));
  const [authNotice, setAuthNotice] = useState("");

  const navigate = useCallback((path, replace = false) => {
    if (replace) {
      window.history.replaceState({}, "", path);
    } else {
      window.history.pushState({}, "", path);
    }
    setRoute(path);
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      const currentPath = window.location.pathname;
      const normalizedPath = normalizeAppRoute(currentPath, Boolean(getAuthToken()));
      if (normalizedPath !== currentPath) window.history.replaceState({}, "", normalizedPath);
      setRoute(normalizedPath);
    };
    const handleUnauthorized = () => {
      clearAuthToken();
      setCurrentUser(null);
      setAuthLoading(false);
      navigate("/login", true);
    };

    window.addEventListener("popstate", handlePopState);
    window.addEventListener("webwatch:unauthorized", handleUnauthorized);
    return () => {
      window.removeEventListener("popstate", handlePopState);
      window.removeEventListener("webwatch:unauthorized", handleUnauthorized);
    };
  }, [navigate]);

  useEffect(() => {
    if (!getAuthToken()) {
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 15_000);
    apiFetch("/auth/me/", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(response.status === 401
            ? "Your session expired. Please sign in again."
            : "We couldn't restore your session. Please sign in again.");
        }
        const user = await response.json();
        if (!cancelled) {
          setCurrentUser(user);
          setAuthLoading(false);
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          clearAuthToken();
          setCurrentUser(null);
          setAuthNotice(requestError.name === "AbortError"
            ? "Session restoration took too long. Please sign in again."
            : requestError instanceof TypeError
              ? "WebWatch couldn't connect to the server. Please sign in again."
              : requestError.message || "We couldn't restore your session. Please sign in again.");
          setAuthLoading(false);
          navigate("/login", true);
        }
      })
      .finally(() => {
        window.clearTimeout(timeoutId);
      });

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [navigate]);

  const establishSession = useCallback(async (token) => {
    saveAuthToken(token);

    const response = await apiFetch("/auth/me/");

    if (!response.ok) {
      clearAuthToken();
      throw new Error("Could not load your account. Please sign in again.");
    }

    setCurrentUser(await response.json());
    setAuthNotice("");
    navigate("/dashboard", true);
  }, [navigate]);

  const logout = async () => {
    try {
      await apiFetch("/auth/logout/", { method: "POST" });
    } catch {
      // Revoke the local session even when the server cannot be reached.
    } finally {
      clearAuthToken();
      setCurrentUser(null);
      navigate("/login", true);
    }
  };

  const updateProfile = useCallback((profile) => {
    setCurrentUser((user) => user ? { ...user, name: profile.name } : user);
  }, []);

  const passwordChanged = useCallback(() => {
    clearAuthToken();
    setCurrentUser(null);
    setAuthNotice("Your password was changed. Sign in with your new password.");
    navigate("/login", true);
  }, [navigate]);

  const accountDeleted = useCallback(() => {
    clearAuthToken(currentUser?.id);
    setCurrentUser(null);
    navigate("/login", true);
  }, [currentUser?.id, navigate]);

  if (authLoading) {
    return <main className="auth-loading" role="status" aria-live="polite">
      <BrandLogo className="auth-loading-logo" />
      <p className="auth-loading-tagline">Monitor today. Stay ahead.</p>
      <span className="auth-loading-indicator" aria-hidden="true"><i /><i /><i /></span>
      <span className="auth-loading-message">Restoring your workspace…</span>
    </main>;
  }

  if (route === "/developer/login") {
    return <DeveloperLogin navigate={navigate} onAccessGranted={() => setCurrentUser(null)} />;
  }

  if (route === "/developer") {
    return <DeveloperConsole navigate={navigate} />;
  }

  if (currentUser) {
    return <Dashboard key={currentUser.id} currentUser={currentUser} route={route} navigate={navigate}
      onLogout={logout} onProfileUpdated={updateProfile} onPasswordChanged={passwordChanged} onAccountDeleted={accountDeleted} />;
  }

  if (route === "/forgot-password" || route.startsWith("/reset-password/")) {
    return <PasswordRecovery key={route} path={route} navigate={navigate} />;
  }

  const authRoute = route === "/register" ? "/register" : "/login";
  return (
    <AuthPage
      path={authRoute}
      navigate={navigate}
      onAuthenticated={establishSession}
      notice={authNotice}
    />
  );
}

export default App;
