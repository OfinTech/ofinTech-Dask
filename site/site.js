/* OfinTech website: language switch (Arabic is the source of truth in the markup, English lives in data-en),
   the engine's address for the sign-in and sign-up forms, and the error codes the engine sends back.

   Every translatable node carries data-en. Placeholders use data-en-placeholder, values data-en-value,
   titles data-en-title. Switching language swaps text, html[lang] and html[dir]; ?lang=en makes it shareable. */
(function () {
  "use strict";

  // Where the Team Server lives. Set window.OFINTECH_ENGINE in the page (or here) when the site and the app are on
  // different hosts; the forms post to it and the engine checks the Origin against DESK_SITE_URL.
  var ENGINE = (window.OFINTECH_ENGINE || "https://desk.ofintech.ai").replace(/\/+$/, "");

  /* ---- language ------------------------------------------------------------------------------------- */
  var AR_ATTR = "data-ar";
  function cache(el, attr, value) { if (!el.hasAttribute(attr)) el.setAttribute(attr, value); }

  function setLang(lang) {
    var en = lang === "en";
    document.querySelectorAll("[data-en]").forEach(function (el) {
      cache(el, AR_ATTR, el.innerHTML);
      el.innerHTML = en ? el.getAttribute("data-en") : el.getAttribute(AR_ATTR);
    });
    document.querySelectorAll("[data-en-placeholder]").forEach(function (el) {
      cache(el, "data-ar-placeholder", el.placeholder || "");
      el.placeholder = en ? el.getAttribute("data-en-placeholder") : el.getAttribute("data-ar-placeholder");
    });
    document.querySelectorAll("[data-en-label]").forEach(function (el) {
      cache(el, "data-ar-label", el.getAttribute("aria-label") || "");
      el.setAttribute("aria-label", en ? el.getAttribute("data-en-label") : el.getAttribute("data-ar-label"));
    });
    document.querySelectorAll("[data-en-title]").forEach(function (el) {
      cache(el, "data-ar-title", el.getAttribute("title") || "");
      el.setAttribute("title", en ? el.getAttribute("data-en-title") : el.getAttribute("data-ar-title"));
    });
    document.documentElement.lang = en ? "en" : "ar";
    document.documentElement.dir = en ? "ltr" : "rtl";
    document.querySelectorAll("[data-lang-toggle]").forEach(function (el) {
      el.textContent = en ? "العربية" : "English";
      el.setAttribute("aria-label", en ? "التبديل إلى العربية" : "Switch to English");
    });
    try { localStorage.setItem("ofintech-lang", lang); } catch (e) { /* private window */ }
    // keep ?lang= on internal links so the choice survives navigation
    document.querySelectorAll('a[href]').forEach(function (a) {
      var href = a.getAttribute("href");
      if (!href || /^(https?:|mailto:|tel:|#)/.test(href)) return;
      a.setAttribute("href", href.split("?")[0].split("#")[0] + (en ? "?lang=en" : "") + (href.indexOf("#") > -1 ? "#" + href.split("#")[1] : ""));
    });
  }

  function initialLang() {
    var p = new URLSearchParams(location.search).get("lang");
    if (p === "en" || p === "ar") return p;
    try { var s = localStorage.getItem("ofintech-lang"); if (s) return s; } catch (e) { /* ignore */ }
    return (navigator.language || "ar").slice(0, 2) === "ar" ? "ar" : "en";
  }

  /* ---- what the engine sends back ------------------------------------------------------------------- */
  // The engine redirects to {site}/#<code> when a form is refused (routes/public.py).
  var MESSAGES = {
    "signin-error": ["تعذّر تسجيل الدخول. تحقّق من البريد وكلمة المرور.", "Could not sign in. Check the e-mail and password."],
    "signin-rate": ["محاولات كثيرة. انتظر خمس دقائق ثم حاول مجدداً.", "Too many attempts. Wait five minutes and try again."],
    "signin-disabled": ["هذا الحساب موقوف. تواصل مع مدير مساحة العمل.", "This account is disabled. Ask your workspace administrator."],
    "signup-invalid": ["راجع البيانات المُدخلة: الاسم والبريد مطلوبان.", "Check what you entered: name and e-mail are required."],
    "signup-exists": ["يوجد حساب بهذا البريد. سجّل الدخول بدلاً من ذلك.", "An account with this e-mail exists. Sign in instead."],
    "signup-password": ["كلمة المرور تحتاج 12 حرفاً على الأقل، بأحرف كبيرة وصغيرة ورقم.", "The password needs at least 12 characters, upper- and lower-case letters and a digit."],
    "signup-rate": ["محاولات كثيرة. انتظر قليلاً ثم حاول مجدداً.", "Too many attempts. Wait a moment and try again."],
    "signup-invitation": ["التسجيل بدعوة. اطلب من مدير مساحة العمل دعوتك.", "Sign-up is by invitation. Ask a workspace administrator to invite you."],
    "google-off": ["تسجيل الدخول بحساب Google غير مفعّل على هذا الخادم.", "Google sign-in is not enabled on this server."],
    "desktop": ["هذا الخادم يعمل في وضع سطح المكتب ولا يملك حسابات.", "This server runs in desktop mode and has no accounts."]
  };

  function showHashMessage() {
    var code = (location.hash || "").replace(/^#/, "");
    if (!MESSAGES[code]) return;
    var onSignUp = /sign-up/.test(location.pathname);
    // errors always land on the site root; send the person to the page the error belongs to
    var belongs = code.indexOf("signup-") === 0 ? "sign-up" : code.indexOf("signin-") === 0 ? "sign-in" : null;
    if (belongs && location.pathname.indexOf(belongs) === -1) {
      location.replace(belongs + ".html" + location.search + "#" + code);
      return;
    }
    var box = document.getElementById("alert");
    if (!box) return;
    box.className = "alert bad show";
    box.setAttribute("role", "alert");
    box.setAttribute("data-en", MESSAGES[code][1]);
    box.textContent = MESSAGES[code][document.documentElement.lang === "en" ? 1 : 0];
    void onSignUp;
  }

  /* ---- start ---------------------------------------------------------------------------------------- */
  document.addEventListener("DOMContentLoaded", function () {
    // forms post to the engine; the action is set here so the address lives in one place
    document.querySelectorAll("form[data-engine-path]").forEach(function (f) {
      f.action = ENGINE + f.getAttribute("data-engine-path");
    });
    document.querySelectorAll("a[data-engine-path]").forEach(function (a) {
      a.href = ENGINE + a.getAttribute("data-engine-path");
    });

    setLang(initialLang());
    document.querySelectorAll("[data-lang-toggle]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        setLang(document.documentElement.lang === "en" ? "ar" : "en");
      });
    });

    showHashMessage();
    window.addEventListener("hashchange", showHashMessage);

    // the header sits on the navy hero at the top of the home page, and on paper once it scrolls past
    var top = document.querySelector(".top[data-over-hero]");
    var hero = document.querySelector(".hero");
    if (top && hero) {
      var sync = function () { top.classList.toggle("on-navy", window.scrollY < hero.offsetHeight - 80); };
      sync();
      window.addEventListener("scroll", sync, { passive: true });
    }
  });
})();
