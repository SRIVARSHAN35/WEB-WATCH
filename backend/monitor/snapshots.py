"""Stable, privacy-conscious DOM snapshots for website monitoring."""

import hashlib
import json
import re
from dataclasses import dataclass


SNAPSHOT_VERSION = 2
MAX_IGNORE_SELECTORS = 10
MAX_IGNORE_SELECTOR_LENGTH = 180
MAX_DOM_NODES = 5000

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_-]*"
_ATTRIBUTE = rf"\[{_IDENTIFIER}(?:::{_IDENTIFIER})?(?:\s*=\s*(?:\"[A-Za-z0-9 _.-]{{1,80}}\"|'[A-Za-z0-9 _.-]{{1,80}}'|[A-Za-z0-9_.:-]+))?\]"
_COMPOUND = rf"(?:[A-Za-z][A-Za-z0-9-]*)?(?:[.#]{_IDENTIFIER}|{_ATTRIBUTE})+"
_SAFE_SELECTOR = re.compile(rf"{_COMPOUND}(?:\s+{_COMPOUND})*\Z")


@dataclass(frozen=True)
class Snapshot:
    content: str
    display_value: str
    legacy_hash: str | None
    diagnostics: dict

    @property
    def content_hash(self):
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def validate_ignore_selectors(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError("Enter ignore selectors as a list of CSS selectors.")
    if len(value) > MAX_IGNORE_SELECTORS:
        raise ValueError(f"Use no more than {MAX_IGNORE_SELECTORS} ignore selectors per website.")

    selectors = []
    for raw_selector in value:
        if not isinstance(raw_selector, str):
            raise ValueError("Each ignore selector must be text.")
        selector = raw_selector.strip()
        if not selector:
            continue
        if len(selector) > MAX_IGNORE_SELECTOR_LENGTH:
            raise ValueError(
                f"Ignore selectors must be {MAX_IGNORE_SELECTOR_LENGTH} characters or fewer."
            )
        if not _SAFE_SELECTOR.fullmatch(selector):
            raise ValueError(
                "Use simple CSS selectors such as .countdown, #live-ticker, "
                "[data-dynamic-content=\"true\"], or .banner .timer. "
                "Pseudo-classes, wildcards, and comma-separated selectors are not supported."
            )
        if selector not in selectors:
            selectors.append(selector)
    return selectors


STABILIZE_PAGE_SCRIPT = r"""async ({minMs, maxMs, quietMs, maxNodes}) => {
  const started = performance.now();
  const body = document.body;
  const repeatedNodes = new WeakSet();
  const candidateNodes = new Set();
  const changeCounts = new WeakMap();
  const reasons = new Set();
  const tokenPattern = /(?:^|[\s_-])(timer|countdown|clock|ticker|marquee|carousel|slider|slideshow|animated[-_ ]?banner)(?:$|[\s_-])/i;
  const motionPattern = /(?:animation|transition|transform|will-change)\s*:/i;

  const reasonFor = (element) => {
    if (!element || element.nodeType !== 1) return "";
    if (element.tagName === "MARQUEE") return "marquee";
    if (element.getAttribute("data-dynamic-content") === "true") return "explicit_dynamic";
    for (const name of ["id", "class", "data-testid", "data-role", "data-type"]) {
      const value = typeof element.className === "string" && name === "class"
        ? element.className
        : element.getAttribute(name) || "";
      const match = value.match(tokenPattern);
      if (match) return match[1].toLowerCase().replace(/[ _]/g, "-");
    }
    if (motionPattern.test(element.getAttribute("style") || "")) return "animated-style";
    return "";
  };

  const candidateFor = (node) => {
    let element = node?.nodeType === 1 ? node : node?.parentElement;
    for (let depth = 0; element && element !== body && depth < 12; depth += 1, element = element.parentElement) {
      const reason = reasonFor(element);
      if (reason) return { element, reason };
    }
    // Track repeated changes in a small named region, but do not automatically
    // suppress it unless the changing text is a narrowly recognized timer.
    element = node?.nodeType === 1 ? node : node?.parentElement;
    for (let depth = 0; element && element !== body && depth < 8; depth += 1, element = element.parentElement) {
      const text = element.textContent || "";
      if ((element.id || element.classList?.length) && text.length <= 300) {
        return { element, reason: "repeated-dom-updates" };
      }
    }
    return null;
  };

  let inspected = 0;
  if (body) {
    const candidates = body.querySelectorAll("*");
    inspected = Math.min(candidates.length + 1, maxNodes);
    for (const element of Array.from(candidates).slice(0, maxNodes)) {
      const reason = reasonFor(element);
      if (reason) {
        candidateNodes.add(element);
        reasons.add(reason);
      }
    }
  }

  let observer;
  let timeout;
  let quietTimer;
  const shouldWatch = body && typeof MutationObserver !== "undefined";
  if (shouldWatch) {
    observer = new MutationObserver((records) => {
      let meaningfulMutation = false;
      for (const record of records) {
        const candidate = candidateFor(record.target);
        if (candidate) {
          candidateNodes.add(candidate.element);
          reasons.add(candidate.reason);
          const nextCount = (changeCounts.get(candidate.element) || 0) + 1;
          changeCounts.set(candidate.element, nextCount);
          if (nextCount >= 2) repeatedNodes.add(candidate.element);
        } else {
          meaningfulMutation = true;
        }
      }
      if (meaningfulMutation) {
        clearTimeout(quietTimer);
        quietTimer = setTimeout(finish, quietMs);
      }
    });

    await new Promise((resolve) => {
      let finished = false;
      const finish = () => {
        if (finished) return;
        if (performance.now() - started < minMs) return;
        finished = true;
        observer.disconnect();
        clearTimeout(timeout);
        clearTimeout(quietTimer);
        resolve();
      };
      observer.observe(body, { subtree: true, childList: true, characterData: true });
      quietTimer = setTimeout(finish, quietMs);
      timeout = setTimeout(() => {
        if (finished) return;
        finished = true;
        observer.disconnect();
        clearTimeout(quietTimer);
        resolve();
      }, maxMs);
      setTimeout(finish, minMs);
    });
  }

  window.__webwatchSnapshotObservation = {
    repeatedNodes,
    candidateNodes,
    elementsInspected: inspected,
    candidateRegions: candidateNodes.size,
    candidateReasons: Array.from(reasons).sort(),
    observationMs: Math.round(performance.now() - started),
  };
  return {
    elements_inspected: inspected,
    candidate_regions: candidateNodes.size,
    candidate_reasons: Array.from(reasons).sort(),
    observation_ms: Math.round(performance.now() - started),
  };
}"""


EXTRACT_SNAPSHOT_SCRIPT = r"""(elements, options) => {
  const observation = window.__webwatchSnapshotObservation || {
    repeatedNodes: new WeakSet(), candidateNodes: new Set(),
    elementsInspected: 0, candidateRegions: 0, candidateReasons: [], observationMs: 0,
  };
  const ignoreSelectors = options.ignoreSelectors || [];
  const ignoredElements = new Set();
  let invalidSelector = false;

  const matchesIgnored = (element) => {
    for (const selector of ignoreSelectors) {
      try {
        if (element.matches(selector)) return true;
      } catch {
        invalidSelector = true;
      }
    }
    return false;
  };
  const tokenPattern = /(?:^|[\s_-])(timer|countdown|clock|ticker|marquee|carousel|slider|slideshow|animated[-_ ]?banner)(?:$|[\s_-])/i;
  const timerPattern = /\b\d{1,2}:\d{2}(?::\d{2})?\b/g;
  const timerUnitPattern = /\b\d+(?:\.\d+)?\s*(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)\b/gi;
  const timeOnlyPattern = /^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s*$/;
  const motionPropertyPattern = /^(?:animation(?:-.+)?|transition(?:-.+)?|transform|will-change)$/i;
  const transientClassPattern = /^(?:is-)?(?:active|current|visible|animating|animated|transitioning|previous|next|slick-current|swiper-slide-active)$/i;

  const ownReason = (element) => {
    if (element.tagName === "MARQUEE") return "marquee";
    if (element.getAttribute("data-dynamic-content") === "true") return "explicit_dynamic";
    for (const name of ["id", "class", "data-testid", "data-role", "data-type"]) {
      const value = typeof element.className === "string" && name === "class"
        ? element.className
        : element.getAttribute(name) || "";
      const match = value.match(tokenPattern);
      if (match) return match[1].toLowerCase().replace(/[ _]/g, "-");
    }
    if (/\b(?:animation|transition|transform|will-change)\s*:/i.test(element.getAttribute("style") || "")) return "animated-style";
    return "";
  };
  const normalizeTimer = (value) => value
    .replace(timerPattern, "[timer]")
    .replace(timerUnitPattern, "[timer]");
  const isTimerReason = (reason) => ["timer", "countdown", "clock"].includes(reason);
  const isMotionReason = (reason) => ["ticker", "marquee", "carousel", "slider", "slideshow", "animated-banner", "animated-style"].includes(reason);
  const cleanMotionStyle = (element) => {
    const style = element.getAttribute("style");
    if (!style) return;
    const stableRules = style.split(";").map((rule) => rule.trim()).filter(Boolean).filter((rule) => {
      const separator = rule.indexOf(":");
      return separator > 0 && !motionPropertyPattern.test(rule.slice(0, separator).trim());
    });
    if (stableRules.length) element.setAttribute("style", stableRules.join("; "));
    else element.removeAttribute("style");
  };

  let configuredIgnoredRegions = 0;
  let explicitDynamicRegions = 0;
  let normalizedTimerRegions = 0;
  let motionNormalizedRegions = 0;
  let candidateRegions = 0;
  let rootIgnored = false;
  let emptyDom = false;

  const cloneAndFilter = (sourceRoot) => {
    const cloneRoot = sourceRoot.cloneNode(true);
    const walk = (source, clone, inheritedReason, isRoot = false) => {
      const reason = ownReason(source) || inheritedReason || "";
      const candidate = Boolean(reason || observation.candidateNodes?.has(source));
      if (candidate) candidateRegions += 1;

      if (!isRoot && matchesIgnored(source)) {
        configuredIgnoredRegions += 1;
        ignoredElements.add(source);
        clone.remove();
        return;
      }
      if (!isRoot && source.getAttribute("data-dynamic-content") === "true") {
        explicitDynamicRegions += 1;
        clone.remove();
        return;
      }
      if (["SCRIPT", "STYLE", "TEMPLATE"].includes(source.tagName)) {
        clone.remove();
        return;
      }
      if (isRoot && matchesIgnored(source)) rootIgnored = true;

      if (isMotionReason(reason)) {
        cleanMotionStyle(clone);
        const normalizedClasses = Array.from(clone.classList || []).filter((name) => !transientClassPattern.test(name));
        if (normalizedClasses.length) clone.setAttribute("class", normalizedClasses.join(" "));
        else if (clone.hasAttribute("class")) clone.removeAttribute("class");
        motionNormalizedRegions += 1;
      }
      if (source.tagName === "MARQUEE") {
        for (const name of ["behavior", "direction", "loop", "scrollamount", "scrolldelay"]) clone.removeAttribute(name);
      }

      const timerRegion = isTimerReason(reason)
        || (observation.repeatedNodes?.has(source) && timeOnlyPattern.test(source.textContent || ""));
      if (timerRegion && source !== sourceRoot) normalizedTimerRegions += 1;

      if (source instanceof HTMLInputElement || source instanceof HTMLTextAreaElement || source instanceof HTMLSelectElement) {
        clone.value = source.value;
      }
      const originalChildren = Array.from(source.childNodes);
      const clonedChildren = Array.from(clone.childNodes);
      originalChildren.forEach((child, index) => {
        const clonedChild = clonedChildren[index];
        if (!clonedChild) return;
        if (child.nodeType === Node.ELEMENT_NODE) {
          walk(child, clonedChild, reason, false);
        } else if (child.nodeType === Node.TEXT_NODE && timerRegion) {
          clonedChild.textContent = normalizeTimer(clonedChild.textContent || "");
        }
      });
      if (timerRegion && source.childNodes.length === 0) clone.textContent = normalizeTimer(clone.textContent || "");
    };
    walk(sourceRoot, cloneRoot, "", true);
    return cloneRoot;
  };

  const results = elements.map((sourceRoot) => {
    const clone = cloneAndFilter(sourceRoot);
    const tag = sourceRoot.tagName.toLowerCase();
    const text = (clone.innerText ?? clone.textContent ?? "").trim();
    const hasValue = ["INPUT", "TEXTAREA", "SELECT"].includes(sourceRoot.tagName);
    const rootValue = hasValue ? clone.value ?? "" : null;
    const controls = Array.from(clone.querySelectorAll("input, textarea, select")).map((control) => ({
      tag: control.tagName.toLowerCase(),
      name: control.getAttribute("name") || "",
      id: control.id || "",
      type: control.getAttribute("type") || "",
      autocomplete: control.getAttribute("autocomplete") || "",
      value: String(control.value ?? ""),
    }));
    const attributes = Array.from(clone.attributes)
      .map((attribute) => [attribute.name, attribute.value])
      .sort(([left], [right]) => left.localeCompare(right));
    return {
      tag,
      text,
      value: rootValue,
      controls,
      attributes,
      html: clone.innerHTML,
      legacyText: (sourceRoot.innerText ?? sourceRoot.textContent ?? "").trim(),
      rootIgnored,
    };
  });
  const isSensitive = (control) => {
    const descriptor = `${control.name || ""} ${control.id || ""} ${control.type || ""} ${control.autocomplete || ""}`.toLowerCase();
    return ["password", "hidden"].includes(String(control.type || "").toLowerCase())
      || /password|token|cookie|session|secret|authorization/.test(descriptor);
  };
  const displayText = results.map((item) => {
    if (item.value === null) return item.text;
    const attrs = Object.fromEntries(item.attributes || []);
    return isSensitive({ name: attrs.name, id: attrs.id, type: attrs.type, autocomplete: attrs.autocomplete })
      ? "[redacted]"
      : item.value;
  }).join("\n").trim();
  const controls = results.flatMap((item) => item.controls);
  const controlValues = controls
    .filter((control) => control.value)
    .map((control) => isSensitive(control) ? "[redacted]" : control.value);
  const displayValue = [displayText, ...controlValues].filter(Boolean).join("\n").trim();
  const serialized = JSON.stringify(results.map(({ legacyText, rootIgnored, ...item }) => item));
  const hasMarkup = results.some((item) => /<[^>]+>/.test(item.html));
  emptyDom = !displayValue && !hasMarkup;
  return {
    serialized,
    display_value: displayValue,
    legacy_text: results.map((item) => item.legacyText).join("\n").trim(),
    diagnostics: {
      elements_inspected: observation.elementsInspected || elements.length,
      candidate_regions: Math.max(observation.candidateRegions || 0, candidateRegions),
      candidate_reasons: observation.candidateReasons || [],
      configured_ignored_regions: configuredIgnoredRegions,
      explicit_dynamic_regions: explicitDynamicRegions,
      normalized_timer_regions: normalizedTimerRegions,
      motion_normalized_regions: motionNormalizedRegions,
      observation_ms: observation.observationMs || 0,
      ignored_root: rootIgnored,
      invalid_selector: invalidSelector,
      empty_dom: emptyDom,
    },
  };
}"""


def stabilize_page(page, ignore_selectors=()):
    return page.evaluate(
        STABILIZE_PAGE_SCRIPT,
        {
            "minMs": 850,
            "maxMs": 1800,
            "quietMs": 300,
            "maxNodes": MAX_DOM_NODES,
            "ignoreSelectors": list(ignore_selectors),
        },
    )


def extract_snapshot(page, selector, ignore_selectors=()):
    """Extract a deterministic snapshot and diagnostics without retaining page text in diagnostics."""
    locator = page.locator(selector)
    try:
        elements = locator.evaluate_all(
            EXTRACT_SNAPSHOT_SCRIPT,
            {"ignoreSelectors": list(ignore_selectors)},
        )
    except TypeError:
        # Older test doubles in downstream projects only accept a script.
        elements = locator.evaluate_all(EXTRACT_SNAPSHOT_SCRIPT)
    if not isinstance(elements, dict) and not isinstance(elements, list):
        # Preserve lightweight legacy test doubles and callers that only expose
        # locator().inner_text(). The production Playwright path returns a list.
        body_text = page.locator("body").inner_text().strip()
        serialized = body_text
        return Snapshot(
            serialized,
            body_text[:5000],
            hashlib.sha256(body_text.encode("utf-8")).hexdigest() if selector.strip().lower() == "body" else None,
            {"elements_inspected": 1, "candidate_regions": 0, "configured_ignored_regions": 0,
             "explicit_dynamic_regions": 0, "normalized_timer_regions": 0,
             "motion_normalized_regions": 0, "observation_ms": 0},
        )
    if isinstance(elements, dict):
        result = elements
    elif not elements:
        raise ValueError("The CSS selector did not match any elements on the page.")
    else:
        result = elements[0]

    if isinstance(elements, list) and elements and "serialized" not in elements[0]:
        # Compatibility for clients/mocks using the previous element-by-element
        # result shape. Production Playwright returns the aggregate object above.
        display_parts = []
        for element in elements:
            value = element.get("value")
            attrs = dict(element.get("attributes") or [])
            descriptor = " ".join(str(attrs.get(key, "")) for key in ("name", "id", "type", "autocomplete")).lower()
            if value is not None:
                display_parts.append("[redacted]" if attrs.get("type", "").lower() in {"password", "hidden"} or re.search(r"password|token|cookie|session|secret|authorization", descriptor) else str(value))
            else:
                display_parts.append(str(element.get("text", "")))
            for control in element.get("controls", []):
                control_descriptor = " ".join(str(control.get(key, "")) for key in ("name", "id", "type", "autocomplete")).lower()
                if control.get("value"):
                    display_parts.append("[redacted]" if control.get("type", "").lower() in {"password", "hidden"} or re.search(r"password|token|cookie|session|secret|authorization", control_descriptor) else str(control["value"]))
        legacy_text = "\n".join(str(item.get("text", "")) for item in elements).strip()
        content = json.dumps(elements, ensure_ascii=False, sort_keys=True)
        return Snapshot(
            content,
            "\n".join(part for part in display_parts if part).strip()[:5000],
            hashlib.sha256(legacy_text.encode("utf-8")).hexdigest() if selector.strip().lower() == "body" and legacy_text else None,
            {"elements_inspected": len(elements), "candidate_regions": 0,
             "configured_ignored_regions": 0, "explicit_dynamic_regions": 0,
             "normalized_timer_regions": 0, "motion_normalized_regions": 0,
             "observation_ms": 0},
        )
    if isinstance(elements, list) and len(elements) > 1:
        result = {
            "serialized": json.dumps(elements, ensure_ascii=False, sort_keys=True),
            "display_value": "\n".join(item.get("display_value", item.get("text", "")) for item in elements),
            "legacy_text": "\n".join(item.get("legacyText", "") for item in elements),
            "diagnostics": elements[0].get("diagnostics", {}),
        }
    diagnostics = result.get("diagnostics") or {}
    if diagnostics.get("invalid_selector"):
        raise ValueError("A saved ignore selector is not valid for this page.")
    if diagnostics.get("ignored_root"):
        raise ValueError("An ignore selector matches the monitored element itself.")
    if diagnostics.get("empty_dom"):
        raise ValueError("The page did not contain comparable content.")
    content = result.get("serialized")
    if not isinstance(content, str):
        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
    legacy_text = result.get("legacy_text", "")
    legacy_hash = (
        hashlib.sha256(legacy_text.encode("utf-8")).hexdigest()
        if selector.strip().lower() == "body" and legacy_text
        else None
    )
    return Snapshot(
        content=content,
        display_value=(result.get("display_value") or "")[:5000],
        legacy_hash=legacy_hash,
        diagnostics=diagnostics,
    )
