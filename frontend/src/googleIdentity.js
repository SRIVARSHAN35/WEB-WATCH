import { buildGoogleIdentityConfig } from "./googleIdentityConfig.js";

export const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

let initializedClientId = null;
let activeCredentialHandler = null;

export function mountGoogleButton(container, onCredential, options = {}, onReady = () => {}) {
  if (!GOOGLE_CLIENT_ID || !container) {
    return () => {};
  }

  let cancelled = false;
  let scriptLoadHandler = null;
  let script = document.querySelector('script[data-webwatch-google="true"]');
  activeCredentialHandler = onCredential;

  const initializeGoogle = () => {
    if (cancelled || !window.google?.accounts?.id || !container) {
      return;
    }

    if (initializedClientId !== GOOGLE_CLIENT_ID) {
      window.google.accounts.id.initialize(
        buildGoogleIdentityConfig(
          GOOGLE_CLIENT_ID,
          (response) => activeCredentialHandler?.(response),
        ),
      );
      initializedClientId = GOOGLE_CLIENT_ID;
    }

    container.replaceChildren();
    window.google.accounts.id.renderButton(container, {
      type: options.type || "standard",
      theme: "outline",
      size: "large",
      text: "continue_with",
      shape: options.shape || (options.type === "icon" ? "circle" : "rectangular"),
      width: Math.min(options.width || 340, container.clientWidth || options.width || 340),
    });
    onReady();
  };

  if (!script) {
    script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.dataset.webwatchGoogle = "true";
    scriptLoadHandler = initializeGoogle;
    script.addEventListener("load", scriptLoadHandler, { once: true });
    document.head.appendChild(script);
  } else if (window.google?.accounts?.id) {
    initializeGoogle();
  } else {
    scriptLoadHandler = initializeGoogle;
    script.addEventListener("load", scriptLoadHandler, { once: true });
  }

  return () => {
    cancelled = true;
    if (script && scriptLoadHandler) {
      script.removeEventListener("load", scriptLoadHandler);
    }
    if (activeCredentialHandler === onCredential) {
      activeCredentialHandler = null;
    }
    container.replaceChildren();
  };
}
