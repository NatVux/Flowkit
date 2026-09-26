import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { ApiError, api } from '../api/flowkit'
import { useSystemStatus } from '../api/SystemStatus'
import { useTranslation } from '../i18n/useTranslation'
import type { TranslationKey } from '../i18n/translations'
import { describeError } from '../lib/errors'
import { problemMessage } from '../lib/problems'
import { inputClass, inputStyle } from '../lib/styles'
import type { AIGeneration, PlanProblem, RunStatus, StoryPlan } from '../types/flow'
import EstimateBox from '../components/flow/EstimateBox'
import { ActionButton, Label, Modal, Notice, PageTitle, Panel } from '../components/flow/ui'

const VIDEO_PROMPT_SOFT = 350
const VIDEO_PROMPT_HARD = 400

/** Review and edit a story plan, rewrite it, or use it (apply + a DRAFT run with its estimate). */
export default function StoryPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const system = useSystemStatus()

  const [gen, setGen] = useState<AIGeneration | null>(null)
  const [plan, setPlan] = useState<StoryPlan | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<'save' | 'use' | 'rewrite' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [problems, setProblems] = useState<PlanProblem[]>([])
  const [savedNote, setSavedNote] = useState(false)
  const [rewriteOpen, setRewriteOpen] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [run, setRun] = useState<RunStatus | null>(null)
  const [runId, setRunId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const g = await api.generation(id)
      setGen(g)
      setPlan(structuredClone(g.output))
      setDirty(false)
      setProblems([])
      if (g.status === 'APPLIED' && g.video_id) {
        const runs = await api.runs({ videoId: g.video_id })
        const latest = runs[0]
        if (latest) {
          setRunId(latest.id)
          if (latest.status === 'DRAFT') setRun(await api.run(latest.id))
        }
      }
    } catch (err) {
      setLoadError(err instanceof ApiError && err.status === 404 ? t('story.notFound') : describeError(t, err))
    }
  }, [id, t])

  useEffect(() => { Promise.resolve().then(load) }, [load])

  if (loadError) return <Notice tone="error">{loadError}</Notice>
  if (!gen || !plan) return <p className="text-xs" style={{ color: 'var(--muted)' }}>{t('story.loading')}</p>

  const applied = gen.status === 'APPLIED'
  const editable = !applied && busy === null

  function edit(mutate: (p: StoryPlan) => void) {
    setPlan(prev => {
      if (!prev) return prev
      const next = structuredClone(prev)
      mutate(next)
      return next
    })
    setDirty(true)
    setSavedNote(false)
  }

  const fieldError = (loc: string) => problems.filter(p => p.loc === loc).map(p => problemMessage(t, p.msg)).join('; ')

  function problemLabel(loc: string): string {
    const m = loc.match(/^(scenes|characters|locations)\.(\d+)\.(\w+)/)
    if (m) {
      const n = Number(m[2]) + 1
      const where = m[1] === 'scenes' ? t('story.scene', { n }) : `${t('story.characters')} ${n}`
      const field: Record<string, TranslationKey> = {
        video_prompt: 'story.field.videoPrompt', image_prompt: 'story.field.imagePrompt', summary: 'story.field.summary',
        narration: 'story.field.narration', description: 'story.field.description',
      }
      return field[m[3]] ? `${where} · ${t(field[m[3]])}` : `${where} · ${m[3]}`
    }
    const top: Record<string, TranslationKey> = { title: 'story.field.title', story: 'story.field.story', logline: 'story.field.logline' }
    return top[loc] ? t(top[loc]) : loc
  }

  /** PUT the edits; true when saved (or nothing to save). */
  async function save(): Promise<boolean> {
    if (!dirty || !plan || !gen) return true
    setBusy('save')
    setError(null)
    try {
      const updated = await api.saveGeneration(gen.id, plan)
      setGen(updated)
      setPlan(structuredClone(updated.output))
      setDirty(false)
      setProblems([])
      setSavedNote(true)
      return true
    } catch (err) {
      const details = err instanceof ApiError ? err.details as { problems?: PlanProblem[] } | null : null
      if (details?.problems) setProblems(details.problems)
      else setError(describeError(t, err))
      return false
    } finally {
      setBusy(null)
    }
  }

  async function use() {
    if (!gen) return
    if (!(await save())) return
    setBusy('use')
    setError(null)
    try {
      if (!gen.video_id) throw new Error('video_id')
      await api.applyGeneration(gen.id, gen.video_id)
      const created = await api.createRun(gen.video_id)
      setRun(created)
      setRunId(created.id)
      setGen({ ...gen, status: 'APPLIED' })
    } catch (err) {
      setError(describeError(t, err))
    } finally {
      setBusy(null)
    }
  }

  async function rewrite() {
    if (!gen || !plan) return
    setRewriteOpen(false)
    setBusy('rewrite')
    setError(null)
    const input = gen.input
    const brief = (input?.brief ?? plan.logline) + (feedback.trim() ? `\n\nGóp ý khi viết lại: ${feedback.trim()}` : '')
    try {
      const next = await api.storyPlan({
        project_id: gen.project_id, video_id: gen.video_id, brief: brief.slice(0, 4000),
        scene_count: input?.scene_count ?? plan.scenes.length, language: input?.language ?? null,
      })
      setFeedback('')
      navigate(`/truyen/${next.id}`)
    } catch (err) {
      setError(describeError(t, err, 'gemini'))
    } finally {
      setBusy(null)
    }
  }

  const entities = [
    ...plan.characters.map((c, i) => ({ kind: 'characters' as const, i, name: c.name, type: c.entity_type, description: c.description })),
    ...plan.locations.map((l, i) => ({ kind: 'locations' as const, i, name: l.name, type: 'location', description: l.description })),
  ]

  return (
    <div className="max-w-4xl flex flex-col gap-4">
      <PageTitle title={t('story.title')} subtitle={applied ? t('story.applied') : t('story.subtitle')} />

      {problems.length > 0 && (
        <Notice tone="error">
          <div className="font-semibold mb-1">{t('story.invalid')}</div>
          <ul className="list-disc pl-5">
            {problems.map((p, i) => <li key={i}><b>{problemLabel(p.loc)}:</b> {problemMessage(t, p.msg)}</li>)}
          </ul>
        </Notice>
      )}

      <Panel className="flex flex-col gap-3">
        <div>
          <Label htmlFor="title">{t('story.field.title')}</Label>
          <input id="title" value={plan.title} disabled={!editable} maxLength={100}
                 onChange={e => edit(p => { p.title = e.target.value })} className={inputClass} style={inputStyle} />
          <FieldError text={fieldError('title')} />
        </div>
        <div>
          <Label htmlFor="logline">{t('story.field.logline')}</Label>
          <input id="logline" value={plan.logline} disabled={!editable} maxLength={300}
                 onChange={e => edit(p => { p.logline = e.target.value })} className={inputClass} style={inputStyle} />
          <FieldError text={fieldError('logline')} />
        </div>
        <div>
          <Label htmlFor="story">{t('story.field.story')}</Label>
          <textarea id="story" rows={5} value={plan.story} disabled={!editable} maxLength={4000}
                    onChange={e => edit(p => { p.story = e.target.value })} className={inputClass} style={inputStyle} />
          <FieldError text={fieldError('story')} />
        </div>
      </Panel>

      <Panel className="flex flex-col gap-3">
        <div>
          <h2 className="text-sm font-semibold">{t('story.characters')}</h2>
          <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('story.charactersHint')}</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {entities.map(ent => (
            <div key={`${ent.kind}-${ent.i}`} className="rounded-md p-3" style={{ background: 'var(--surface)' }}>
              <div className="flex items-baseline gap-2 mb-1">
                <span className="text-sm font-semibold">{ent.name}</span>
                <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{t(`story.entity.${ent.type}` as TranslationKey)}</span>
              </div>
              <Advanced label={t('story.field.description')}>
                <textarea rows={3} value={ent.description} disabled={!editable} maxLength={600}
                          aria-label={`${ent.name} · ${t('story.field.description')}`}
                          onChange={e => edit(p => { p[ent.kind][ent.i].description = e.target.value })}
                          className={inputClass} style={inputStyle} />
                <FieldError text={fieldError(`${ent.kind}.${ent.i}.description`)} />
              </Advanced>
            </div>
          ))}
        </div>
      </Panel>

      <h2 className="text-sm font-semibold mt-2">{t('story.scenes')}</h2>
      {plan.scenes.map((scene, i) => {
        const vlen = scene.video_prompt.length
        const vcolor = vlen > VIDEO_PROMPT_HARD ? 'var(--red)' : vlen > VIDEO_PROMPT_SOFT ? 'var(--yellow)' : 'var(--muted)'
        return (
          <Panel key={i} className="flex flex-col gap-3">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-semibold">{t('story.scene', { n: i + 1 })}</span>
              {scene.continues_previous && <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{t('story.continues')}</span>}
              {scene.character_names.length > 0 && (
                <span className="ml-auto text-[11px]" style={{ color: 'var(--muted)' }}>
                  {t('story.inScene', { names: scene.character_names.join(', ') })}
                </span>
              )}
            </div>
            <div>
              <Label htmlFor={`summary-${i}`}>{t('story.field.summary')}</Label>
              <input id={`summary-${i}`} value={scene.summary} disabled={!editable} maxLength={200}
                     onChange={e => edit(p => { p.scenes[i].summary = e.target.value })} className={inputClass} style={inputStyle} />
              <FieldError text={fieldError(`scenes.${i}.summary`)} />
            </div>
            <div>
              <Label htmlFor={`narration-${i}`}>{t('story.field.narration')}</Label>
              <textarea id={`narration-${i}`} rows={2} value={scene.narration ?? ''} disabled={!editable} maxLength={600}
                        onChange={e => edit(p => { p.scenes[i].narration = e.target.value })} className={inputClass} style={inputStyle} />
              <FieldError text={fieldError(`scenes.${i}.narration`)} />
            </div>
            <Advanced label={t('story.advanced')}
                      open={problems.some(p => p.loc === `scenes.${i}.image_prompt` || p.loc === `scenes.${i}.video_prompt`)}>
              <div className="flex flex-col gap-3">
                <div>
                  <Label htmlFor={`image-${i}`}>{t('story.field.imagePrompt')}</Label>
                  <textarea id={`image-${i}`} rows={3} value={scene.image_prompt} disabled={!editable} maxLength={1500}
                            onChange={e => edit(p => { p.scenes[i].image_prompt = e.target.value })} className={inputClass} style={inputStyle} />
                  <FieldError text={fieldError(`scenes.${i}.image_prompt`)} />
                </div>
                <div>
                  <Label htmlFor={`video-${i}`} hint={t('story.videoPromptHint')}>{t('story.field.videoPrompt')}</Label>
                  <textarea id={`video-${i}`} rows={3} value={scene.video_prompt} disabled={!editable} maxLength={1500}
                            onChange={e => edit(p => { p.scenes[i].video_prompt = e.target.value })} className={inputClass} style={inputStyle} />
                  <div className="text-[11px] text-right" style={{ color: vcolor }}>{t('story.chars', { n: vlen })}</div>
                  <FieldError text={fieldError(`scenes.${i}.video_prompt`)} />
                </div>
              </div>
            </Advanced>
          </Panel>
        )
      })}

      {error && <Notice tone="error">{error}</Notice>}

      {!applied && (
        <div className="flex flex-wrap items-center gap-3 sticky bottom-0 py-3" style={{ background: 'var(--bg)' }}>
          <ActionButton onClick={use} disabled={busy !== null}>{busy === 'use' ? t('story.using') : t('story.use')}</ActionButton>
          <ActionButton tone="secondary" onClick={save} disabled={!dirty || busy !== null}>
            {busy === 'save' ? t('story.saving') : t('story.save')}
          </ActionButton>
          <ActionButton tone="secondary" onClick={() => setRewriteOpen(true)} disabled={busy !== null || !system.gemini}>
            {t('story.rewrite')}
          </ActionButton>
          {busy === 'rewrite' && (
            <span className="flex items-center gap-2 text-xs" style={{ color: 'var(--muted)' }}>
              <Loader2 size={14} className="animate-spin" />{t('story.rewriting')}
            </span>
          )}
          {savedNote && !dirty && <span className="text-xs" style={{ color: 'var(--green)' }}>{t('story.saved')}</span>}
        </div>
      )}

      {applied && run?.status === 'DRAFT' && (
        <EstimateBox run={run} onStarted={started => navigate(`/chay/${started.id}`)} />
      )}
      {applied && runId && run?.status !== 'DRAFT' && (
        <div><Link to={`/chay/${runId}`} className="text-sm underline" style={{ color: 'var(--accent)' }}>{t('story.openRun')}</Link></div>
      )}

      {rewriteOpen && (
        <Modal title={t('story.rewriteTitle')} onClose={() => setRewriteOpen(false)}>
          <Label htmlFor="feedback" hint={t('story.rewriteHint')}>{t('story.rewrite')}</Label>
          <textarea id="feedback" rows={3} value={feedback} onChange={e => setFeedback(e.target.value)}
                    className={inputClass} style={inputStyle} maxLength={500} />
          {dirty && <Notice tone="warn">{t('story.rewriteDiscard')}</Notice>}
          <div className="flex gap-2 justify-end">
            <ActionButton tone="secondary" onClick={() => setRewriteOpen(false)}>{t('story.cancel')}</ActionButton>
            <ActionButton onClick={rewrite}>{t('story.rewriteConfirm')}</ActionButton>
          </div>
        </Modal>
      )}
    </div>
  )
}

function FieldError({ text }: { text: string }) {
  if (!text) return null
  return <p className="text-[11px] mt-1" style={{ color: 'var(--red)' }}>{text}</p>
}

function Advanced({ label, children, open: initiallyOpen = false }: { label: string; children: React.ReactNode; open?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen)
  const shown = open || initiallyOpen
  return (
    <div>
      <button type="button" onClick={() => setOpen(!shown)} className="flex items-center gap-1 text-[11px]" style={{ color: 'var(--muted)' }}>
        {shown ? <ChevronDown size={12} /> : <ChevronRight size={12} />}{label}
      </button>
      {shown && <div className="mt-2">{children}</div>}
    </div>
  )
}
