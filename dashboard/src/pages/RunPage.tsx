import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { CheckCircle2, CircleDashed, FolderOpen, Download, Loader2 } from 'lucide-react'
import { ApiError, api } from '../api/flowkit'
import { useSystemStatus } from '../api/SystemStatus'
import { useWebSocketContext } from '../api/useWebSocketContext'
import { useTranslation } from '../i18n/useTranslation'
import type { TranslationKey } from '../i18n/translations'
import { CREDIT_PRICES } from '../lib/credits'
import { describeError } from '../lib/errors'
import { inputClass, inputStyle } from '../lib/styles'
import {
  approvalCost, canRedo, descendants, isActive, prefixOf, reasonKey, scenesWipedByNewReference,
  type SceneLike,
} from '../lib/runLogic'
import type { Character, Project, Scene, Video } from '../types'
import type { Material, RunItem, RunStatus, StageName } from '../types/flow'
import EstimateBox from '../components/flow/EstimateBox'
import { ActionButton, Modal, Notice, Panel } from '../components/flow/ui'

const STAGES: StageName[] = ['REFS', 'IMAGES', 'VIDEOS', 'CONCAT']
const LIVE_EVENTS = new Set(['pipeline_update', 'pipeline_items_queued', 'request_update'])

type Dialog =
  | { kind: 'approveVideos'; videos: number; credits: number }
  | { kind: 'cancel' }
  | { kind: 'redoRef'; item: RunItem; name: string; affected: SceneLike[] }
  | { kind: 'redoImage'; scene: SceneLike; n: number; children: SceneLike[]; withChildren: boolean }
  | { kind: 'redoVideo'; scene: SceneLike; n: number }
  | { kind: 'editPrompt'; scene: SceneLike; n: number; field: 'prompt' | 'video_prompt'; text: string }

/** Progress of one pipeline run: stages, images and clips, approve / redo / resume, the final video. */
export default function RunPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams<{ id: string }>()
  const system = useSystemStatus()
  const { lastEvent, isConnected } = useWebSocketContext()

  const [run, setRun] = useState<RunStatus | null>(null)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [chars, setChars] = useState<Character[]>([])
  const [video, setVideo] = useState<Video | null>(null)
  const [project, setProject] = useState<Project | null>(null)
  const [materials, setMaterials] = useState<Material[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [refreshingImages, setRefreshingImages] = useState(false)
  const refreshedOnce = useRef(false)

  const load = useCallback(async () => {
    try {
      const r = await api.run(id)
      const [s, c, v, p] = await Promise.all([
        api.scenes(r.video_id), api.characters(r.project_id), api.video(r.video_id), api.project(r.project_id),
      ])
      setRun(r); setScenes(s); setChars(c); setVideo(v); setProject(p)
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError && err.status === 404 ? t('run.notFound') : describeError(t, err))
    }
  }, [id, t])

  useEffect(() => { Promise.resolve().then(load) }, [load])
  useEffect(() => { api.materials().then(setMaterials).catch(() => undefined) }, [])

  // Live: refetch (debounced) on pipeline/request events; poll when the WebSocket is down.
  const active = run ? isActive(run.status) : false
  useEffect(() => {
    if (!lastEvent || !LIVE_EVENTS.has(lastEvent.type) || !active) return
    const timer = setTimeout(load, 500)
    return () => clearTimeout(timer)
  }, [lastEvent, active, load])
  useEffect(() => {
    if (!active) return
    const timer = setInterval(load, isConnected ? 15_000 : 5_000)
    return () => clearInterval(timer)
  }, [active, isConnected, load])

  if (loadError) return <Notice tone="error">{loadError}</Notice>
  if (!run || !project) return <p className="text-xs" style={{ color: 'var(--muted)' }}>{t('run.loading')}</p>

  const p = prefixOf(run.orientation)
  const sceneList = [...scenes].sort((a, b) => a.display_order - b.display_order) as unknown as (Scene & SceneLike)[]
  const items = (stage: StageName) => new Map((run.stages[stage]?.items ?? []).map(i => [i.target_id, i]))
  const refItems = items('REFS')
  const imageItems = items('IMAGES')
  const videoItems = items('VIDEOS')
  const sceneNumber = (sceneId: string) => sceneList.findIndex(s => s.id === sceneId) + 1
  const stylePrefix = materials.find(m => m.id === project.material)?.scene_prefix ?? ''
  // What a scene is about, without the style words every prompt starts with.
  const sceneText = (s: Scene) => {
    if (s.narrator_text) return s.narrator_text
    const prompt = s.prompt ?? ''
    return stylePrefix && prompt.startsWith(stylePrefix) ? prompt.slice(stylePrefix.length).trim() : prompt
  }

  async function act(name: string, fn: () => Promise<RunStatus | unknown>) {
    setBusy(name)
    setError(null)
    try {
      await fn()
      setDialog(null)
      await load()
    } catch (err) {
      setError(describeError(t, err, 'flow'))
    } finally {
      setBusy(null)
    }
  }

  function approve() {
    if (!run) return
    const cost = approvalCost(run)
    if (cost.next === 'VIDEOS') {
      setDialog({ kind: 'approveVideos', videos: run.estimate_remaining.videos, credits: cost.credits })
      return
    }
    act('approve', () => api.approveRun(run.id))
  }

  function onImageError() {
    if (refreshedOnce.current || !system.flowReady || !project) return
    refreshedOnce.current = true  // once per page: a Flow read, no credit
    setRefreshingImages(true)
    api.refreshUrls(project.id).catch(() => undefined).finally(() => { setRefreshingImages(false); load() })
  }

  const cost = approvalCost(run)
  const canAct = system.flowReady && system.cooldownSeconds === 0 && busy === null
  const redo = (body: Parameters<typeof api.redo>[1]) => act('redo', () => api.redo(run.id, body))

  return (
    <div className="max-w-6xl flex flex-col gap-4">
      {/* title */}
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-lg font-semibold">{video?.title || t('run.untitled')}</h1>
        <span className="text-xs" style={{ color: 'var(--muted)' }}>{project.name}</span>
        <StateBadge state={run.status} label={t(`run.state.${run.status}` as TranslationKey)} />
      </div>

      {/* stage rail */}
      <div className="grid grid-cols-4 gap-2">
        {STAGES.map(stage => {
          const s = run.stages[stage]
          const current = run.stage === stage && run.status !== 'COMPLETED'
          const done = s ? s.total > 0 && s.done === s.total : run.status === 'COMPLETED'
          return (
            <div key={stage} className="rounded-md border px-3 py-2 text-xs flex items-center gap-2"
                 style={{ background: 'var(--card)', borderColor: current ? 'var(--accent)' : 'var(--border)' }}>
              {done || run.status === 'COMPLETED'
                ? <CheckCircle2 size={14} style={{ color: 'var(--green)' }} />
                : current && run.status === 'RUNNING' ? <Loader2 size={14} className="animate-spin" style={{ color: 'var(--accent)' }} />
                : <CircleDashed size={14} style={{ color: 'var(--muted)' }} />}
              <span style={{ color: current ? 'var(--text)' : 'var(--muted)' }}>{t(`run.stage.${stage}` as TranslationKey)}</span>
              {s && s.total > 0 && <span className="ml-auto" style={{ color: 'var(--muted)' }}>{s.done}/{s.total}</span>}
            </div>
          )
        })}
      </div>

      {/* what is going on, and what you can do */}
      {run.status === 'DRAFT' && <EstimateBox run={run} onStarted={() => load()} />}
      {run.status !== 'DRAFT' && (
        <Panel className="flex flex-col gap-3">
          <StatusMessage run={run} t={t} sceneNumber={sceneNumber} names={new Map(chars.map(c => [c.id, c.name]))} />
          {error && <Notice tone="error">{error}</Notice>}
          <div className="flex flex-wrap gap-2">
            {run.status === 'AWAITING_APPROVAL' && (
              <ActionButton onClick={approve} disabled={!canAct}
                            cost={cost.next ? t('run.approveCost', { n: cost.credits }) : undefined}>
                {busy === 'approve' ? t('run.approving') : t('run.approve')}
              </ActionButton>
            )}
            {(run.status === 'PAUSED' || run.status === 'NEEDS_USER_ACTION') && (
              <ActionButton onClick={() => act('resume', () => api.resumeRun(run.id))} disabled={!canAct}>
                {busy === 'resume' ? t('run.resuming') : t('run.resume')}
              </ActionButton>
            )}
            {isActive(run.status) && (
              <ActionButton tone="secondary" onClick={() => setDialog({ kind: 'cancel' })} disabled={busy !== null}>
                {t('run.cancelRun')}
              </ActionButton>
            )}
          </div>
          <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('run.spent', { n: run.generations_spent })}</p>
        </Panel>
      )}

      {/* final video */}
      {run.final_url && (
        <Panel className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">{t('run.final')}</h2>
          <video src={run.final_url} controls className="rounded-md max-h-[70vh] self-start"
                 style={{ background: '#000', maxWidth: run.orientation === 'VERTICAL' ? '360px' : '100%' }} />
          <div className="flex gap-2">
            <ActionButton tone="secondary" onClick={() => run.final_path && api.openFolder(run.final_path).catch(err => setError(describeError(t, err)))}>
              <span className="flex items-center gap-1.5"><FolderOpen size={14} />{t('run.openFolder')}</span>
            </ActionButton>
            <a href={run.final_url} download className="inline-flex items-center gap-1.5 rounded-md px-4 py-2 text-sm"
               style={{ ...inputStyle }}><Download size={14} />{t('run.download')}</a>
          </div>
        </Panel>
      )}

      {refreshingImages && <Notice tone="info">{t('run.imageRefreshing')}</Notice>}

      {/* reference images */}
      <h2 className="text-sm font-semibold mt-2">{t('run.refs')}</h2>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))' }}>
        {chars.map(c => {
          const item = refItems.get(c.id)
          return (
            <Card key={c.id} title={c.name} status={item ? t(`run.item.${item.status}` as TranslationKey) : undefined}
                  statusTone={item?.status}>
              <Thumb src={c.reference_image_url} alt={c.name} empty={t('run.noImage')} square onError={onImageError} />
              {item?.error_code && <ReasonLine text={t(reasonKey(item.error_code), { msg: item.error_message ?? '' })} />}
              {canRedo(run, 'REFS', item) && (
                <SmallButton disabled={!canAct} onClick={() => setDialog({
                  kind: 'redoRef', item: item!, name: c.name, affected: scenesWipedByNewReference(sceneList, c.name, p),
                })}>{t('run.redo')}</SmallButton>
              )}
            </Card>
          )
        })}
      </div>

      {/* scenes */}
      <h2 className="text-sm font-semibold mt-2">{t('run.scenes')}</h2>
      <div className="grid gap-3" style={{ gridTemplateColumns: run.orientation === 'VERTICAL' ? 'repeat(auto-fill, minmax(220px, 1fr))' : 'repeat(auto-fill, minmax(320px, 1fr))' }}>
        {sceneList.map((s, i) => {
          const n = i + 1
          const img = imageItems.get(s.id)
          const vid = videoItems.get(s.id)
          const clip = vid?.local_url || (s[`${p}_video_url` as keyof Scene] as string | null)
          const image = s[`${p}_image_url` as keyof Scene] as string | null
          const children = descendants(sceneList, s.id)
          const item = vid ?? img
          return (
            <Card key={s.id} title={t('run.sceneN', { n })}
                  status={item ? t(`run.item.${item.status}` as TranslationKey) : undefined} statusTone={item?.status}>
              {clip && (run.stage === 'VIDEOS' || run.stage === 'CONCAT' || run.status === 'COMPLETED')
                ? <video src={`${clip}#t=0.1`} preload="metadata" controls className="w-full rounded object-contain"
                         style={{ background: '#000', aspectRatio: run.orientation === 'VERTICAL' ? '9 / 16' : '16 / 9' }} />
                : <Thumb src={image} alt={t('run.sceneN', { n })} empty={t('run.noImage')} vertical={run.orientation === 'VERTICAL'} onError={onImageError} />}
              <p className="text-[11px] line-clamp-3" style={{ color: 'var(--muted)' }} title={s.prompt ?? ''}>{sceneText(s)}</p>
              {item?.error_code && <ReasonLine text={t(reasonKey(item.error_code), { msg: item.error_message ?? '' })} />}
              <div className="flex flex-wrap gap-1.5">
                {canRedo(run, 'IMAGES', img) && (<>
                  <SmallButton disabled={!canAct} onClick={() => setDialog({ kind: 'redoImage', scene: s, n, children, withChildren: children.length > 0 })}>
                    {t('run.redoImage')}
                  </SmallButton>
                  <SmallButton disabled={!canAct} onClick={() => setDialog({ kind: 'editPrompt', scene: s, n, field: 'prompt', text: s.prompt ?? '' })}>
                    {t('run.editPrompt')}
                  </SmallButton>
                </>)}
                {canRedo(run, 'VIDEOS', vid) && (<>
                  <SmallButton disabled={!canAct} onClick={() => setDialog({ kind: 'redoVideo', scene: s, n })}
                               cost={t('run.redoVideoCost', { n: CREDIT_PRICES.video })}>
                    {t('run.redoVideo')}
                  </SmallButton>
                  <SmallButton disabled={!canAct} onClick={() => setDialog({ kind: 'editPrompt', scene: s, n, field: 'video_prompt', text: s.video_prompt ?? '' })}>
                    {t('run.editPrompt')}
                  </SmallButton>
                </>)}
              </div>
            </Card>
          )
        })}
      </div>

      {run.warnings.length > 0 && (
        <details className="text-[11px]" style={{ color: 'var(--muted)' }}>
          <summary>{t('run.warnings')}</summary>
          <ul className="list-disc pl-5 mt-1">{run.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </details>
      )}

      {/* dialogs */}
      {dialog?.kind === 'approveVideos' && (
        <Confirm title={t('run.approveVideosTitle')} busy={busy === 'approve'} onClose={() => setDialog(null)}
                 confirm={t('run.confirm')} cost={t('run.redoVideoCost', { n: dialog.credits })} t={t}
                 onConfirm={() => act('approve', () => api.approveRun(run.id))}>
          <p>{t('run.approveVideosBody', { n: dialog.videos, credits: dialog.credits })}</p>
        </Confirm>
      )}
      {dialog?.kind === 'cancel' && (
        <Confirm title={t('run.cancelRunTitle')} busy={busy === 'cancel'} onClose={() => setDialog(null)} danger
                 confirm={t('run.cancelRun')} t={t} onConfirm={() => act('cancel', () => api.cancelRun(run.id))}>
          <p>{t('run.cancelRunBody')}</p>
        </Confirm>
      )}
      {dialog?.kind === 'redoRef' && (
        <Confirm title={t('run.redoRefTitle', { name: dialog.name })} busy={busy === 'redo'} onClose={() => setDialog(null)}
                 confirm={t('run.redo')} t={t} danger={dialog.affected.length > 0}
                 onConfirm={() => redo({ target_type: 'character', target_id: dialog.item.target_id, confirm_invalidates: dialog.affected.length > 0 })}>
          {dialog.affected.length > 0 ? (<>
            <p>{t('run.redoRefAffected', { name: dialog.name })}</p>
            <ul className="list-disc pl-5">{dialog.affected.map(s => <li key={s.id}>{t('run.sceneN', { n: sceneNumber(s.id) })}</li>)}</ul>
          </>) : <p>{t('run.redoRefNone', { name: dialog.name })}</p>}
        </Confirm>
      )}
      {dialog?.kind === 'redoImage' && (
        <Confirm title={t('run.redoImageTitle', { n: dialog.n })} busy={busy === 'redo'} onClose={() => setDialog(null)}
                 confirm={t('run.redo')} t={t}
                 onConfirm={() => redo({ target_type: 'scene', target_id: dialog.scene.id, include_descendants: dialog.withChildren })}>
          {dialog.children.length > 0 && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={dialog.withChildren} className="accent-blue-500"
                     onChange={e => setDialog({ ...dialog, withChildren: e.target.checked })} />
              {t('run.redoImageChildren', { list: dialog.children.map(c => sceneNumber(c.id)).join(', ') })}
            </label>
          )}
        </Confirm>
      )}
      {dialog?.kind === 'redoVideo' && (
        <Confirm title={t('run.redoVideoTitle', { n: dialog.n })} busy={busy === 'redo'} onClose={() => setDialog(null)}
                 confirm={t('run.redoVideo')} cost={t('run.redoVideoCost', { n: CREDIT_PRICES.video })} t={t}
                 onConfirm={() => redo({ target_type: 'scene', target_id: dialog.scene.id })}>
          <p>{t('run.redoVideoBody', { n: CREDIT_PRICES.video })}</p>
        </Confirm>
      )}
      {dialog?.kind === 'editPrompt' && (
        <Confirm title={dialog.field === 'prompt' ? t('run.editImagePrompt', { n: dialog.n }) : t('run.editVideoPrompt', { n: dialog.n })}
                 busy={busy === 'redo'} onClose={() => setDialog(null)} t={t}
                 confirm={t('run.saveAndRedo')}
                 cost={dialog.field === 'video_prompt' ? t('run.redoVideoCost', { n: CREDIT_PRICES.video }) : undefined}
                 onConfirm={() => act('redo', async () => {
                   await api.patchScene(dialog.scene.id, { [dialog.field]: dialog.text })
                   return api.redo(run.id, { target_type: 'scene', target_id: dialog.scene.id })
                 })}>
          <textarea rows={6} value={dialog.text} onChange={e => setDialog({ ...dialog, text: e.target.value })}
                    className={inputClass} style={inputStyle} aria-label={t('run.editPrompt')} />
          {dialog.field === 'video_prompt' && (
            <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('story.videoPromptHint')} · {t('story.chars', { n: dialog.text.length })}</p>
          )}
        </Confirm>
      )}
    </div>
  )
}

type T = (key: TranslationKey, params?: Record<string, string | number>) => string

function StatusMessage({ run, t, sceneNumber, names }: { run: RunStatus; t: T; sceneNumber: (id: string) => number; names: Map<string, string> }) {
  if (run.status === 'RUNNING') {
    return <p className="text-sm">{t('run.running', { stage: run.stage ? t(`run.stage.${run.stage}` as TranslationKey) : '' })}</p>
  }
  if (run.status === 'AWAITING_APPROVAL' && run.stage) {
    return <p className="text-sm">{t(`run.awaiting.${run.stage}` as TranslationKey)}</p>
  }
  if (run.status === 'PAUSED' && run.pause_reason) {
    return <Notice tone="warn">{t(`run.pause.${run.pause_reason}` as TranslationKey)}</Notice>
  }
  if (run.status === 'NEEDS_USER_ACTION') {
    if (run.needs_user_action.length === 0) {
      return <Notice tone="warn">{t('run.concatFailed', { error: run.error ?? run.status_detail ?? '' })}</Notice>
    }
    return (
      <Notice tone="warn">
        <div className="mb-1">{t('run.needsIntro')}</div>
        <ul className="list-disc pl-5">
          {run.needs_user_action.map(item => (
            <li key={item.id}>
              <b>{item.target_type === 'scene' ? t('run.sceneN', { n: sceneNumber(item.target_id) }) : names.get(item.target_id) ?? item.label}:</b>{' '}
              {t(reasonKey(item.error_code), { msg: item.error_message ?? '' })}
            </li>
          ))}
        </ul>
      </Notice>
    )
  }
  if (run.status === 'COMPLETED') return <p className="text-sm" style={{ color: 'var(--green)' }}>{t('run.state.COMPLETED')}</p>
  if (run.status === 'CANCELLED') return <p className="text-sm">{t('run.cancelled')}</p>
  if (run.status === 'FAILED') return <Notice tone="error">{t('run.failed', { error: run.error ?? '' })}</Notice>
  return null
}

function StateBadge({ state, label }: { state: RunStatus['status']; label: string }) {
  const color = state === 'COMPLETED' ? 'var(--green)' : state === 'NEEDS_USER_ACTION' || state === 'PAUSED' || state === 'FAILED'
    ? 'var(--yellow)' : state === 'AWAITING_APPROVAL' ? 'var(--accent)' : 'var(--muted)'
  return <span className="text-[11px] rounded-full px-2 py-0.5 border" style={{ color, borderColor: color }}>{label}</span>
}

function Card({ title, status, statusTone, children }: { title: string; status?: string; statusTone?: string; children: React.ReactNode }) {
  const color = statusTone === 'COMPLETED' || statusTone === 'SKIPPED' ? 'var(--green)'
    : statusTone === 'NEEDS_USER_ACTION' || statusTone === 'FAILED' ? 'var(--red)'
    : statusTone === 'SUBMITTED' ? 'var(--accent)' : 'var(--muted)'
  return (
    <div className="rounded-lg border p-2.5 flex flex-col gap-2" style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
      <div className="flex items-center gap-2 text-xs">
        <span className="font-semibold truncate">{title}</span>
        {status && <span className="ml-auto text-[10px] whitespace-nowrap" style={{ color }}>{status}</span>}
      </div>
      {children}
    </div>
  )
}

function Thumb({ src, alt, empty, square, vertical, onError }: {
  src: string | null | undefined; alt: string; empty: string; square?: boolean; vertical?: boolean; onError: () => void
}) {
  const aspect = square ? '1 / 1' : vertical ? '9 / 16' : '16 / 9'
  const [failed, setFailed] = useState<string | null>(null)  // the src that failed (a refreshed src is tried again)
  if (!src || failed === src) {
    return <div className="w-full rounded flex items-center justify-center text-[11px]"
                style={{ aspectRatio: aspect, background: 'var(--surface)', color: 'var(--muted)' }}>{empty}</div>
  }
  return <img src={src} alt={alt} onError={() => { setFailed(src); onError() }} loading="lazy" className="w-full rounded object-cover"
              style={{ aspectRatio: aspect, background: 'var(--surface)' }} />
}

function ReasonLine({ text }: { text: string }) {
  return <p className="text-[11px]" style={{ color: 'var(--yellow)' }}>{text}</p>
}

function SmallButton({ children, onClick, disabled, cost }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; cost?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
            className="rounded px-2 py-1 text-[11px] disabled:opacity-40 flex flex-col items-center"
            style={{ ...inputStyle }}>
      <span>{children}</span>
      {cost && <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{cost}</span>}
    </button>
  )
}

function Confirm({ title, children, confirm, cost, onConfirm, onClose, busy, danger, t }: {
  title: string; children?: React.ReactNode; confirm: string; cost?: string; onConfirm: () => void; onClose: () => void
  busy: boolean; danger?: boolean; t: T
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <div className="text-xs leading-relaxed flex flex-col gap-2">{children}</div>
      <div className="flex gap-2 justify-end">
        <ActionButton tone="secondary" onClick={onClose} disabled={busy}>{t('run.cancelAction')}</ActionButton>
        <ActionButton tone={danger ? 'danger' : 'primary'} onClick={onConfirm} disabled={busy} cost={cost}>
          {busy ? t('run.redoing') : confirm}
        </ActionButton>
      </div>
    </Modal>
  )
}

