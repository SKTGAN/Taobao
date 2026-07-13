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
  const elements = roots.flatMap((root) => Array.from(root.querySelectorAll(selector)));
  const controls = elements
    .filter(visible)
    .slice(0, 800)
    .map((element) => ({
      text: normalize(element.innerText || element.value || element.getAttribute("aria-label")),
      disabled: Boolean(
        element.disabled
        || element.getAttribute("aria-disabled") === "true"
        || element.classList.contains("disabled")
      ),
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
    controls = tuple(payload.get("controls") or ())
    test_marker = str(payload.get("testMarker") or "")

    if url.startswith(("http://127.0.0.1:", "http://localhost:")) and test_marker in {
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
    elif any(token in url for token in ("cashier", "pay.taobao.com", "excashier")):
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
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
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
  const allElements = roots.flatMap((root) => Array.from(root.querySelectorAll('*'))).filter(visible);
  const resolveControl = (element) => element.closest(interactiveSelector) || element;
  const controls = allElements
    .map((element) => {{
      const control = resolveControl(element);
      return {{
        element: control,
        text: normalize(element.innerText || element.value || element.getAttribute("aria-label")),
        disabled: Boolean(
          control.disabled
          || control.getAttribute("aria-disabled") === "true"
          || control.classList.contains("disabled")
        ),
      }};
    }})
    .filter((item) => item.text && item.text.length <= 160 && !item.disabled)
    .sort((left, right) => left.text.length - right.text.length);
  let match = controls.find((item) => labels.some((label) => item.text === normalize(label)));
  if (!match) {{
    match = controls.find((item) => labels.some((label) => item.text.includes(normalize(label))));
  }}
  if (!match) return {{ found: false, text: "" }};
  match.element.scrollIntoView({{ block: "center", inline: "center" }});
  const rect = match.element.getBoundingClientRect();
  let x = rect.left + rect.width / 2;
  let y = rect.top + rect.height / 2;
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
