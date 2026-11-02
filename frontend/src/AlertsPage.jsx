import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./api.js";

const localDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short",
  }).format(date);
};

export default function AlertsPage({ onUnreadCount, navigate }) {
  const [filter, setFilter] = useState("all");
  const [alerts, setAlerts] = useState([]);
  const [count, setCount] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState(null);

  const load = useCallback(async (nextPage = 1, selectedFilter = "all") => {
    try {
      const response = await apiFetch(`/alerts/?filter=${selectedFilter}&page=${nextPage}`);
      if (!response.ok) throw new Error("Unable to load alerts.");
      const data = await response.json();
      setAlerts((current) => nextPage === 1 ? data.results : [...current, ...data.results]);
      setCount(data.count);
      setUnreadCount(data.unread_count);
      setHasMore(data.has_more);
      setPage(nextPage);
      onUnreadCount(data.unread_count);
    } catch {
      setError("Alerts could not be loaded. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [onUnreadCount]);

  useEffect(() => {
    const initialRequest = window.setTimeout(() => { void load(1, filter); }, 0);
    const refreshInterval = window.setInterval(() => { void load(1, filter); }, 15_000);
    return () => {
      window.clearTimeout(initialRequest);
      window.clearInterval(refreshInterval);
    };
  }, [filter, load]);

  const goToDashboard = () => navigate?.("/dashboard");

  const markRead = async (id) => {
    const response = await apiFetch(`/alerts/${id}/mark-read/`, { method: "POST" });
    if (!response.ok) throw new Error("Unable to mark alert as read.");
    const updated = await response.json();
    setAlerts((items) => items.map((item) => item.id === id ? updated : item));
    if (detail?.id === id) setDetail(updated);
    const nextCount = Math.max(0, unreadCount - 1);
    setUnreadCount(nextCount);
    onUnreadCount(nextCount);
    if (filter === "unread") setAlerts((items) => items.filter((item) => item.id !== id));
    return updated;
  };

  const openAlert = async (alert) => {
    setError("");
    try {
      const response = await apiFetch(`/alerts/${alert.id}/`);
      if (!response.ok) throw new Error();
      const data = await response.json();
      setDetail(data);
      if (!data.is_read) {
        const updated = await markRead(data.id);
        setDetail(updated);
      }
    } catch {
      setError("This alert could not be opened. Please refresh and try again.");
    }
  };

  const markAll = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/alerts/mark-all-read/", { method: "POST" });
      if (!response.ok) throw new Error();
      setUnreadCount(0);
      onUnreadCount(0);
      if (filter === "unread") setAlerts([]);
      else setAlerts((items) => items.map((item) => ({ ...item, is_read: true })));
      setDetail((item) => item ? { ...item, is_read: true } : item);
    } catch {
      setError("Alerts could not be marked as read. Please try again.");
    } finally { setBusy(false); }
  };

  return (
    <main className="alerts-page">
      <div className="alerts-heading">
        <span className="visually-hidden">Alert controls</span>
        <button className="secondary-button" onClick={() => { setLoading(true); setError(""); load(1, filter); }} disabled={loading}>Refresh</button>
      </div>
      <div className="alerts-summary"><span>{count} total alerts</span><span>{unreadCount} unread</span>
        <button className="monitor-button history-button" onClick={markAll} disabled={busy || unreadCount === 0}>
          {busy ? "Marking..." : "Mark all as read"}
        </button>
      </div>
      <div className="alerts-filters" role="group" aria-label="Filter alerts">
        {["all", "unread"].map((value) => <button key={value} className={filter === value ? "selected" : ""}
          onClick={() => { setLoading(true); setError(""); setFilter(value); }}>{value === "all" ? "All" : "Unread"}</button>)}
      </div>
      {error && <div className="alerts-error" role="alert">{error}<button onClick={() => { setLoading(true); setError(""); load(1, filter); }}>Retry</button></div>}
      {loading && alerts.length === 0 ? <div className="alerts-skeleton" role="status" aria-label="Loading alerts"><span /><span /><span /></div> : alerts.length === 0 ?
        <div className="empty-state alerts-empty"><div className="empty-icon">✓</div><h3>You're all caught up!</h3>
          <p>Website change alerts will appear here when your monitored websites change.</p></div> :
        <div className="alerts-list" aria-live="polite" aria-relevant="additions">{alerts.map((alert) => <article key={alert.id} className={`alert-card ${alert.is_read ? "read" : "unread"}`}>
          <button className="alert-open" onClick={() => openAlert(alert)}>
            <span className="alert-dot" aria-hidden="true" />
            <span className="alert-copy"><strong>{alert.website.name}</strong><span>{alert.summary}</span><time>{localDate(alert.detected_at)}</time></span>
          </button>
          <button className="alert-dashboard-link" type="button" onClick={goToDashboard}>View dashboard</button>
          {!alert.is_read && <button className="monitor-button history-button" disabled={busy} onClick={async () => {
            setBusy(true); try { await markRead(alert.id); } catch { setError("Alert could not be marked as read."); } finally { setBusy(false); }
          }}>Mark as read</button>}
        </article>)}</div>}
      {hasMore && <button className="secondary-button alerts-more" disabled={loading} onClick={() => { setLoading(true); load(page + 1, filter); }}>{loading ? "Loading..." : "Load more"}</button>}
      {detail && <div className="modal-overlay" onClick={() => setDetail(null)}><section className="modal alert-detail" role="dialog" aria-modal="true" aria-labelledby="alert-detail-title" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header"><div><h2 id="alert-detail-title">Alert details</h2><p>{detail.summary}</p></div><button className="modal-close" onClick={() => setDetail(null)} aria-label="Close">×</button></div>
        <dl><dt>Website</dt><dd>{detail.website.name}</dd><dt>Detected</dt><dd>{localDate(detail.detected_at)}</dd><dt>Status</dt><dd>{detail.is_read ? "Read" : "Unread"}</dd></dl>
        {/^https?:\/\//i.test(detail.website.url) && <a href={detail.website.url} target="_blank" rel="noopener noreferrer">Visit website</a>}
        <button className="alert-dashboard-link" type="button" onClick={goToDashboard}>View dashboard</button>
        <button className="secondary-button" onClick={() => setDetail(null)}>Back to alerts</button>
      </section></div>}
    </main>
  );
}
