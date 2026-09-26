// Typed calls for the "Tạo video mới" flow. Every one goes to an existing backend
// route; nothing here calls Flow for a generation except start/approve/redo on a run.
import type { Project, Scene, Character, Video } from '../types'
import type {
  AIGeneration, AIStatus, FlowStatus, HealthResponse, Material, RunStatus, RunSummary, StoryPlan, StoryPlanInput,
} from '../types/flow'

/** A failed call. `details` is the backend's error.details (e.g. {problems: [...]}). */
export class ApiError extends Error {
  status: number
  code: string | null
  details: unknown

  constructor(status: number, message: string, code: string | null = null, details: unknown = null) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  } catch {
    throw new ApiError(0, 'network')  // server not running
  }
  const text = await res.text()
  let body: unknown = null
  try { body = text ? JSON.parse(text) : null } catch { body = text }
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new ApiError(res.status, err?.message ?? (typeof detail === 'string' ? detail : res.statusText),
      err?.code ?? null, err?.details ?? detail ?? null)
  }
  return body as T
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const patch = <T>(path: string, body: unknown) => call<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
const put = <T>(path: string, body: unknown) => call<T>(path, { method: 'PUT', body: JSON.stringify(body) })
const del = <T>(path: string) => call<T>(path, { method: 'DELETE' })

export const api = {
  // status bar
  health: () => call<HealthResponse>('/health'),
  flowStatus: () => call<FlowStatus>('/api/flow/status'),
  aiStatus: () => call<AIStatus>('/api/ai/status'),

  // new video
  materials: () => call<Material[]>('/api/materials'),
  /** Creates the project on Flow too (no credit; needs the extension and a Flow tab). */
  createProject: (body: { name: string; language: string; material: string; allow_music: boolean; allow_voice: boolean }) =>
    post<Project>('/api/projects', body),
  /** title '' lets apply fill in the story's title. */
  createVideo: (body: { project_id: string; title: string; orientation: 'VERTICAL' | 'HORIZONTAL' }) =>
    post<Video>('/api/videos', body),
  /** Calls Gemini. Only on "Viết truyện" / "Viết lại". */
  storyPlan: (body: StoryPlanInput) => post<AIGeneration>('/api/ai/story-plan', body),
  generation: (id: string) => call<AIGeneration>(`/api/ai/generations/${id}`),
  generations: (projectId: string) => call<AIGeneration[]>(`/api/ai/generations?project_id=${projectId}`),
  saveGeneration: (id: string, plan: StoryPlan) => put<AIGeneration>(`/api/ai/generations/${id}`, plan),
  applyGeneration: (id: string, videoId: string) =>
    post<{ scenes_created: string[] }>(`/api/ai/generations/${id}/apply`, { video_id: videoId }),

  // projects, videos, scenes
  projects: () => call<Project[]>('/api/projects'),
  project: (id: string) => call<Project>(`/api/projects/${id}`),
  deleteProject: (id: string) => del<{ ok: boolean }>(`/api/projects/${id}`),
  videos: (projectId: string) => call<Video[]>(`/api/videos?project_id=${projectId}`),
  video: (id: string) => call<Video>(`/api/videos/${id}`),
  patchVideo: (id: string, body: Partial<Pick<Video, 'title' | 'description'>>) => patch<Video>(`/api/videos/${id}`, body),
  scenes: (videoId: string) => call<Scene[]>(`/api/scenes?video_id=${videoId}`),
  patchScene: (id: string, body: { prompt?: string; video_prompt?: string }) => patch<Scene>(`/api/scenes/${id}`, body),
  characters: (projectId: string) => call<Character[]>(`/api/projects/${projectId}/characters`),
  /** Re-signs expired media URLs (a Flow read, no credit). */
  refreshUrls: (projectId: string) => post<unknown>(`/api/flow/refresh-urls/${projectId}`),

  // pipeline runs
  createRun: (videoId: string) => post<RunStatus>('/api/pipeline-runs', { video_id: videoId }),
  runs: (q: { projectId?: string; videoId?: string }) => {
    const params = new URLSearchParams()
    if (q.projectId) params.set('project_id', q.projectId)
    if (q.videoId) params.set('video_id', q.videoId)
    return call<RunSummary[]>(`/api/pipeline-runs?${params}`)
  },
  run: (id: string) => call<RunStatus>(`/api/pipeline-runs/${id}`),
  startRun: (id: string) => post<RunStatus>(`/api/pipeline-runs/${id}/start`),
  approveRun: (id: string) => post<RunStatus>(`/api/pipeline-runs/${id}/approve`),
  resumeRun: (id: string) => post<RunStatus>(`/api/pipeline-runs/${id}/resume`),
  cancelRun: (id: string) => post<RunStatus>(`/api/pipeline-runs/${id}/cancel`),
  redo: (id: string, body: { target_type: 'character' | 'scene'; target_id: string; include_descendants?: boolean; confirm_invalidates?: boolean }) =>
    post<RunStatus & { redo: { estimated_generations: number; stale_descendants: string[] } }>(`/api/pipeline-runs/${id}/redo`, body),

  // local files
  openFolder: (path: string) => post<{ ok: boolean }>('/api/system/open-folder', { path }),
}
