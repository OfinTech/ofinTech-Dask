// Number, money and date formatting with the user's locale and the workspace currency.

import { locale as uiLocale } from "./i18n";

// formatting follows the interface language (Western digits everywhere); English uses the browser's own locale
const locales = (): string => (uiLocale().startsWith("en") && typeof navigator !== "undefined" ? navigator.language || "en-US" : uiLocale());

export function fmtNumber(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  return new Intl.NumberFormat(locales(), { minimumFractionDigits: 0, maximumFractionDigits: digits }).format(n);
}

export function fmtMoney(v: unknown, currency?: string): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  const opts: Intl.NumberFormatOptions = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  if (currency && /^[A-Z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat(locales(), { ...opts, style: "currency", currency, currencySign: "accounting" }).format(n);
    } catch { /* unknown currency code: fall through */ }
  }
  const s = new Intl.NumberFormat(locales(), opts).format(Math.abs(n));
  return n < 0 ? `(${s})` : s;
}

export function fmtCompact(v: number, currency?: string): string {
  try {
    return new Intl.NumberFormat(locales(), {
      notation: "compact", maximumFractionDigits: 1,
      ...(currency && /^[A-Z]{3}$/.test(currency) ? { style: "currency", currency } : {}),
    }).format(v);
  } catch {
    return fmtNumber(v, 1);
  }
}

export function fmtPercent(v: unknown, digits = 1): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return new Intl.NumberFormat(locales(), { style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
}

export function fmtDate(v: unknown): string {
  if (!v) return "—";
  const d = typeof v === "number" ? new Date(v * 1000) : new Date(String(v).length === 10 ? `${v}T00:00:00` : String(v));
  if (Number.isNaN(d.getTime())) return String(v);
  return new Intl.DateTimeFormat(locales(), { dateStyle: "medium" }).format(d);
}

export function fmtDateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Intl.DateTimeFormat(locales(), { dateStyle: "medium", timeStyle: "short" }).format(new Date(ts * 1000));
}

export function fmtRelative(ts: number | null | undefined): string {
  if (!ts) return "—";
  const diff = ts * 1000 - Date.now();
  const rtf = new Intl.RelativeTimeFormat(locales(), { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 60_000) return rtf.format(Math.round(diff / 1000), "second");
  if (abs < 3_600_000) return rtf.format(Math.round(diff / 60_000), "minute");
  if (abs < 86_400_000) return rtf.format(Math.round(diff / 3_600_000), "hour");
  if (abs < 30 * 86_400_000) return rtf.format(Math.round(diff / 86_400_000), "day");
  return fmtDateTime(ts);
}

export function formatCell(v: unknown, format: string | undefined, currency?: string): string {
  if (v === null || v === undefined) return "—";
  switch (format) {
    case "money": return fmtMoney(v, undefined);
    case "percent": return fmtPercent(v);
    case "number": return fmtNumber(v);
    case "date": return fmtDate(v);
    case "days": return `${fmtNumber(v, 1)} days`;
    case "ratio": return fmtNumber(v, 2);
    default: return typeof v === "number" ? fmtNumber(v) : String(v);
  }
  void currency;
}

export function isNegative(v: unknown): boolean {
  return typeof v === "number" && v < 0;
}

export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];

export const CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CNY", "INR", "AUD", "CAD", "CHF", "SGD", "HKD", "AED", "SAR", "QAR",
  "KWD", "BHD", "OMR", "EGP", "ZAR", "NGN", "KES", "BRL", "MXN", "ARS", "CLP", "COP", "PEN", "SEK", "NOK", "DKK", "PLN",
  "CZK", "HUF", "TRY", "ILS", "KRW", "IDR", "MYR", "THB", "PHP", "VND", "PKR", "BDT", "NZD"];
