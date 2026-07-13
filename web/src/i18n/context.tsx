import { createContext, useContext, type ReactNode } from "react";
import type { Translations } from "./types";
import { en } from "./en";

interface I18nContextValue {
  t: Translations;
}

const I18nContext = createContext<I18nContextValue>({
  t: en,
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const value: I18nContextValue = {
    t: en,
  };

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
