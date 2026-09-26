import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { ApiError, NetworkError } from "../api/client";
import en from "./en.json";
import ru from "./ru.json";

export const LANGUAGES = ["ru", "en"] as const;
const STORAGE_KEY = "panel.lang";

function storedLanguage(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "ru";
  } catch {
    return "ru";
  }
}

void i18n.use(initReactI18next).init({
  resources: { ru: { translation: ru }, en: { translation: en } },
  lng: storedLanguage(),
  fallbackLng: "ru",
  interpolation: { escapeValue: false },
});

i18n.on("languageChanged", (lng) => {
  try {
    localStorage.setItem(STORAGE_KEY, lng);
  } catch {
    // Private mode or blocked storage: the choice just is not remembered.
  }
  document.documentElement.lang = lng;
});

/** Human-readable text for an API or network error. */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const key = `errors.${err.code}`;
    return i18n.exists(key) ? i18n.t(key) : i18n.t("errors.generic", { status: err.status });
  }
  if (err instanceof NetworkError) return i18n.t("errors.network");
  return err instanceof Error ? err.message : String(err);
}

export default i18n;
