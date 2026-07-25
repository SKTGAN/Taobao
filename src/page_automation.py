from __future__ import annotations

from dataclasses import dataclass
import json
import re
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
  const semanticSelector = 'button, a, [role="button"], input[type="button"], input[type="submit"], [onclick]';
  const elements = Array.from(new Set(
    [...preferredElements, ...genericElements].map((element) => element.closest(semanticSelector) || element)
  ));
  const disabled = (element) => Boolean(
    element.disabled
    || element.getAttribute("aria-disabled") === "true"
    || element.classList.contains("disabled")
    || element.querySelector?.(':disabled, [aria-disabled="true"], .disabled')
  );
  const controls = elements
    .filter((element) => !elements.some((child) =>
      child !== element
      && element.contains?.(child)
      && child.matches?.(semanticSelector)
      && normalize(element.innerText || element.value || element.getAttribute("aria-label"))
        === normalize(child.innerText || child.value || child.getAttribute("aria-label"))
    ))
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
        "friend_pay_request",
        "friend_pay_sent",
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
    elif "alipay.com" in url and any(
        token in combined for token in ("代付申请已提交", "已通知好友付款", "代付请求已发送")
    ):
        kind = "friend_pay_sent"
    elif (
        "alipay.com" in url
        and any(token in combined for token in ("申请代付", "找人代付", "请他付款"))
        and any(token in combined for token in ("好友的账户", "支付宝账户", "淘宝账户"))
    ):
        kind = "friend_pay_request"
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
    return enabled_action_count(snapshot, labels) > 0


def enabled_action_count(snapshot: PageSnapshot, labels: tuple[str, ...]) -> int:
    normalized_labels = tuple("".join(label.split()) for label in labels)
    exact_matches: list[str] = []
    contains_matches: list[str] = []
    for control in snapshot.controls:
        if control.get("disabled"):
            continue
        text = "".join(str(control.get("text") or "").split())
        if not text:
            continue
        if any(text == label for label in normalized_labels):
            exact_matches.append(text)
        elif any(label in text for label in normalized_labels):
            contains_matches.append(text)
    # Taobao often exposes a real button plus a visible action-bar wrapper whose
    # text is "加入购物车立即购买". When an exact control exists, ignore wrappers.
    return len(exact_matches) if exact_matches else len(contains_matches)


def has_unique_enabled_action(snapshot: PageSnapshot, labels: tuple[str, ...]) -> bool:
    return enabled_action_count(snapshot, labels) == 1


def product_precheck_finished(snapshot: PageSnapshot, buy_labels: tuple[str, ...]) -> bool:
    """Return only when precheck has meaningful page state, not an interim blank tab."""
    if snapshot.kind in {"login", "challenge"}:
        return True
    return (
        snapshot.kind == "product"
        and snapshot.ready_state == "complete"
        and has_enabled_action(snapshot, buy_labels)
    )


def parse_spec_terms(value: str) -> tuple[str, ...]:
    terms = [item.strip() for item in re.split(r"[|；;\n]+", str(value or ""))]
    return tuple(dict.fromkeys(item for item in terms if item))


def build_click_product_option_script(option_text: str) -> str:
    encoded_text = json.dumps(str(option_text or "").strip(), ensure_ascii=False)
    return rf"""
(() => {{
  const wanted = ({encoded_text}).replace(/\s+/g, "").trim();
  const normalize = (value) => (value || "").replace(/\s+/g, "").trim();
  const visible = (element) => {{
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && style.pointerEvents !== "none" && rect.width > 0 && rect.height > 0;
  }};
  const disabled = (element) => Boolean(
    element.disabled
    || element.getAttribute("aria-disabled") === "true"
    || element.classList.contains("disabled")
    || element.classList.contains("is-disabled")
  );
  const selector = [
    'button',
    '[role="button"]',
    'label',
    'li[class*="sku" i]',
    'li[class*="prop" i]',
    '[class*="sku-item" i]',
    '[class*="skuItem" i]',
    '[class*="value-item" i]',
    '[data-value]',
  ].join(',');
  const roots = [document];
  for (let index = 0; index < roots.length && index < 100; index += 1) {{
    for (const element of roots[index].querySelectorAll('*')) {{
      if (element.shadowRoot && !roots.includes(element.shadowRoot)) roots.push(element.shadowRoot);
    }}
  }}
  const candidates = roots.flatMap((root) => Array.from(root.querySelectorAll(selector)))
    .filter((element) => visible(element) && !disabled(element))
    .filter((element) => normalize(
      element.innerText || element.textContent || element.getAttribute("aria-label")
    ) === wanted);
  const unique = Array.from(new Set(candidates.map((element) =>
    element.closest('button,[role="button"],label,li,[data-value]') || element
  )));
  if (unique.length !== 1) {{
    return {{
      found: false,
      text: wanted,
      reason: unique.length ? "ambiguous_option" : "option_not_found",
      candidateCount: unique.length,
    }};
  }}
  const element = unique[0];
  element.scrollIntoView({{block: "center", inline: "center"}});
  const rect = element.getBoundingClientRect();
  return {{
    found: true,
    text: wanted,
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
  }};
}})()
"""


def build_set_quantity_script(quantity: int) -> str:
    normalized_quantity = max(1, min(5, int(quantity)))
    return rf"""
(() => {{
  const wanted = {normalized_quantity};
  const visible = (element) => {{
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && rect.width > 0 && rect.height > 0;
  }};
  const roots = [document];
  for (let index = 0; index < roots.length && index < 100; index += 1) {{
    for (const element of roots[index].querySelectorAll('*')) {{
      if (element.shadowRoot && !roots.includes(element.shadowRoot)) roots.push(element.shadowRoot);
    }}
  }}
  const inputs = roots.flatMap((root) => Array.from(root.querySelectorAll(
    'input[type="number"], input[class*="quantity" i], input[class*="amount" i], input[class*="count" i]'
  ))).filter((element) => visible(element) && !element.disabled && !element.readOnly);
  if (inputs.length !== 1) {{
    return {{
      changed: false,
      reason: inputs.length ? "ambiguous_quantity" : "quantity_input_not_found",
      candidateCount: inputs.length,
    }};
  }}
  const input = inputs[0];
  const min = Number(input.min || 1);
  const max = Number(input.max || 5);
  if (wanted < min || wanted > max) {{
    return {{changed: false, reason: "quantity_out_of_range", min, max}};
  }}
  const setter = Object.getOwnPropertyDescriptor(
    input.ownerDocument.defaultView.HTMLInputElement.prototype,
    "value"
  )?.set;
  if (setter) setter.call(input, String(wanted));
  else input.value = String(wanted);
  input.dispatchEvent(new Event("input", {{bubbles: true}}));
  input.dispatchEvent(new Event("change", {{bubbles: true}}));
  input.blur();
  return {{changed: true, value: String(input.value)}};
}})()
"""


def build_select_address_script(keyword: str) -> str:
    encoded_keyword = json.dumps(str(keyword or "").strip(), ensure_ascii=False)
    return rf"""
(() => {{
  const wanted = ({encoded_keyword}).replace(/\s+/g, "").trim();
  const normalize = (value) => (value || "").replace(/\s+/g, "").trim();
  const visible = (element) => {{
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && style.pointerEvents !== "none" && rect.width > 0 && rect.height > 0;
  }};
  if (wanted.length < 2) return {{found: false, reason: "address_keyword_too_short"}};
  const selector = [
    'label',
    '[role="radio"]',
    '[class*="address-item" i]',
    '[class*="addressItem" i]',
    'li[class*="address" i]',
    'div[class*="address" i][onclick]',
  ].join(',');
  const roots = [document];
  for (let index = 0; index < roots.length && index < 100; index += 1) {{
    for (const element of roots[index].querySelectorAll('*')) {{
      if (element.shadowRoot && !roots.includes(element.shadowRoot)) roots.push(element.shadowRoot);
    }}
  }}
  const candidates = roots.flatMap((root) => Array.from(root.querySelectorAll(selector)))
    .filter(visible)
    .filter((element) => {{
      const text = normalize(element.innerText || element.textContent);
      return text.includes(wanted) && text.length <= 400;
    }});
  const unique = Array.from(new Set(candidates.map((element) =>
    element.closest('label,[role="radio"],li[class*="address" i],[class*="address-item" i],[class*="addressItem" i]')
      || element
  )));
  if (unique.length !== 1) {{
    return {{
      found: false,
      reason: unique.length ? "ambiguous_address" : "address_not_found",
      candidateCount: unique.length,
    }};
  }}
  const element = unique[0];
  element.scrollIntoView({{block: "center", inline: "center"}});
  const rect = element.getBoundingClientRect();
  return {{
    found: true,
    text: normalize(element.innerText || element.textContent).slice(0, 160),
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
  }};
}})()
"""


def build_fill_friend_pay_script(account: str) -> str:
    encoded_account = json.dumps(str(account or "").strip(), ensure_ascii=False)
    return rf"""
(() => {{
  const account = {encoded_account};
  const visible = (element) => {{
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && rect.width > 0 && rect.height > 0;
  }};
  const roots = [document];
  for (let index = 0; index < roots.length && index < 100; index += 1) {{
    for (const element of roots[index].querySelectorAll('*')) {{
      if (element.shadowRoot && !roots.includes(element.shadowRoot)) roots.push(element.shadowRoot);
    }}
  }}
  const inputs = roots.flatMap((root) =>
    Array.from(root.querySelectorAll('input[type="text"], input:not([type])'))
  )
    .filter((input) => visible(input) && !input.disabled && !input.readOnly)
    .filter((input) => {{
      const hint = [
        input.placeholder,
        input.name,
        input.id,
        input.getAttribute("aria-label"),
      ].join(" ");
      return /支付宝|淘宝账户|好友|friend|account|payer/i.test(hint);
    }});
  if (inputs.length !== 1) {{
    return {{
      filled: false,
      reason: inputs.length ? "ambiguous_friend_account" : "friend_account_input_not_found",
      candidateCount: inputs.length,
    }};
  }}
  const input = inputs[0];
  const setter = Object.getOwnPropertyDescriptor(
    input.ownerDocument.defaultView.HTMLInputElement.prototype,
    "value"
  )?.set;
  if (setter) setter.call(input, account);
  else input.value = account;
  input.dispatchEvent(new Event("input", {{bubbles: true}}));
  input.dispatchEvent(new Event("change", {{bubbles: true}}));
  return {{filled: input.value === account}};
}})()
"""


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
  const interactiveSelector = 'button, a, label, [role="button"], input[type="button"], input[type="submit"], input[type="checkbox"], input[type="radio"], [onclick], [class*="btn" i], [class*="button" i]';
  const disabled = (element) => Boolean(
    element.disabled
    || element.getAttribute("aria-disabled") === "true"
    || element.classList.contains("disabled")
    || element.querySelector?.(':disabled, [aria-disabled="true"], .disabled')
  );
  const wantsSubmit = labels.some((label) => normalize(label).includes("提交订单"));
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
    const seenSubmitElements = new Set();
    for (const root of roots) {{
      for (const selector of preferredSelectors) {{
        for (const element of root.querySelectorAll(selector)) {{
          if (seenSubmitElements.has(element)) continue;
          seenSubmitElements.add(element);
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
    const compactSubmitCandidates = submitCandidates.filter((item) =>
      !submitCandidates.some((child) =>
        child !== item
        && item.element.contains?.(child.element)
        && item.text === child.text
      )
    );
    const exactSubmitCandidates = compactSubmitCandidates.filter((item) =>
      labels.some((label) => item.text === normalize(label))
    );
    const eligibleSubmitCandidates = exactSubmitCandidates.length
      ? exactSubmitCandidates
      : compactSubmitCandidates;
    if (eligibleSubmitCandidates.length > 1) {{
      return {{
        found: false,
        text: "",
        reason: "ambiguous_action",
        candidateCount: eligibleSubmitCandidates.length,
      }};
    }}
    match = eligibleSubmitCandidates[0] || null;
    // Submitting an order is irreversible enough that a whole-page text
    // fallback is unsafe. If Taobao changes the submit container, stop for
    // human review instead of clicking another "立即支付"/agreement link.
    if (!match) return {{ found: false, text: "", reason: "submit_container_not_found" }};
  }}
  const seenControls = new Set();
  const controls = roots
    .flatMap((root) => Array.from(root.querySelectorAll(interactiveSelector)))
    .filter((control) => {{
      if (seenControls.has(control)) return false;
      seenControls.add(control);
      return visible(control);
    }})
    .map((control) => ({{
      element: control,
      text: normalize(control.innerText || control.value || control.textContent || control.getAttribute("aria-label")),
      disabled: disabled(control),
    }}))
    .filter((item) => item.text && item.text.length <= 160 && !item.disabled)
    .sort((left, right) => left.text.length - right.text.length);
  if (!match) {{
    const exactCandidates = controls.filter((item) =>
      labels.some((label) => item.text === normalize(label))
    );
    const containsCandidates = exactCandidates.length
      ? exactCandidates
      : controls.filter((item) => labels.some((label) => item.text.includes(normalize(label))));
    if (containsCandidates.length > 1) {{
      return {{
        found: false,
        text: "",
        reason: "ambiguous_action",
        candidateCount: containsCandidates.length,
      }};
    }}
    match = containsCandidates[0] || null;
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
