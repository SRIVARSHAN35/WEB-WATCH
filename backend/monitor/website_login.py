from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .url_safety import guard_public_request


CHALLENGE_MARKERS = (
    "captcha", "verify you are human", "one-time password", "one time password",
    "two-factor", "two factor", "security code", "enter otp", "cloudflare",
)
LOGIN_FAILURE_MARKERS = (
    "invalid password", "incorrect password", "incorrect username", "invalid credentials",
    "could not sign in", "couldn't sign in", "login failed", "sign in failed",
)
DEFAULT_SELECTORS = {
    "username_selector": {"", 'input[type="email"]'},
    "password_selector": {"", 'input[type="password"]'},
    "submit_selector": {"", 'button[type="submit"]'},
}


def additional_authentication_required(page):
    current_url = page.url.lower()
    if any(marker in current_url for marker in ("captcha", "challenge", "two-factor", "2fa", "otp")):
        return True
    try:
        page_text = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        return False
    return any(marker in page_text for marker in CHALLENGE_MARKERS)


def _technical_login_failure(error):
    error_text = str(error).lower()
    if isinstance(error, PlaywrightTimeoutError) or "timeout" in error_text:
        message = "Connection timed out while loading the login page."
    elif "connection refused" in error_text or "err_connection_refused" in error_text:
        message = "Connection refused by the login website."
    elif any(marker in error_text for marker in ("name_not_resolved", "getaddrinfo failed", "dns")):
        message = "Unable to resolve the login website address."
    elif isinstance(error, (PlaywrightError, OSError, ConnectionError)):
        message = "Unable to connect to the login website."
    else:
        message = "Unable to complete the website login check."
    return {"success": False, "message": message, "technical_error": True, "error_type": type(error).__name__}


_LOGIN_FORM_DETECTION_SCRIPT = r"""() => {
  const visible = element => {
    if (!element || !element.isConnected || element.disabled) return false;
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
  };
  const forms = Array.from(document.forms);
  const inputs = Array.from(document.querySelectorAll('input'));
  const formKey = element => element.form ? forms.indexOf(element.form) : -1;
  const formText = element => (element.form?.innerText || '').toLowerCase();
  const loginWords = /sign\s*in|log\s*in|login|continue|account/;
  const registerWords = /sign\s*up|register|create\s+account|join\s+now/;
  const visibleInputs = inputs.map((element, index) => ({element, index}))
    .filter(item => visible(item.element) && !['hidden', 'submit', 'button', 'reset', 'image'].includes((item.element.type || '').toLowerCase()));
  const textFor = element => [element.name, element.id, element.type, element.autocomplete,
    element.placeholder, element.getAttribute('aria-label'),
    Array.from(element.labels || []).map(label => label.innerText).join(' ')]
    .filter(Boolean).join(' ').toLowerCase();
  const usernameScore = item => {
    const element = item.element;
    const type = (element.type || '').toLowerCase();
    const text = textFor(element);
    if (['password', 'search', 'tel', 'number', 'checkbox', 'radio'].includes(type)) return -100;
    let score = type === 'email' ? 38 : type === 'text' ? 4 : 0;
    if ((element.autocomplete || '').toLowerCase() === 'username') score += 60;
    if (/username|user\s*name|email|e-mail|login\s*id/.test(text)) score += 35;
    if (/search|query|newsletter|subscribe|first\s*name|last\s*name/.test(text)) score -= 55;
    const words = formText(element);
    if (loginWords.test(words)) score += 15;
    if (registerWords.test(words) && !/sign\s*in|log\s*in|login/.test(words)) score -= 45;
    return score;
  };
  const passwordScore = item => {
    const element = item.element;
    const text = textFor(element);
    const words = formText(element);
    let score = 10;
    if ((element.autocomplete || '').toLowerCase() === 'current-password') score += 50;
    if (/password|passcode/.test(text)) score += 18;
    if (/confirm|repeat|new\s*password|create\s*password/.test(text)) score -= 65;
    if (loginWords.test(words)) score += 15;
    if (registerWords.test(words) && !/sign\s*in|log\s*in|login/.test(words)) score -= 55;
    return score;
  };
  const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]'));
  const submitFor = key => {
    const items = buttons.map((element, index) => ({element, index}))
      .filter(item => visible(item.element) && (formKey(item.element) === key || (key === -1 && !item.element.form)));
    const named = items.filter(item => /sign\s*in|log\s*in|login|continue|next|submit|verify/.test(
      [item.element.innerText, item.element.value, item.element.getAttribute('aria-label')].filter(Boolean).join(' ').toLowerCase()
    ));
    if (named.length === 1) return {index: named[0].index, ambiguous: false};
    if (named.length > 1) return {index: null, ambiguous: true};
    const submit = items.filter(item => (item.element.type || '').toLowerCase() === 'submit');
    if (submit.length === 1) return {index: submit[0].index, ambiguous: false};
    if (submit.length > 1) return {index: null, ambiguous: true};
    if (items.length === 1) return {index: items[0].index, ambiguous: false};
    return {index: null, ambiguous: items.length > 1};
  };
  const passwords = visibleInputs.filter(item => (item.element.type || '').toLowerCase() === 'password');
  const passwordForms = new Set(passwords.map(item => formKey(item.element)));
  const eligibleForms = [];
  for (const key of passwordForms) {
    const form = key >= 0 ? forms[key] : null;
    const text = (form?.innerText || '').toLowerCase();
    const users = visibleInputs.filter(item => formKey(item.element) === key && usernameScore(item) >= 20);
    const formLooksLogin = loginWords.test(text) || users.length > 0;
    const formLooksRegistration = registerWords.test(text) && !/sign\s*in|log\s*in|login/.test(text);
    if (formLooksLogin && !formLooksRegistration) eligibleForms.push({key, users});
  }
  if (eligibleForms.length > 1) return {ambiguous: true, reason: 'multiple_forms'};
  if (eligibleForms.length === 1) {
    const {key, users} = eligibleForms[0];
    const passwordCandidates = passwords.filter(item => formKey(item.element) === key)
      .map(item => ({...item, score: passwordScore(item)})).sort((a,b) => b.score - a.score);
    const userCandidates = users.map(item => ({...item, score: usernameScore(item)})).sort((a,b) => b.score - a.score);
    if (passwordCandidates.length > 1 && passwordCandidates[0].score === passwordCandidates[1].score)
      return {ambiguous: true, reason: 'multiple_password_fields'};
    if (userCandidates.length > 1 && userCandidates[0].score === userCandidates[1].score)
      return {ambiguous: true, reason: 'multiple_username_fields'};
    const submit = submitFor(key);
    if (submit.ambiguous) return {ambiguous: true, reason: 'multiple_submit_controls'};
    return {ambiguous: false, form_index: key, username_index: userCandidates[0]?.index ?? null,
      password_index: passwordCandidates[0]?.index ?? null, submit_index: submit.index};
  }

  // Username-first pages commonly expose a single username input and a Continue button.
  const usernameCandidates = visibleInputs.map(item => ({...item, score: usernameScore(item)}))
    .filter(item => item.score >= 25).sort((a,b) => b.score - a.score);
  const plausible = [];
  for (const item of usernameCandidates) {
    const key = formKey(item.element);
    const form = key >= 0 ? forms[key] : null;
    const text = (form?.innerText || '').toLowerCase();
    if (registerWords.test(text) && !/sign\s*in|log\s*in|login/.test(text)) continue;
    plausible.push({item, key});
  }
  const distinctForms = new Set(plausible.map(candidate => candidate.key));
  if (distinctForms.size > 1) return {ambiguous: true, reason: 'multiple_forms'};
  if (!plausible.length) return {ambiguous: false, username_index: null, password_index: null, submit_index: null};
  if (plausible.length > 1 && plausible[0].item.score === plausible[1].item.score)
    return {ambiguous: true, reason: 'multiple_username_fields'};
  const key = plausible[0].key;
  const submit = submitFor(key);
  if (submit.ambiguous) return {ambiguous: true, reason: 'multiple_submit_controls'};
  return {ambiguous: false, form_index: key, username_index: plausible[0].item.index,
    password_index: null, submit_index: submit.index};
}"""


def _custom_selector(credentials, field):
    selector = str(credentials.get(field, "") or "").strip()
    return selector if selector and selector not in DEFAULT_SELECTORS.get(field, set()) else ""


def _unique_visible_locator(page, selector, field_name):
    try:
        matches = page.locator(selector)
        count = matches.count()
        visible = [matches.nth(index) for index in range(count) if matches.nth(index).is_visible() and matches.nth(index).is_enabled()]
    except Exception:
        return None, f"The {field_name} selector is invalid. Check Advanced Settings."
    if len(visible) > 1:
        return None, f"The {field_name} selector matches multiple visible fields. Make it more specific in Advanced Settings."
    if not visible:
        return None, f"Could not find a visible {field_name} field. Open Advanced Settings to provide a selector."
    return visible[0], ""


def detect_login_fields(page, credentials=None):
    """Return a conservative set of visible login controls on the current page."""
    credentials = credentials or {}
    manual = {}
    for key, label in (("username_selector", "username"), ("password_selector", "password")):
        selector = _custom_selector(credentials, key)
        if selector:
            locator, error = _unique_visible_locator(page, selector, label)
            if error:
                return {"ambiguous": False, "error": error}
            manual[key] = locator

    try:
        detected = page.evaluate(_LOGIN_FORM_DETECTION_SCRIPT)
    except Exception:
        detected = None
    if not isinstance(detected, dict):
        detected = {"ambiguous": False, "username_index": None, "password_index": None, "submit_index": None}
    if detected.get("ambiguous"):
        return detected

    username = manual.get("username_selector")
    password = manual.get("password_selector")
    if username is None and detected.get("username_index") is not None:
        username = page.locator("input").nth(detected["username_index"])
    if password is None and detected.get("password_index") is not None:
        password = page.locator("input").nth(detected["password_index"])
    if username is not None and password is not None:
        try:
            username_form = username.evaluate("(field) => field.form ? Array.from(document.forms).indexOf(field.form) : -1")
            password_form = password.evaluate("(field) => field.form ? Array.from(document.forms).indexOf(field.form) : -1")
            if username_form != password_form:
                return {"ambiguous": True, "reason": "fields_in_different_forms"}
        except Exception:
            pass

    submit = None
    manual_submit = _custom_selector(credentials, "submit_selector")
    if manual_submit:
        submit, error = _unique_visible_locator(page, manual_submit, "submit")
        if error:
            return {"ambiguous": False, "error": error}
    elif detected.get("submit_index") is not None:
        submit = page.locator('button, input[type="submit"], input[type="button"], [role="button"]').nth(detected["submit_index"])
    return {
        **detected,
        "username_locator": username,
        "password_locator": password,
        "submit_locator": submit,
    }


def _login_failure_visible(page):
    try:
        text = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        return False
    return any(marker in text for marker in LOGIN_FAILURE_MARKERS)


def is_login_page(page, credentials):
    try:
        current = urlsplit(page.url)
        login = urlsplit(credentials.get("login_url", ""))
        same_login_path = (
            current.scheme == login.scheme
            and current.netloc.lower() == login.netloc.lower()
            and current.path.rstrip("/").lower() == login.path.rstrip("/").lower()
        )
    except ValueError:
        same_login_path = False
    fields = detect_login_fields(page, credentials)
    has_password = fields.get("password_locator") is not None
    has_login_pair = has_password and (fields.get("username_locator") is not None or bool(credentials.get("username")))
    return not fields.get("ambiguous") and has_login_pair and (same_login_path or fields.get("form_index") is not None)


def _submit_login_form(page, fields, credentials):
    custom_submit = _custom_selector(credentials, "submit_selector")
    if custom_submit:
        submit, error = _unique_visible_locator(page, custom_submit, "submit")
        if error:
            return False, error
        submit.click(timeout=5000)
        return True, ""
    submit = fields.get("submit_locator")
    if submit is not None:
        submit.click(timeout=5000)
        return True, ""
    active_field = fields.get("password_locator") or fields.get("username_locator")
    if active_field is None:
        return False, "Could not identify a safe login submit action. Open Advanced Settings to configure selectors."
    active_field.press("Enter", timeout=5000)
    return True, ""


def _wait_after_submit(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_timeout(300)
    except Exception:
        pass


def _success_selector_visible(page, selector):
    if not selector:
        return False
    try:
        matches = page.locator(selector)
        return matches.count() == 1 and matches.is_visible(timeout=1500)
    except Exception:
        return False


def login_page(page, credentials):
    try:
        page.goto(credentials["login_url"], wait_until="domcontentloaded", timeout=30000)
        if additional_authentication_required(page):
            return {"success": False, "message": "Additional authentication is required. Complete it manually and try again."}

        username_submitted = False
        for _ in range(3):
            fields = detect_login_fields(page, credentials)
            if fields.get("ambiguous"):
                return {"success": False, "message": "Multiple possible login forms were found. Open Advanced Settings and choose the correct fields."}
            if fields.get("error"):
                return {"success": False, "message": fields["error"]}
            username_field = fields.get("username_locator")
            password_field = fields.get("password_locator")
            submitted_password_this_step = password_field is not None
            if username_field is None and password_field is None:
                return {"success": False, "message": "Could not identify a login form. Open Advanced Settings to provide CSS selectors."}

            if username_field is not None:
                username_field.fill(credentials["username"], timeout=5000)
                username_submitted = True
            if password_field is not None:
                if not username_submitted and not credentials.get("username_selector"):
                    return {"success": False, "message": "Could not identify the username field. Open Advanced Settings to provide a selector."}
                password_field.fill(credentials["password"], timeout=5000)

            did_submit, submit_error = _submit_login_form(page, fields, credentials)
            if not did_submit:
                return {"success": False, "message": submit_error}
            _wait_after_submit(page)
            if additional_authentication_required(page):
                return {"success": False, "message": "This website requires CAPTCHA, MFA, or another manual authentication step."}

            if _success_selector_visible(page, credentials.get("success_selector", "")):
                return {"success": True, "message": "Login successful."}
            if credentials.get("success_url") and credentials["success_url"].casefold() in page.url.casefold():
                after = detect_login_fields(page, credentials)
                if after.get("password_locator") is None and not _login_failure_visible(page):
                    return {"success": True, "message": "Login successful."}

            remaining = detect_login_fields(page, credentials)
            if remaining.get("ambiguous"):
                return {"success": False, "message": "The login result is ambiguous. Open Advanced Settings and verify the form selectors."}
            if remaining.get("error"):
                return {"success": False, "message": remaining["error"]}
            if remaining.get("password_locator") is not None:
                return {"success": False, "message": "Login could not be verified. Check the credentials or Advanced Settings."}
            if _login_failure_visible(page):
                return {"success": False, "message": "Login failed. Check the username and password."}
            if submitted_password_this_step:
                # A disappearing login form is useful evidence for SPA flows; a URL
                # change by itself is deliberately insufficient.
                if remaining.get("password_locator") is None and remaining.get("username_locator") is None:
                    return {"success": True, "message": "Login successful."}
                return {"success": False, "message": "Login could not be verified. Check the credentials or Advanced Settings."}
            if username_field is not None and remaining.get("password_locator") is not None:
                continue
            return {"success": False, "message": "Login could not be confidently verified. Check Advanced Settings."}

        return {"success": False, "message": "The login form requires additional steps that WebWatch could not verify."}
    except Exception as error:
        return _technical_login_failure(error)


def test_website_login(credentials):
    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.route("**/*", guard_public_request)
            result = login_page(page, credentials)
            if not result.get("success"):
                return {"success": False, "message": result.get("message", "Login could not be verified.")}
            try:
                state = context.storage_state()
            except Exception:
                return {"success": False, "message": "Login succeeded, but WebWatch could not securely capture the session. Try again."}
            if not isinstance(state, dict):
                return {"success": False, "message": "Login succeeded, but WebWatch could not securely capture the session. Try again."}
            return {"success": True, "message": "Login successful. You can now save this configuration.", "storage_state": state}
    except Exception:
        return {"success": False, "message": "Unable to verify login. Check the URL and try again."}
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
