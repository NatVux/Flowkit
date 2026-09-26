import type { TranslationKey } from '../i18n/translations'

type T = (key: TranslationKey, params?: Record<string, string | number>) => string

/** A plan-validation message from the backend (PUT /api/ai/generations/{id}) in the reader's language.
 *  Unknown messages are shown as they came. */
export function problemMessage(t: T, msg: string): string {
  if (/cuts to another shot/.test(msg)) return t('problem.cut')
  let m = msg.match(/^(\d+) chars, max (\d+)$/)
  if (m) return t('problem.tooLong', { n: m[1], max: m[2] })
  m = msg.match(/^(\d+%) accented words$/)
  if (m) return t('problem.notEnglish', { share: m[1] })
  if (/should have at least 1 character/.test(msg)) return t('problem.empty')
  m = msg.match(/should have at most (\d+) characters?/)
  if (m) return t('problem.maxLength', { max: m[1] })
  m = msg.match(/references undefined entities: \[(.*)\]/)
  if (m) return t('problem.unknownNames', { names: m[1].replace(/'/g, '') })
  m = msg.match(/duplicate entity name: '(.*)'/)
  if (m) return t('problem.duplicateName', { name: m[1] })
  if (/first scene cannot continue/.test(msg)) return t('problem.firstContinues')
  return msg
}
