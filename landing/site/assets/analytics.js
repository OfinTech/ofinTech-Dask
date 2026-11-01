/* OfinTech Desk — privacy-preserving website analytics.
 * Sends anonymous page-view and funnel events to the Team Server's collector (POST /api/analytics/collect) only after the
 * visitor has consented. Nothing identifying is sent: no cookies are set, no identifiers are stored; the server derives a
 * daily-rotating visitor hash from the request and keeps only the path, referrer host, coarse device/browser class,
 * language and (optionally) the proxy's country header. Configure the engine address with data-engine on the script tag:
 *   <script src="assets/analytics.js" data-engine="https://desk.example.com" defer></script>
 */
(function () {
  var script = document.currentScript || document.querySelector("script[data-engine]");
  var engine = (script && script.getAttribute("data-engine")) || "";
  if (!engine) return;
  var KEY = "ofin-analytics-consent";
  function consent() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function send(name, props) {
    if (consent() !== "yes") return;
    // sent as text/plain so the cross-origin request needs no CORS preflight; the collector parses the body as JSON
    var body = JSON.stringify({ name: name, path: location.pathname, referrer: document.referrer || null, lang: navigator.language, consent: true, props: props || null });
    try {
      if (navigator.sendBeacon) navigator.sendBeacon(engine + "/api/analytics/collect", new Blob([body], { type: "text/plain" }));
      else fetch(engine + "/api/analytics/collect", { method: "POST", mode: "no-cors", headers: { "content-type": "text/plain" }, body: body, keepalive: true });
    } catch (e) { /* analytics must never break the page */ }
  }
  window.ofinTrack = send;
  function banner() {
    if (consent()) return;
    var el = document.createElement("div");
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", "Analytics consent");
    el.style.cssText = "position:fixed;inset-inline:16px;bottom:16px;max-width:560px;margin:auto;background:#fff;color:#14231d;border:1px solid #d5dcd8;border-radius:10px;padding:14px 16px;font:14px/1.5 system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.12);z-index:9999";
    el.innerHTML = '<p style="margin:0 0 10px">We measure page views anonymously to improve this site: no cookies, no identifiers, no financial data. ' +
      '<a href="' + engine + '/api/analytics/policy" target="_blank" rel="noreferrer">What is collected</a>.</p>' +
      '<button data-c="yes" style="padding:6px 14px;border-radius:6px;border:1px solid #1e4fa3;background:#1e4fa3;color:#fff;cursor:pointer">Allow</button> ' +
      '<button data-c="no" style="padding:6px 14px;border-radius:6px;border:1px solid #d5dcd8;background:#fff;cursor:pointer;margin-inline-start:8px">Decline</button>';
    el.addEventListener("click", function (e) {
      var c = e.target && e.target.getAttribute && e.target.getAttribute("data-c");
      if (!c) return;
      try { localStorage.setItem(KEY, c); } catch (err) { /* private mode */ }
      el.remove();
      if (c === "yes") send("page_view");
    });
    document.body.appendChild(el);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", banner); else banner();
  send("page_view");
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest && e.target.closest("a[href*='#pricing'], a[href*='#signup'], form#signup button");
    if (!a) return;
    var h = a.getAttribute("href") || "";
    if (h.indexOf("#pricing") >= 0) send("pricing_view");
    if (h.indexOf("#signup") >= 0) send("signup_view");
  });
})();
