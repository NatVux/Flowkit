import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/flowkit'
import { useTranslation } from '../i18n/useTranslation'
import type { TranslationKey } from '../i18n/translations'
import { describeError } from '../lib/errors'
import type { Project, Video } from '../types'
import type { AIGeneration, RunSummary } from '../types/flow'
import { ActionButton, Modal, Notice, PageTitle, Panel } from '../components/flow/ui'

interface Row {
  project: Project
  videos: Video[]
  runs: RunSummary[]           // newest first
  generations: AIGeneration[]  // newest first
  sceneCount: number
}

/** Every project, newest first: its latest run (open it) or, without a run, a draft to continue or delete. */
export default function MyVideosPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Row | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const projects = (await api.projects()).filter(p => p.status !== 'DELETED')
      const loaded = await Promise.all(projects.map(async project => {
        const [videos, runs, generations] = await Promise.all([
          api.videos(project.id), api.runs({ projectId: project.id }), api.generations(project.id).catch(() => []),
        ])
        const scenes = await Promise.all(videos.map(v => api.scenes(v.id).catch(() => [])))
        return { project, videos, runs, generations, sceneCount: scenes.reduce((n, s) => n + s.length, 0) }
      }))
      const newest = (r: Row) => r.runs[0]?.updated_at ?? r.project.updated_at ?? r.project.created_at
      setRows(loaded.sort((a, b) => newest(b).localeCompare(newest(a))))
    } catch (err) {
      setError(describeError(t, err))
    }
  }, [t])

  useEffect(() => { Promise.resolve().then(load) }, [load])

  function continueDraft(row: Row) {
    const draft = row.generations.find(g => g.operation === 'STORY_PLAN')
    if (draft) navigate(`/truyen/${draft.id}`)
    else navigate(`/tao-moi?du-an=${row.project.id}`)  // no story yet: write one for this project
  }

  async function deleteDraft() {
    if (!deleting) return
    setBusy(true)
    try {
      await api.deleteProject(deleting.project.id)
      setDeleting(null)
      await load()
    } catch (err) {
      setError(describeError(t, err))
    } finally {
      setBusy(false)
    }
  }

  const date = (iso: string) => new Date(iso).toLocaleString('vi-VN')

  return (
    <div className="max-w-4xl flex flex-col gap-3">
      <PageTitle title={t('my.title')} subtitle={t('my.subtitle')} />
      {error && <Notice tone="error">{error}</Notice>}
      {rows === null && !error && <p className="text-xs" style={{ color: 'var(--muted)' }}>{t('my.loading')}</p>}
      {rows?.length === 0 && (
        <Panel className="flex flex-col items-start gap-3">
          <p className="text-sm">{t('my.empty')}</p>
          <ActionButton onClick={() => navigate('/tao-moi')}>{t('my.createFirst')}</ActionButton>
        </Panel>
      )}
      {rows?.map(row => {
        const run = row.runs[0]
        const title = row.videos[0]?.title || row.project.name
        return (
          <Panel key={row.project.id} className="flex flex-wrap items-center gap-3">
            <div className="flex flex-col gap-0.5 min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold truncate">{title}</span>
                {run
                  ? <Badge tone={run.status}>{t(`run.state.${run.status}` as TranslationKey)}</Badge>
                  : <Badge tone="DRAFT">{t('my.draft')}</Badge>}
              </div>
              <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
                {row.project.name !== title && `${row.project.name} · `}
                {t('my.created', { date: date(run?.created_at ?? row.project.created_at) })}
                {run?.stage && run.status !== 'COMPLETED' && ` · ${t('my.stage', { stage: t(`run.stage.${run.stage}` as TranslationKey) })}`}
                {!run && ` · ${t('my.draftHint')}`}
              </span>
              {row.runs.length > 1 && (
                <details className="text-[11px]" style={{ color: 'var(--muted)' }}>
                  <summary>{t('my.olderRuns', { n: row.runs.length - 1 })}</summary>
                  <ul className="pl-4">
                    {row.runs.slice(1).map(r => (
                      <li key={r.id}>
                        <Link to={`/chay/${r.id}`} className="underline">{date(r.created_at)}</Link>
                        {' · '}{t(`run.state.${r.status}` as TranslationKey)}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
            {run ? (
              <ActionButton onClick={() => navigate(`/chay/${run.id}`)}>{t('my.open')}</ActionButton>
            ) : (
              <div className="flex gap-2">
                <ActionButton onClick={() => continueDraft(row)}>{t('my.continue')}</ActionButton>
                <ActionButton tone="secondary" onClick={() => setDeleting(row)}>{t('my.deleteDraft')}</ActionButton>
              </div>
            )}
          </Panel>
        )
      })}

      {deleting && (
        <Modal title={t('my.deleteTitle', { name: deleting.videos[0]?.title || deleting.project.name })} onClose={() => setDeleting(null)}>
          <p className="text-xs leading-relaxed">{t('my.deleteBody')}</p>
          {deleting.sceneCount > 0 && <Notice tone="warn">{t('my.deleteScenes', { n: deleting.sceneCount })}</Notice>}
          <div className="flex gap-2 justify-end">
            <ActionButton tone="secondary" onClick={() => setDeleting(null)} disabled={busy}>{t('run.cancelAction')}</ActionButton>
            <ActionButton tone="danger" onClick={deleteDraft} disabled={busy}>{busy ? t('my.deleting') : t('my.deleteDraft')}</ActionButton>
          </div>
        </Modal>
      )}
    </div>
  )
}

function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  const color = tone === 'COMPLETED' ? 'var(--green)' : tone === 'DRAFT' ? 'var(--muted)'
    : tone === 'NEEDS_USER_ACTION' || tone === 'PAUSED' || tone === 'FAILED' ? 'var(--yellow)'
    : tone === 'CANCELLED' ? 'var(--muted)' : 'var(--accent)'
  return <span className="text-[10px] rounded-full px-2 py-0.5 border whitespace-nowrap" style={{ color, borderColor: color }}>{children}</span>
}
