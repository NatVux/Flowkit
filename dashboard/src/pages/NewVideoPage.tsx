import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { api } from '../api/flowkit'
import { useSystemStatus } from '../api/SystemStatus'
import { useTranslation } from '../i18n/useTranslation'
import { describeError } from '../lib/errors'
import type { Material } from '../types/flow'
import { ActionButton, Label, Notice, PageTitle, Panel, Toggle } from '../components/flow/ui'
import { inputClass, inputStyle } from '../lib/styles'

const STORY_LANGUAGES = [
  { code: 'vi', label: 'Tiếng Việt' },
  { code: 'en', label: 'English' },
]

type Step = 'project' | 'video' | 'story'

/** "Viết truyện": create the project (on Flow) and the video, then ask Gemini for the story. */
export default function NewVideoPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const system = useSystemStatus()

  const [materials, setMaterials] = useState<Material[]>([])
  const [idea, setIdea] = useState('')
  const [language, setLanguage] = useState('vi')
  const [material, setMaterial] = useState('3d_pixar')
  const [orientation, setOrientation] = useState<'VERTICAL' | 'HORIZONTAL'>('VERTICAL')
  const [sceneCount, setSceneCount] = useState(3)
  const [music, setMusic] = useState(false)
  const [voice, setVoice] = useState(false)

  const [step, setStep] = useState<Step | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Kept after a failure so "Thử lại" does not create a second Flow project.
  const [created, setCreated] = useState<{ projectId: string; videoId?: string } | null>(null)

  useEffect(() => {
    api.materials().then(ms => {
      setMaterials(ms)
      if (ms.length && !ms.some(m => m.id === '3d_pixar')) setMaterial(ms[0].id)
    }).catch(() => setMaterials([]))
  }, [])

  const ready = system.server && system.flowReady && system.gemini
  const busy = step !== null

  async function write(e?: FormEvent) {
    e?.preventDefault()
    if (!idea.trim()) { setError(t('new.ideaRequired')); return }
    setError(null)
    let projectId = created?.projectId
    let videoId = created?.videoId
    const at: { step: Step } = { step: 'project' }
    const go = (next: Step) => { at.step = next; setStep(next) }
    try {
      if (!projectId) {
        go('project')
        const name = idea.trim().split('\n')[0].slice(0, 60)
        const project = await api.createProject({ name, language, material, allow_music: music, allow_voice: voice })
        projectId = project.id
        setCreated({ projectId })
      }
      if (!videoId) {
        go('video')
        const video = await api.createVideo({ project_id: projectId, title: '', orientation })
        videoId = video.id
        setCreated({ projectId, videoId })
      }
      go('story')
      const gen = await api.storyPlan({
        project_id: projectId, video_id: videoId, brief: idea.trim(), scene_count: sceneCount, language,
      })
      navigate(`/truyen/${gen.id}`)
    } catch (err) {
      const source = at.step === 'story' ? 'gemini' : 'flow'
      setError(describeError(t, err, source))
      setStep(null)
    }
  }

  return (
    <div className="max-w-3xl">
      <PageTitle title={t('new.title')} subtitle={t('new.subtitle')} />
      <form onSubmit={write} className="flex flex-col gap-4">
        <Panel>
          <Label htmlFor="idea">{t('new.idea')}</Label>
          <textarea id="idea" rows={4} value={idea} onChange={e => setIdea(e.target.value)} disabled={busy}
                    placeholder={t('new.ideaPlaceholder')} className={inputClass} style={inputStyle} maxLength={4000} />
        </Panel>

        <Panel className="grid gap-4 sm:grid-cols-3">
          <div>
            <Label htmlFor="language">{t('new.language')}</Label>
            <select id="language" value={language} onChange={e => setLanguage(e.target.value)} disabled={busy || !!created}
                    className={inputClass} style={inputStyle}>
              {STORY_LANGUAGES.map(l => <option key={l.code} value={l.code}>{l.label}</option>)}
            </select>
          </div>
          <div>
            <Label>{t('new.orientation')}</Label>
            <div className="flex gap-2">
              {(['VERTICAL', 'HORIZONTAL'] as const).map(o => (
                <button key={o} type="button" disabled={busy || !!created?.videoId} onClick={() => setOrientation(o)}
                        className="flex-1 rounded-md px-2 py-2 text-xs"
                        style={{ ...inputStyle, borderColor: orientation === o ? 'var(--accent)' : 'var(--border)',
                                 color: orientation === o ? 'var(--text)' : 'var(--muted)' }}>
                  {o === 'VERTICAL' ? t('new.vertical') : t('new.horizontal')}
                </button>
              ))}
            </div>
          </div>
          <div>
            <Label htmlFor="scenes" hint={t('new.sceneCountHint')}>{t('new.sceneCount')}</Label>
            <input id="scenes" type="number" min={1} max={10} value={sceneCount} disabled={busy}
                   onChange={e => setSceneCount(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
                   className={inputClass} style={inputStyle} />
          </div>
        </Panel>

        <Panel>
          <Label>{t('new.style')}</Label>
          <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))' }}>
            {materials.map(m => (
              <button key={m.id} type="button" disabled={busy || !!created} onClick={() => setMaterial(m.id)}
                      title={m.style_instruction}
                      className="rounded-md px-3 py-2 text-left text-xs"
                      style={{ ...inputStyle, borderColor: material === m.id ? 'var(--accent)' : 'var(--border)',
                               color: material === m.id ? 'var(--text)' : 'var(--muted)' }}>
                {m.name}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-6 mt-4">
            <Toggle id="music" checked={music} onChange={setMusic} label={t('new.music')} />
            <Toggle id="voice" checked={voice} onChange={setVoice} label={t('new.voice')} />
          </div>
        </Panel>

        {error && (
          <Notice tone="error">
            {error}
            {created && <div className="mt-1" style={{ color: 'var(--muted)' }}>{t('new.draftKept')}</div>}
          </Notice>
        )}
        {!ready && system.loaded && <Notice tone="warn">{t('new.blocked')}</Notice>}

        <div className="flex items-center gap-3">
          <ActionButton type="submit" disabled={busy || !ready}>
            {busy ? t('new.writing') : created ? t('new.retry') : t('new.write')}
          </ActionButton>
          {step && (
            <span className="flex items-center gap-2 text-xs" style={{ color: 'var(--muted)' }}>
              <Loader2 size={14} className="animate-spin" />
              {t(`new.step.${step}`)}
            </span>
          )}
        </div>
      </form>
    </div>
  )
}
