import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import { it, type Translation } from "./it";
import { en } from "./en";

export type Language = "it" | "en";

export const SPEED_OPTIONS = [0.25, 0.5, 1, 2, 4, 8, 16, 32] as const;
export type SpeedOption = (typeof SPEED_OPTIONS)[number];

interface I18nContextType {
  lang: Language;
  setLang: (lang: Language) => void;
  t: Translation;
}

const I18nContext = createContext<I18nContextType>({
  lang: "it",
  setLang: () => {},
  t: it,
});

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Language>(() => {
    try {
      const saved = localStorage.getItem("vrp-lang");
      if (saved === "en" || saved === "it") return saved;
    } catch {
      // Ignore storage errors
    }
    return "it";
  });

  const setLang = (newLang: Language) => {
    setLangState(newLang);
    try {
      localStorage.setItem("vrp-lang", newLang);
    } catch {
      // Ignore storage errors
    }
  };

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const value = useMemo(
    () => ({
      lang,
      setLang,
      t: lang === "en" ? en : it,
    }),
    [lang]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextType {
  return useContext(I18nContext);
}
