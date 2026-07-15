from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


PAGE_SNAPSHOT_SCRIPT = r"""
(() => {
  const normalize = (value) => (value || "").replace(/\s+/g, "").trim();
  const visible = (element) => {
    const view = element.ownerDocument?.defaultView || window;
    const style = view.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const roots = [document];
  const seenRoots = new Set(roots);
  for (let index = 0; index < roots.length && index < 100; index += 1) {
    const root = roots[index];
    for (const element of root.querySelectorAll('*')) {
      if (element.shadowRoot && !seenRoots.has(element.shadowRoot)) {
        seenRoots.add(element.shadowRoot);
        roots.push(element.shadowRoot);
      }
      if (element.tagName === 'IFRAME') {
        try {
          const frameDocument = element.contentDocument;
          if (frameDocument && !seenRoots.has(frameDocument)) {
            seenRoots.add(frameDocument);
            roots.push(frameDocument);
          }
        } catch (_) {}
      }
    }
  }
  const selector = 'button, a, [role="button"], input[type="button"], input[type="submit"], [onclick], [class*="btn" i], [class*="button" i]';
  // Keep only explicit controls inside the checkout area ahead of the generic
  // 800-item limit. Never treat the whole #submitOrder region as a button: it
  // can also contain address, agreement and privacy-help links.
  const preferredSelectors = [
    '#submitOrder button',
    '#submitOrder input[type="submit"]',
    '#submitOrder [role="button"]',
    '#submitOrder [onclick]',
    '#submitOrder .trade-buy-btn-submit [class*="btn" i]',
    '#submitOrder .trade-buy-btn-submit [class*="button" i]',
    '#submitOrder .trade-buy-btn-submit',
    'button.trade-buy-btn-submit',
    'input.trade-buy-btn-submit',
    '.trade-buy-btn-submit[role="button"]',
    '.trade-buy-btn-submit[onclick]',
  ];
  const preferredElements = roots.flatMap((root) => {
    for (const preferredSelector of preferredSelectors) {
      const matches = Array.from(root.querySelectorAll(preferredSelector));
      if (matches.length) return matches;
    }
    return [];
  });
  const genericElements = roots.flatMap((root) => Array.from(root.querySelectorAll(selector)));
  const elements = Array.from(new Set([...preferredElements, ...genericElements]));
  const disabled = (element) => Boolean(
    element.disabled
    || element.getAttribute("aria-disabled") === "true"
    || element.classList.contains("disabled")
    || element.querySelector?.(':disabled, [aria-disabled="true"], .disabled')
  );
  const controls = elements
    .filter(visible)
    .slice(0, 800)
    .map((element) => ({
      text: normalize(element.innerText || element.value || element.getAttribute("aria-label")),
      disabled: disabled(element),
    }))
    .filter((item) => item.text);
  const textParts = roots.map((root) => {
    if (root.body?.innerText) return root.body.innerText;
    return root.textContent || "";
  });
  return {
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    testMarker: document.querySelector('meta[name="taobao-assistant-page"]')?.content || "",
    bodyText: normalize(textParts.join(" ")).slice(0, 12000),
    controls,
  };
})()
"""


@dataclass(frozen=True)
class PageSnapshot:
    kind: str
    url: str
    title: str
    ready_state: str
    body_text: str
    controls: tuple[dict[str, Any], ...]

    @property
    def action_texts(self) -> tuple[str, ...]:
        return tuple(str(item.get("text") or "") for item in self.controls)


def classify_page(payload: dict[str, Any]) -> PageSnapshot:
    url = str(payload.get("url") or "").lower()
    title = str(payload.get("title") or "")
    body_text = str(payload.get("bodyText") or "")
    combined = f"{title} {body_text}"
    combined_lower = combined.lower()
    controls = tuple(payload.get("controls") or ())
    test_marker = str(payload.get("testMarker") or "")

    if (
        url.startswith("chrome-error://")
        and "alipay.com" in combined_lower
        and any(
            token in combined_lower
            for token in (
                "err_connection_closed",
                "err_connection_reset",
                "err_connection_timed_out",
                "err_timed_out",
                "无法访问此网站",
                "意外终止了连接",
            )
        )
    ):
        # Chrome replaces location.href with chrome-error://chromewebdata/ when
        # the Alipay payment endpoint cannot be loaded. This is distinct from a
        # failed order-submit click: the checkout already navigated away from
        # Taobao and the user must verify the newly created pending order.
        kind = "payment_error"
    elif url.startswith(("http://127.0.0.1:", "http://localhost:")) and test_marker in {
        "product",
        "confirm_order",
        "pending_payment",
        "login",
        "challenge",
    }:
        kind = test_marker
    elif any(token in url for token in ("login.taobao.com", "login.tmall.com")):
        kind = "login"
    elif any(token in url for token in ("sec.taobao.com", "captcha", "verify")) or any(
        token in combined for token in ("验证码", "滑块", "安全验证", "账户存在风险", "账号存在风险")
    ):
        kind = "challenge"
    elif any(
        token in url
        for token in (
            "phone-privacy",
            "privacy-rule",
            "privacy_rule",
            "agreement",
            "rules.htm",
        )
    ) and any(host in url for host in ("taobao.com", "tmall.com")):
        kind = "auxiliary"
    elif any(token in url for token in ("cashier", "pay.taobao.com", "excashier")):
        kind = "pending_payment"
    elif "alipay.com" in url and any(token in url for token in ("trade_payment", "tradepayment")):
        kind = "pending_payment"
    elif "alipay.com" in url and any(token in combined for token in ("收银台", "立即付款", "确认付款", "支付订单")):
        kind = "pending_payment"
    elif any(token in url for token in ("buy.taobao.com", "buy.tmall.com")) or "确认订单" in combined:
        kind = "confirm_order"
    elif any(token in url for token in ("item.taobao.com", "detail.tmall.com")):
        kind = "product"
    elif any(token in combined for token in ("订单提交成功", "订单创建成功")) and any(
        token in combined for token in ("待付款", "收银台", "立即付款", "去付款")
    ):
        kind = "pending_payment"
    else:
        kind = "unknown"

    return PageSnapshot(
        kind=kind,
        url=str(payload.get("url") or ""),
        title=title,
        ready_state=str(payload.get("readyState") or ""),
        body_text=body_text,
        controls=controls,
    )


def has_enabled_action(snapshot: PageSnapshot, labels: tuple[str, ...]) -> bool:
    for control in snapshot.controls:
        text = str(control.get("text") or "")
        if not control.get("disabled") and any(label in text for label in labels):
            return True
    return False


def product_precheck_finished(snapshot: PageSnapshot, buy_labels: tuple[str, ...]) -> bool:
    """Return only when precheck has meaningful page state, not an interim blank tab."""
    if snapshot.kind in {"login", "challenge"}:
        return True
    return (
        snapshot.kind == "product"
        and snapshot.ready_state == "complete"
        and has_enabled_action(snapshot, buy_labels)
    )


def build_click_action_script(labels: tuple[str, ...]) -> str:
    encoded_labels = json.dumps(labels, ensure_ascii=False)
    return rf"""
(() => {{
  const labels = {encoded_labels};
  const normalize = (value) => (value || "").replace(/\s+/g, "").trim();
  const visible = (element) => {{
    const view = element.ownerDocument?.defaultView || window;
    const style = view.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none"
      && style.visibility !== "hidden"
      && style.pointerEvents !== "none"
      && Number(style.opacity || 1) > 0
      && rect.width > 0
      && rect.height > 0;
  }};
  const roots = [document];
  const seenRoots = new Set(roots);
  for (let index = 0; index < roots.length && index < 100; index += 1) {{
    const root = roots[index];
    for (const element of root.querySelectorAll('*')) {{
      if (element.shadowRoot && !seenRoots.has(element.shadowRoot)) {{
        seenRoots.add(element.shadowRoot);
        roots.push(element.shadowRoot);
      }}
      if (element.tagName === 'IFRAME') {{
        try {{
          const frameDocument = element.contentDocument;
          if (frameDocument && !seenRoots.has(frameDocument)) {{
            seenRoots.add(frameDocument);
            roots.push(frameDocument);
          }}
        }} catch (_) {{}}
      }}
    }}
  }}
  const interactiveSelector = 'button, a, [role="button"], input[type="button"], input[type="submit"], [onclick], [class*="btn" i], [class*="button" i]';
  const disabled = (element) => Boolean(
    element.disabled
    || element.getAttribute("aria-disabled") === "true"
    || element.classList.contains("disabled")
    || element.querySelector?.(':disabled, [aria-disabled="true"], .disabled')
  );
  const wantsSubmit = labels.some((label) =>
    ["提交订单", "提交并支付", "确认提交", "立即支付", "立即付款"].some(
      (token) => normalize(label).includes(token)
    )
  );
  const preferredSelectors = [
    '#submitOrder button',
    '#submitOrder input[type="submit"]',
    '#submitOrder [role="button"]',
    '#submitOrder [onclick]',
    '#submitOrder .trade-buy-btn-submit [class*="btn" i]',
    '#submitOrder .trade-buy-btn-submit [class*="button" i]',
    '#submitOrder .trade-buy-btn-submit',
    'button.trade-buy-btn-submit',
    'input.trade-buy-btn-submit',
    '.trade-buy-btn-submit[role="button"]',
    '.trade-buy-btn-submit[onclick]',
  ];
  let match = null;
  if (wantsSubmit) {{
    const submitCandidates = [];
    for (const root of roots) {{
      for (const selector of preferredSelectors) {{
        for (const element of root.querySelectorAll(selector)) {{
          if (!visible(element) || disabled(element)) continue;
          const text = normalize(element.innerText || element.value || element.textContent || element.getAttribute("aria-label"));
          const href = String(element.href || element.getAttribute("href") || "").toLowerCase();
          const unsafeHelpLink = ["phone-privacy", "privacy-rule", "agreement", "rules.htm"]
            .some((token) => href.includes(token));
          if (
            !unsafeHelpLink
            && text.length <= 100
            && labels.some((label) => text.includes(normalize(label)))
          ) {{
            submitCandidates.push({{ element, text }});
          }}
        }}
      }}
    }}
    submitCandidates.sort((left, right) => {{
      const leftExact = labels.some((label) => left.text === normalize(label)) ? 0 : 1;
      const rightExact = labels.some((label) => right.text === normalize(label)) ? 0 : 1;
      return leftExact - rightExact || left.text.length - right.text.length;
    }});
    match = submitCandidates[0] || null;
    // Submitting an order is irreversible enough that a whole-page text
    // fallback is unsafe. If Taobao changes the submit container, stop for
    // human review instead of clicking another "立即支付"/agreement link.
    if (!match) return {{ found: false, text: "", reason: "submit_container_not_found" }};
  }}
  const allElements = roots.flatMap((root) => Array.from(root.querySelectorAll('*'))).filter(visible);
  const resolveControl = (element) => element.closest(interactiveSelector) || element;
  const controls = allElements
    .map((element) => {{
      const control = resolveControl(element);
      return {{
        element: control,
        text: normalize(element.innerText || element.value || element.getAttribute("aria-label")),
        disabled: disabled(control),
      }};
    }})
    .filter((item) => item.text && item.text.length <= 160 && !item.disabled)
    .sort((left, right) => left.text.length - right.text.length);
  match ||= controls.find((item) => labels.some((label) => item.text === normalize(label)));
  if (!match) {{
    match = controls.find((item) => labels.some((label) => item.text.includes(normalize(label))));
  }}
  if (!match) return {{ found: false, text: "" }};
  match.element.scrollIntoView({{ block: "center", inline: "center" }});
  const rect = match.element.getBoundingClientRect();
  const localX = rect.left + rect.width / 2;
  const localY = rect.top + rect.height / 2;
  let x = localX;
  let y = localY;
  let view = match.element.ownerDocument?.defaultView;
  while (view && view !== window) {{
    try {{
      const frame = view.frameElement;
      if (!frame) break;
      const frameRect = frame.getBoundingClientRect();
      x += frameRect.left;
      y += frameRect.top;
      view = frame.ownerDocument?.defaultView;
    }} catch (_) {{
      break;
    }}
  }}
  const composedContains = (ancestor, node) => {{
    let current = node;
    for (let index = 0; current && index < 100; index += 1) {{
      if (current === ancestor) return true;
      current = current.parentNode
        || current.host
        || current.ownerDocument?.defaultView?.frameElement
        || null;
    }}
    return false;
  }};
  // document.elementFromPoint() stops at a shadow host or iframe. Resolve the
  // point in the element's own root using local viewport coordinates so the
  // safety check sees the actual button rather than only its outer host.
  const localRoot = match.element.getRootNode?.() || match.element.ownerDocument;
  const localHit = localRoot?.elementFromPoint?.(localX, localY) || null;
  const hit = localHit || window.document.elementFromPoint(x, y);
  if (!hit || (!composedContains(match.element, hit) && !composedContains(hit, match.element))) {{
    return {{
      found: false,
      text: match.text,
      reason: "target_covered",
      x,
      y,
    }};
  }}
  if (wantsSubmit) {{
    const hitControl = hit.closest?.(interactiveSelector) || hit;
    const hitText = normalize(
      hitControl.innerText || hitControl.value || hitControl.textContent || hitControl.getAttribute?.("aria-label")
    );
    const hitHref = String(hitControl.href || hitControl.getAttribute?.("href") || "").toLowerCase();
    const unsafeHelpLink = ["phone-privacy", "privacy-rule", "agreement", "rules.htm"]
      .some((token) => hitHref.includes(token));
    if (unsafeHelpLink || !labels.some((label) => hitText.includes(normalize(label)))) {{
      return {{
        found: false,
        text: hitText,
        reason: "unsafe_submit_hit_target",
        x,
        y,
      }};
    }}
  }}
  return {{
    found: true,
    text: match.text,
    x,
    y,
    width: rect.width,
    height: rect.height,
  }};
}})()
"""
