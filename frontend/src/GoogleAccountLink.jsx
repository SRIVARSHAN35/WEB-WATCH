import { useEffect, useRef, useState } from "react";
import { apiFetch } from "./api.js";
import { GOOGLE_CLIENT_ID, mountGoogleButton } from "./googleIdentity.js";

function GoogleAccountLink({ email, linked, onLinked }) {
  const buttonRef = useRef(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID || linked || !expanded || !buttonRef.current) {
      return undefined;
    }

    const handleCredential = async ({ credential }) => {
      if (!credential) {
        setMessage("Google did not return a sign-in credential. Please try again.");
        return;
      }

      setBusy(true);
      setMessage("");
      try {
        const response = await apiFetch("/auth/google/link/", {
          method: "POST",
          body: JSON.stringify({ credential }),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Unable to link this Google account.");
        }
        onLinked();
        setMessage("Google sign-in is linked to this WebWatch account.");
      } catch (error) {
        setMessage(error.message || "Unable to link this Google account. Please try again.");
      } finally {
        setBusy(false);
      }
    };

    return mountGoogleButton(buttonRef.current, handleCredential, { width: 210 });
  }, [expanded, linked, onLinked]);

  return (
    <div className="google-account-link">
      {linked ? (
        <span className="google-linked-status">Google linked</span>
      ) : (
        <>
          <button
            className="google-link-start"
            type="button"
            onClick={() => setExpanded((value) => !value)}
          >
            Link Google
          </button>
          {expanded && (
            <div className="google-link-popover" role="dialog" aria-label="Link Google account">
              <button
                className="google-link-close"
                type="button"
                aria-label="Close Google linking"
                onClick={() => setExpanded(false)}
              >
                ×
              </button>
              <strong>Link your Google account</strong>
              <p>Continue with the Google account for {email}.</p>
              {!GOOGLE_CLIENT_ID ? (
                <span className="google-link-message" role="status">Google sign-in is not configured.</span>
              ) : (
                <div className={busy ? "google-link-button is-busy" : "google-link-button"} ref={buttonRef} />
              )}
              {message && <span className="google-link-message" role="status">{message}</span>}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default GoogleAccountLink;
