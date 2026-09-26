import { useState } from 'react'
import { api } from '../../api/flowkit'
import { useSystemStatus } from '../../api/SystemStatus'
import { useTranslation } from '../../i18n/useTranslation'
import { CREDIT_PRICES, DAILY_FREE_CREDITS, credits } from '../../lib/credits'
import { describeError } from '../../lib/errors'
import type { RunStatus } from '../../types/flow'
import { ActionButton, Notice, Panel } from './ui'

/** A DRAFT run's minimum generations and credits, and the "Bắt đầu" button (the first Flow call). */
export default function EstimateBox({ run, onStarted }: { run: RunStatus; onStarted: (run: RunStatus) => void }) {
  const { t } = useTranslation()
  const system = useSystemStatus()
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const e = run.estimate_remaining
  const total = credits(e)
  const canStart = system.flowReady && system.cooldownSeconds === 0 && !starting

  async function start() {
    setStarting(true)
    setError(null)
    try {
      onStarted(await api.startRun(run.id))
    } catch (err) {
      setError(describeError(t, err, 'flow'))
      setStarting(false)
    }
  }

  return (
    <Panel className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold">{t('estimate.title')}</h2>
      <div className="grid grid-cols-3 gap-3 text-center">
        {([['estimate.refs', e.refs], ['estimate.images', e.images], ['estimate.videos', e.videos]] as const).map(([k, n]) => (
          <div key={k} className="rounded-md py-2" style={{ background: 'var(--surface)' }}>
            <div className="text-xl font-semibold">{n}</div>
            <div className="text-[11px]" style={{ color: 'var(--muted)' }}>{t(k)}</div>
          </div>
        ))}
      </div>
      <div className="text-xs flex flex-col gap-1" style={{ color: 'var(--muted)' }}>
        <span>{t('estimate.imagePrice', { n: CREDIT_PRICES.image })} · {t('estimate.videoPrice', { n: CREDIT_PRICES.video })}</span>
        <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{t('estimate.total', { n: total })}</span>
        <span>{t('estimate.minimum')}</span>
        <span>{t('estimate.daily', { n: DAILY_FREE_CREDITS })}</span>
        <span>{t('estimate.checkpoints')}</span>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <div>
        <ActionButton onClick={start} disabled={!canStart} cost={t('estimate.startCost', { n: total })}>
          {starting ? t('estimate.starting') : t('estimate.start')}
        </ActionButton>
      </div>
    </Panel>
  )
}
