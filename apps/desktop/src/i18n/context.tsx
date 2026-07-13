import { createContext, type ReactNode, useContext } from 'react'

import { TRANSLATIONS } from './catalog'
import type { Translations } from './types'

const DEFAULT_TRANSLATIONS: Translations = TRANSLATIONS.en

export interface I18nContextValue {
  t: Translations
}

const I18nContext = createContext<I18nContextValue>({
  t: DEFAULT_TRANSLATIONS
})

export function I18nProvider({ children }: { children: ReactNode }) {
  return <I18nContext.Provider value={{ t: DEFAULT_TRANSLATIONS }}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext)
}
