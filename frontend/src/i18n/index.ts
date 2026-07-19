import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import de from "./locales/de.json";
import en from "./locales/en.json";

function readStoredLang(): string {
  try {
    return localStorage.getItem("tiqora-lang") ?? "en";
  } catch {
    return "en";
  }
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    de: { translation: de },
  },
  lng: readStoredLang(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export default i18n;
