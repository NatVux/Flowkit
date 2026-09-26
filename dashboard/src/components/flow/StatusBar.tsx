import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { formatSeconds, useSystemStatus } from '../../api/SystemStatus'
import { useTranslation } from '../../i18n/useTranslation'
import type { TranslationKey } from '../../i18n/translations'

interface Signal {
  key: string
  label: TranslationKey
  ok: boolean
  fix: string
}

/** One line under the header: green "Ready", or each problem with what to do about it. */
export default function StatusBar() {
  const { t } = useTranslation()
  const s = useSystemStatus()

  if (!s.loaded) {
    return <Strip tone="muted"><span>{t('status.checking')}</span></Strip>
  }

  const signals: Signal[] = [
    { key: 'server', label: 'status.server', ok: s.server, fix: t('status.fix.server') },
    // Without a server nothing else can be known: only report it.
    ...(s.server ? [
      { key: 'extension', label: 'status.extension' as TranslationKey, ok: s.extension, fix: t('status.fix.extension') },
      { key: 'flowTab', label: 'status.flowTab' as TranslationKey, ok: !s.extension || s.flowTab, fix: t('status.fix.flowTab') },
      { key: 'cooldown', label: 'status.cooldown' as TranslationKey, ok: s.cooldownSeconds === 0,
        fix: t('status.fix.cooldown', { time: formatSeconds(s.cooldownSeconds) }) },
      { key: 'gemini', label: 'status.gemini' as TranslationKey, ok: s.gemini, fix: t('status.fix.gemini') },
    ] : []),
  ]
  const problems = signals.filter(x => !x.ok)

  if (problems.length === 0) {
    return (
      <Strip tone="ok">
        <CheckCircle2 size={13} style={{ color: 'var(--green)' }} />
        <span style={{ color: 'var(--green)' }}>{t('status.ready')}</span>
        <span className="ml-auto flex items-center gap-3" style={{ color: 'var(--muted)' }}>
          {signals.map(x => <Dot key={x.key} ok label={t(x.label)} />)}
        </span>
      </Strip>
    )
  }

  return (
    <Strip tone="warn">
      <div className="flex flex-col gap-1 w-full">
        <div className="flex items-center gap-2 font-semibold" style={{ color: 'var(--yellow)' }}>
          <AlertTriangle size={13} />
          {t('status.problems', { n: problems.length })}
          <span className="ml-auto flex items-center gap-3 font-normal" style={{ color: 'var(--muted)' }}>
            {signals.map(x => <Dot key={x.key} ok={x.ok} label={t(x.label)} />)}
          </span>
        </div>
        {problems.map(p => (
          <div key={p.key} className="flex gap-2" style={{ color: 'var(--text)' }}>
            <span style={{ color: 'var(--red)' }}>●</span>
            <span><b>{t(p.label)}:</b> {p.fix}</span>
          </div>
        ))}
      </div>
    </Strip>
  )
}

function Dot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: ok ? 'var(--green)' : 'var(--red)' }} />
      {label}
    </span>
  )
}

function Strip({ tone, children }: { tone: 'ok' | 'warn' | 'muted'; children: React.ReactNode }) {
  const border = tone === 'warn' ? 'var(--yellow)' : 'var(--border)'
  return (
    <div className="flex items-center gap-2 px-5 py-2 text-[11px] border-b flex-shrink-0"
         style={{ background: 'var(--surface)', borderColor: border }}
         role={tone === 'warn' ? 'alert' : 'status'}>
      {children}
    </div>
  )
}
