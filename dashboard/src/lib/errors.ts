import { ApiError } from '../api/flowkit'
import type { TranslationKey } from '../i18n/translations'

type T = (key: TranslationKey, params?: Record<string, string | number>) => string

/** A failed API call as a sentence a non-programmer can act on. `source` says what was being called. */
export function describeError(t: T, err: unknown, source: 'gemini' | 'flow' | 'other' = 'other'): string {
  if (!(err instanceof ApiError)) return t('err.generic', { msg: String(err) })
  if (err.status === 0) return t('err.network')
  if (source === 'gemini') {
    if (err.status === 503) return t('err.geminiOff')
    if (err.status === 429) return t('err.geminiBusy')
    if (err.status === 504) return t('err.geminiSlow')
    if (err.status === 422) return t('err.geminiRefused')
    if (err.status === 502) return t('err.geminiBad')
  }
  if (source === 'flow') {
    if (err.status === 503) return t('err.flowNotConnected')
    if (err.status === 502) return t('err.flow', { msg: err.message })
  }
  if (err.status === 409) return t('err.conflict', { msg: err.message })
  return t('err.generic', { msg: err.message })
}
