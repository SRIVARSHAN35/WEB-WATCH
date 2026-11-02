import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL, apiFetch, clearAuthToken, getAuthToken, readJsonResponse, saveAuthToken } from "./api.js";
import BrandLogo from "./BrandLogo.jsx";

function DeveloperLogin({ navigate, onAccessGranted }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const previousToken = getAuthToken();
    clearAuthToken();
    try {
      const loginResponse = await apiFetch("/auth/login/", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const loginPayload = await readJsonResponse(loginResponse);
      if (!loginResponse.ok) throw new Error(loginPayload.detail || "Developer sign-in failed.");

      saveAuthToken(loginPayload.token);
      const accessResponse = await apiFetch("/developer/overview/");
      if (!accessResponse.ok) {
        const accessPayload = await readJsonResponse(accessResponse);
        throw new Error(accessResponse.status === 403
          ? "This account does not have developer access."
          : accessPayload.detail || "Developer access could not be verified.");
      }
      onAccessGranted();
      navigate("/developer", true);
    } catch (requestError) {
      clearAuthToken();
      if (previousToken) saveAuthToken(previousToken);
      setError(requestError.message || "Developer sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="developer-login-screen">
      <section className="developer-login-card">
        <BrandLogo className="developer-brand" />
        <p className="developer-eyebrow">RESTRICTED ACCESS</p>
        <h1>Developer console</h1>
        <p>Sign in with your WebWatch developer account. To create or recover local developer access, run <code>python manage.py bootstrap_developer</code> from the backend folder.</p>
        <form onSubmit={submit} className="developer-login-form">
          <label>Email<input type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {error && <p className="developer-error" role="alert">{error}</p>}
          <button type="submit" disabled={busy}>{busy ? "Checking access…" : "Sign in to developer console"}</button>
        </form>
        <button className="developer-back-link" type="button" onClick={() => navigate("/login")}>Return to WebWatch login</button>
      </section>
    </main>
  );
}

function displayValue(value, field) {
  if (value === null || value === undefined || value === "") return "—";
  if (field.type === "boolean") return value ? "Yes" : "No";
  if (field.type === "datetime") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  }
  return String(value);
}

function validationMessage(payload, fallback) {
  if (typeof payload?.detail === "string") return payload.detail;
  const entries = Object.entries(payload || {});
  if (!entries.length) return fallback;
  return entries.map(([field, errors]) => `${field}: ${Array.isArray(errors) ? errors.join(" ") : String(errors)}`).join(" ");
}

function DeveloperConsole({ navigate }) {
  const [overview, setOverview] = useState(null);
  const [tables, setTables] = useState([]);
  const [selectedTable, setSelectedTable] = useState("");
  const [pageData, setPageData] = useState(null);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [filterValue, setFilterValue] = useState("");
  const [sort, setSort] = useState("id");
  const [direction, setDirection] = useState("desc");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(true);
  const [detailBusy, setDetailBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [formValues, setFormValues] = useState({});
  const [auditMode, setAuditMode] = useState(false);
  const requestId = useRef(0);

  const selectedTableData = tables.find((table) => table.key === selectedTable);
  const requestedRecordId = typeof selectedRecord === "number" ? selectedRecord : null;

  const loadInitial = async () => {
    try {
      const [overviewResponse, tablesResponse] = await Promise.all([
        apiFetch("/developer/overview/"),
        apiFetch("/developer/tables/"),
      ]);
      const [overviewPayload, tablesPayload] = await Promise.all([
        readJsonResponse(overviewResponse),
        readJsonResponse(tablesResponse),
      ]);
      if (overviewResponse.status === 403 || tablesResponse.status === 403) {
        setError("This account does not have developer access.");
        return;
      }
      if (!overviewResponse.ok || !tablesResponse.ok) {
        throw new Error(overviewPayload.detail || tablesPayload.detail || "Developer data could not be loaded.");
      }
      setOverview(overviewPayload);
      setTables(tablesPayload.tables || []);
      setSelectedTable((current) => current || tablesPayload.tables?.[0]?.key || "");
    } catch (requestError) {
      setError(requestError.message || "Developer data could not be loaded.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadInitial(); }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setActiveQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!selectedTable || auditMode) return undefined;
    const currentRequestId = ++requestId.current;
    const params = new URLSearchParams({ page: String(page), page_size: "25", sort, direction });
    if (activeQuery) params.set("q", activeQuery);
    if (filter && filterValue) {
      params.set("filter", filter);
      params.set("value", filterValue);
    }
    apiFetch(`/developer/tables/${encodeURIComponent(selectedTable)}/records/?${params}`)
      .then(async (response) => {
        const payload = await readJsonResponse(response);
        if (!response.ok) throw new Error(payload.detail || "Records could not be loaded.");
        if (currentRequestId === requestId.current) setPageData(payload);
      })
      .catch((requestError) => {
        if (currentRequestId === requestId.current) setError(requestError.message || "Records could not be loaded.");
      })
      .finally(() => {
        if (currentRequestId === requestId.current) setBusy(false);
      });
    return () => { requestId.current += 1; };
  }, [selectedTable, auditMode, activeQuery, filter, filterValue, sort, direction, page]);

  useEffect(() => {
    if (!auditMode) return undefined;
    let active = true;
    const params = new URLSearchParams({ page: String(page), page_size: "50" });
    if (activeQuery) params.set("q", activeQuery);
    apiFetch(`/developer/audit-logs/?${params}`)
      .then(async (response) => {
        const payload = await readJsonResponse(response);
        if (!response.ok) throw new Error(payload.detail || "Audit history could not be loaded.");
        if (active) setPageData({ ...payload, model: { label: "Developer audit log", fields: [
          { key: "actor_email", label: "Developer" }, { key: "action", label: "Action" },
          { key: "model_key", label: "Model" }, { key: "record_id", label: "Record ID" },
          { key: "changed_fields", label: "Changed fields" }, { key: "created_at", label: "Time", type: "datetime" },
        ] } });
      })
      .catch((requestError) => { if (active) setError(requestError.message || "Audit history could not be loaded."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [auditMode, page, activeQuery]);

  useEffect(() => {
    if (requestedRecordId === null || auditMode) return undefined;
    let active = true;
    apiFetch(`/developer/tables/${encodeURIComponent(selectedTable)}/records/${encodeURIComponent(requestedRecordId)}/`)
      .then(async (response) => {
        const payload = await readJsonResponse(response);
        if (!response.ok) throw new Error(payload.detail || "Record details could not be loaded.");
        if (active) {
          setSelectedRecord(payload);
          setFormValues(payload.record || {});
        }
      })
      .catch((requestError) => { if (active) setError(requestError.message || "Record details could not be loaded."); })
      .finally(() => { if (active) setDetailBusy(false); });
    return () => { active = false; };
  }, [selectedTable, requestedRecordId, auditMode]);

  const fetchAuditLogs = async () => {
    setAuditMode(true);
    setSelectedRecord(null);
    setBusy(true);
    setError("");
    setPage(1);
    setQuery("");
    setActiveQuery("");
    setPageData(null);
  };

  const recordFields = useMemo(() => selectedRecord?.model?.fields || [], [selectedRecord]);

  const saveRecord = async (event) => {
    event.preventDefault();
    if (!selectedRecord || saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const patch = Object.fromEntries(selectedRecord.editable_fields.map((key) => [key, formValues[key]]));
      const response = await apiFetch(`/developer/tables/${encodeURIComponent(selectedTable)}/records/${encodeURIComponent(selectedRecord.record.id)}/`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      const payload = await readJsonResponse(response);
      if (!response.ok) throw new Error(validationMessage(payload, "The record did not pass validation."));
      setSelectedRecord(payload);
      setFormValues(payload.record || {});
      setNotice("Record saved and audit logged.");
      await reloadRecords();
    } catch (requestError) {
      setError(requestError.message || "Record could not be saved.");
    } finally {
      setSaving(false);
    }
  };

  const reloadRecords = async () => {
    if (!selectedTable || auditMode) return;
    const params = new URLSearchParams({ page: String(page), page_size: "25", sort, direction });
    if (activeQuery) params.set("q", activeQuery);
    if (filter && filterValue) { params.set("filter", filter); params.set("value", filterValue); }
    const response = await apiFetch(`/developer/tables/${encodeURIComponent(selectedTable)}/records/?${params}`);
    const payload = await readJsonResponse(response);
    if (response.ok) setPageData(payload);
  };

  const deleteRecord = async () => {
    if (!selectedRecord || saving) return;
    const consequences = selectedRecord.deletion?.consequences || [];
    const message = `Delete ${selectedRecord.model.label} record ${selectedRecord.record.id}?\n\n${consequences.join("\n")}\n\nVerify that a current database backup exists before continuing. This console does not create backups.`;
    if (!window.confirm(message)) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const recordId = selectedRecord.record.id;
      const response = await apiFetch(`/developer/tables/${encodeURIComponent(selectedTable)}/records/${encodeURIComponent(recordId)}/`, {
        method: "DELETE",
        body: JSON.stringify({ confirm: true, backup_confirmed: true, record_id: String(recordId) }),
      });
      const payload = await readJsonResponse(response);
      if (!response.ok) throw new Error(payload.detail || "Record could not be deleted.");
      setSelectedRecord(null);
      setNotice(`${selectedRecord.model.label} record ${recordId} deleted.`);
      await reloadRecords();
      await loadInitial();
    } catch (requestError) {
      setError(requestError.message || "Record could not be deleted.");
    } finally {
      setSaving(false);
    }
  };

  const logout = async () => {
    try { await apiFetch("/auth/logout/", { method: "POST" }); } catch { /* Local session is still cleared. */ }
    clearAuthToken();
    navigate("/developer/login", true);
  };

  const selectTable = (key) => {
    setBusy(true);
    setError("");
    setAuditMode(false);
    setSelectedRecord(null);
    setPageData(null);
    setQuery("");
    setActiveQuery("");
    setFilter("");
    setFilterValue("");
    setPage(1);
    setSelectedTable(key);
  };

  return (
    <main className="developer-console">
      <header className="developer-topbar">
        <BrandLogo />
        <div><span>RESTRICTED WORKSPACE</span><h1>Developer console</h1></div>
        <div className="developer-topbar-actions">
          <a href={new URL("/admin/", API_BASE_URL).href} target="_blank" rel="noreferrer">Django admin</a>
          <button type="button" onClick={logout}>Sign out</button>
        </div>
      </header>

      {error && <div className={error.includes("does not have developer access") ? "developer-alert developer-alert-denied" : "developer-alert"} role="alert">
        <span>{error}</span>
        {error.includes("does not have developer access") && <button type="button" onClick={() => navigate("/developer/login")}>Use a staff account</button>}
      </div>}
      {notice && <p className="developer-notice" role="status">{notice}</p>}

      <section className="developer-overview-grid" aria-label="Application overview">
        <article><span>Total users</span><strong>{overview?.counts?.users ?? "—"}</strong></article>
        <article><span>Total websites</span><strong>{overview?.counts?.websites ?? "—"}</strong></article>
        <article><span>Total alerts</span><strong>{overview?.counts?.alerts ?? "—"}</strong></article>
        <article className={overview?.database?.healthy ? "health-card is-healthy" : "health-card is-unhealthy"}>
          <span>Database</span><strong>{overview ? (overview.database.healthy ? "Connected" : "Unavailable") : "Checking…"}</strong>
          {overview?.database?.healthy && <small>{overview.database.vendor}</small>}
        </article>
      </section>

      {!overview?.recent_errors_available && <p className="developer-muted-panel">Recent application errors are unavailable because this project has no persistent error log configured.</p>}

      <div className="developer-workspace">
        <aside className="developer-sidebar" aria-label="Database tables">
          <h2>Database explorer</h2>
          {tables.map((table) => <button key={table.key} type="button" className={!auditMode && selectedTable === table.key ? "is-selected" : ""} onClick={() => selectTable(table.key)}>
            <span>{table.label}</span><small>{table.count}</small>
          </button>)}
          <button type="button" className={auditMode ? "is-selected" : ""} onClick={fetchAuditLogs}><span>Developer audit log</span></button>
        </aside>

        <section className="developer-browser" aria-label="Record browser">
          <div className="developer-browser-heading">
            <div><span>SAFE MODEL ALLOWLIST</span><h2>{auditMode ? "Developer audit log" : selectedTableData?.label || "Select a model"}</h2></div>
            {pageData && <small>{pageData.count} record{pageData.count === 1 ? "" : "s"}</small>}
          </div>
          {auditMode ? <div className="developer-controls"><label className="developer-search">Search audit log<input type="search" value={query} maxLength={200} placeholder="Search developer, model, record ID…" onChange={(event) => { setBusy(true); setError(""); setQuery(event.target.value); setPage(1); }} /></label></div> : selectedTableData && <div className="developer-controls">
            <label className="developer-search">Search<input type="search" value={query} maxLength={200} placeholder={`Search ${selectedTableData.label.toLowerCase()}…`} onChange={(event) => { setBusy(true); setError(""); setQuery(event.target.value); setPage(1); }} /></label>
            {selectedTableData.filters.map((item) => <label key={item.key}>{item.label}<select value={filter === item.key ? filterValue : ""} onChange={(event) => { setBusy(true); setError(""); setFilter(event.target.value ? item.key : ""); setFilterValue(event.target.value); setPage(1); }}><option value="">All</option>{item.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>)}
          </div>}
          {!pageData || (busy && !pageData.results.length) ? <p className="developer-empty">Loading database records…</p> : pageData.results.length ? <>
            <div className="developer-table-scroll"><table className="developer-table"><thead><tr>{pageData.model.fields.map((field) => <th key={field.key}>{field.sortable && !auditMode ? <button type="button" onClick={() => { setBusy(true); if (sort === field.key) setDirection((value) => value === "asc" ? "desc" : "asc"); else { setSort(field.key); setDirection("asc"); } }}>{field.label}{sort === field.key ? direction === "asc" ? " ↑" : " ↓" : ""}</button> : field.label}</th>)}</tr></thead><tbody>
              {pageData.results.map((record) => <tr key={record.id || `${record.model_key}-${record.record_id}`} onClick={() => { if (!auditMode) { setDetailBusy(true); setSelectedRecord(record.id); } }} className={!auditMode ? "is-clickable" : ""}>
                {pageData.model.fields.map((field) => <td key={field.key}>{Array.isArray(record[field.key]) ? record[field.key].join(", ") : displayValue(record[field.key], field)}</td>)}
              </tr>)}
            </tbody></table></div>
            <div className="developer-pagination"><span>Page {pageData.page} of {pageData.num_pages || 1}</span><button type="button" disabled={pageData.page <= 1 || busy} onClick={() => { setBusy(true); setPage((value) => Math.max(1, value - 1)); }}>Previous</button><button type="button" disabled={pageData.page >= pageData.num_pages || busy} onClick={() => { setBusy(true); setPage((value) => value + 1); }}>Next</button></div>
          </> : <p className="developer-empty">{busy ? "Loading database records…" : "No records found."}</p>}
        </section>

        {selectedRecord && !auditMode && <aside className="developer-detail" aria-label="Record details">
          <div className="developer-detail-heading"><div><span>{selectedRecord.model.label}</span><h2>Record {selectedRecord.record.id}</h2></div><button type="button" aria-label="Close record details" onClick={() => setSelectedRecord(null)}>×</button></div>
          {detailBusy ? <p>Loading record…</p> : <>
            <dl>{recordFields.filter((field) => !selectedRecord.editable_fields.includes(field.key)).map((field) => <div key={field.key}><dt>{field.label}</dt><dd>{displayValue(selectedRecord.record[field.key], field)}</dd></div>)}</dl>
            {selectedRecord.editable_fields.length > 0 && <form className="developer-edit-form" onSubmit={saveRecord}>
              <h3>Editable fields</h3>
              {recordFields.filter((field) => selectedRecord.editable_fields.includes(field.key)).map((field) => <label key={field.key}>{field.label}
                {field.type === "boolean" ? <input type="checkbox" checked={Boolean(formValues[field.key])} onChange={(event) => setFormValues((values) => ({ ...values, [field.key]: event.target.checked }))} />
                  : field.type === "choice" ? <select value={formValues[field.key] ?? ""} onChange={(event) => setFormValues((values) => ({ ...values, [field.key]: event.target.value }))}>{(field.options || []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
                    : <input type={field.type === "integer" ? "number" : field.type === "url" ? "url" : "text"} value={formValues[field.key] ?? ""} onChange={(event) => setFormValues((values) => ({ ...values, [field.key]: field.type === "integer" ? Number(event.target.value) : event.target.value }))} />}
              </label>)}
              <button type="submit" disabled={saving}>{saving ? "Saving…" : "Save changes"}</button>
            </form>}
            {selectedRecord.deletion?.allowed && <div className="developer-delete-zone"><p>{selectedRecord.deletion.consequences.join(" ")}</p><button type="button" className="developer-delete-button" disabled={saving} onClick={deleteRecord}>{saving ? "Deleting…" : "Delete record"}</button></div>}
          </>}
        </aside>}
      </div>
    </main>
  );
}

export { DeveloperConsole, DeveloperLogin };
