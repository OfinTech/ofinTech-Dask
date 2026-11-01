import type { Locale } from "./types";

const STRINGS = {
  en: {
    title: "Financial copilot",
    placeholder: "Ask about revenue, costs, invoices, cash…",
    send: "Send",
    stop: "Stop",
    thinking: "Working…",
    tool: "Looked up",
    readOnly: "Read-only: this assistant answers from your organisation's data and cannot change anything.",
    unverified: "Figures without a source",
    unverifiedDetail: "These numbers were not found in any tool result or document; verify before use.",
    approvalTitle: "Needs approval in OfinTech Desk",
    approvalBody: "This action can only be approved by a workspace editor inside OfinTech Desk.",
    openDesk: "Open in OfinTech Desk",
    error: "Something went wrong",
    retry: "Try again",
    empty: "Nothing to show",
    rows: "rows",
    showing: "Showing",
    of: "of",
    matched: "Matched",
    unmatched: "Unmatched",
    total: "Total",
    sources: "Sources",
    page: "page",
    overdueDays: "days overdue",
    expired: "Expires",
    offTopic: "This assistant covers the organisation's finances only.",
    networkError: "The assistant could not be reached. Check your connection and try again.",
    sessionEnded: "Your session ended. Reload the page to continue.",
  },
  ar: {
    title: "المساعد المالي",
    placeholder: "اسأل عن الإيرادات أو التكاليف أو الفواتير أو النقد…",
    send: "إرسال",
    stop: "إيقاف",
    thinking: "جارٍ العمل…",
    tool: "تم الاطلاع على",
    readOnly: "للقراءة فقط: يجيب هذا المساعد من بيانات منشأتك ولا يمكنه تغيير أي شيء.",
    unverified: "أرقام بلا مصدر",
    unverifiedDetail: "لم تُعثر هذه الأرقام في نتائج الأدوات أو المستندات؛ تحقّق منها قبل الاعتماد عليها.",
    approvalTitle: "يحتاج موافقة داخل OfinTech Desk",
    approvalBody: "لا يمكن اعتماد هذا الإجراء إلا من محرّر في مساحة العمل داخل OfinTech Desk.",
    openDesk: "افتح في OfinTech Desk",
    error: "حدث خطأ",
    retry: "أعد المحاولة",
    empty: "لا شيء لعرضه",
    rows: "صفوف",
    showing: "عرض",
    of: "من",
    matched: "مطابق",
    unmatched: "غير مطابق",
    total: "الإجمالي",
    sources: "المصادر",
    page: "صفحة",
    overdueDays: "يوم تأخير",
    expired: "ينتهي",
    offTopic: "يغطي هذا المساعد الشؤون المالية للمنشأة فقط.",
    networkError: "تعذّر الوصول إلى المساعد. تحقّق من الاتصال وحاول مجدداً.",
    sessionEnded: "انتهت جلستك. أعد تحميل الصفحة للمتابعة.",
  },
} as const;

export type StringKey = keyof (typeof STRINGS)["en"];

export function t(locale: Locale, key: StringKey): string {
  return STRINGS[locale]?.[key] ?? STRINGS.en[key];
}

export function dirOf(locale: Locale): "rtl" | "ltr" {
  return locale === "ar" ? "rtl" : "ltr";
}

const NUMBER_LOCALE: Record<Locale, string> = { en: "en", ar: "ar-SA" };

export function formatNumber(locale: Locale, value: number | null | undefined, format?: string, currency?: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const nf = NUMBER_LOCALE[locale] ?? "en";
  try {
    if (format === "money") {
      if (currency && /^[A-Z]{3}$/.test(currency)) {
        return new Intl.NumberFormat(nf, { style: "currency", currency, maximumFractionDigits: 2 }).format(value);
      }
      return new Intl.NumberFormat(nf, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
    }
    if (format === "percent") {
      return new Intl.NumberFormat(nf, { style: "percent", maximumFractionDigits: 1 }).format(Math.abs(value) > 1.5 ? value / 100 : value);
    }
    return new Intl.NumberFormat(nf, { maximumFractionDigits: 2 }).format(value);
  } catch {
    return String(value);
  }
}
