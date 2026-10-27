/**
 * Interface language. Strings are written in English in the code and looked up by their English text, so a missing
 * translation simply shows English. Arabic switches the whole layout to right-to-left.
 *
 * Add a language: copy src/i18n/fr.ts, translate the values, register it in LANGUAGES and DICTS, then run
 * `npm run i18n:check` to list strings that still need translating.
 */
import ar from "../i18n/ar";
import es from "../i18n/es";
import fr from "../i18n/fr";

export type Lang = "en" | "ar" | "es" | "fr";
export const LANGUAGES: { code: Lang; name: string; dir: "ltr" | "rtl" }[] = [
  { code: "en", name: "English", dir: "ltr" },
  { code: "ar", name: "العربية", dir: "rtl" },
  { code: "es", name: "Español", dir: "ltr" },
  { code: "fr", name: "Français", dir: "ltr" },
];
const DICTS: Record<Lang, Record<string, string>> = { en: {}, ar, es, fr };
const KEY = "desk.lang";

function initial(): Lang {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved && saved in DICTS) return saved as Lang;
  } catch { /* storage unavailable */ }
  const nav = (navigator.language || "en").slice(0, 2).toLowerCase();
  return (nav in DICTS ? nav : "en") as Lang;
}

let current: Lang = initial();
const listeners = new Set<(l: Lang) => void>();

export function lang(): Lang {
  return current;
}

export function dir(): "ltr" | "rtl" {
  return LANGUAGES.find((l) => l.code === current)?.dir || "ltr";
}

function apply(): void {
  document.documentElement.lang = current;
  document.documentElement.dir = dir();
}
apply();

/** Change the language for this browser (the server keeps the person's choice too, see Settings). */
export function setLang(l: Lang): void {
  if (!(l in DICTS) || l === current) return;
  current = l;
  try { localStorage.setItem(KEY, l); } catch { /* storage unavailable */ }
  apply();
  listeners.forEach((fn) => fn(l));
}

export function onLang(fn: (l: Lang) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Marks a string in a table as translatable (it is translated where it is shown, with tr). */
export const N = (text: string): string => text;

/** Translate an English interface string. `{name}` placeholders are filled from `vars`. */
export function tr(text: string, vars?: Record<string, string | number>): string {
  let out = (current !== "en" && DICTS[current][text]) || text;
  if (vars) for (const [k, v] of Object.entries(vars)) out = out.split(`{${k}}`).join(String(v));
  return out;
}

/** The locale used to format dates and numbers. Figures keep Western digits in every language, as in most finance
 * teams' reports. */
export function locale(): string {
  return { en: "en-GB", ar: "ar-SA-u-nu-latn", es: "es-ES", fr: "fr-FR" }[current];
}
