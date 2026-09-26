// Pure rules behind the run page (no React), so they can be tested on their own.
import type { TranslationKey } from '../i18n/translations'
import type { RunItem, RunStatus, StageName } from '../types/flow'
import { imageCredits, videoCredits } from './credits'

export interface SceneLike {
  id: string
  display_order: number
  parent_scene_id: string | null
  character_names: string[] | string | null
  [key: string]: unknown
}

/** character_names arrives as a list, or as a JSON string from older rows. */
export function sceneNames(scene: Pick<SceneLike, 'character_names'>): string[] {
  const raw = scene.character_names
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string' && raw.trim()) {
    try {
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed.map(String) : []
    } catch {
      return []
    }
  }
  return []
}

export function prefixOf(orientation: RunStatus['orientation']): 'vertical' | 'horizontal' {
  return orientation === 'HORIZONTAL' ? 'horizontal' : 'vertical'
}

/** Scenes a new reference for `entityName` would wipe: they use it and already have an image or a video. */
export function scenesWipedByNewReference(scenes: SceneLike[], entityName: string, prefix: 'vertical' | 'horizontal'): SceneLike[] {
  const name = entityName.toLowerCase()
  return scenes
    .filter(s => sceneNames(s).some(n => n.toLowerCase() === name))
    .filter(s => Boolean(s[`${prefix}_image_media_id`] || s[`${prefix}_video_media_id`]))
    .sort((a, b) => a.display_order - b.display_order)
}

/** Every CONTINUATION scene below `sceneId` (children, grandchildren, ...), in display order. */
export function descendants(scenes: SceneLike[], sceneId: string): SceneLike[] {
  const children = new Map<string, SceneLike[]>()
  for (const s of scenes) {
    if (s.parent_scene_id) children.set(s.parent_scene_id, [...(children.get(s.parent_scene_id) ?? []), s])
  }
  const out: SceneLike[] = []
  const seen = new Set<string>()
  const stack = [...(children.get(sceneId) ?? [])]
  while (stack.length) {
    const s = stack.pop()!
    if (seen.has(s.id)) continue
    seen.add(s.id)
    out.push(s)
    stack.push(...(children.get(s.id) ?? []))
  }
  return out.sort((a, b) => a.display_order - b.display_order)
}

const WAITING_FOR_YOU = ['AWAITING_APPROVAL', 'NEEDS_USER_ACTION', 'PAUSED']

/** The stage an item belongs to for redo: CONCAT redoes clips, so it is VIDEOS. */
export function redoStage(run: Pick<RunStatus, 'stage'>): StageName | null {
  return run.stage === 'CONCAT' ? 'VIDEOS' : run.stage
}

/** Mirrors the runner's redo rule (runner._redo): the item's stage is the current one, and the run
 *  waits for a person — or it is RUNNING and this item failed. References only before images. */
export function canRedo(run: Pick<RunStatus, 'status' | 'stage'>, itemStage: StageName, item: Pick<RunItem, 'status'> | undefined): boolean {
  if (!item || redoStage(run) !== itemStage) return false
  if (item.status === 'SUBMITTED') return false
  if (WAITING_FOR_YOU.includes(run.status)) return true
  return run.status === 'RUNNING' && (item.status === 'FAILED' || item.status === 'NEEDS_USER_ACTION')
}

/** What "Duyệt" starts next and its credits, for the label on the button. */
export function approvalCost(run: Pick<RunStatus, 'stage' | 'estimate_remaining'>): { next: StageName | null; credits: number } {
  const e = run.estimate_remaining
  if (run.stage === 'REFS') return { next: 'IMAGES', credits: imageCredits(e.images) }
  if (run.stage === 'IMAGES') return { next: 'VIDEOS', credits: videoCredits(e.videos) }
  if (run.stage === 'VIDEOS') return { next: 'CONCAT', credits: 0 }
  return { next: null, credits: 0 }
}

const KNOWN_REASONS = [
  'UNSAFE_GENERATION', 'MINOR_INPUT_IMAGE', 'MODEL_ACCESS_DENIED', 'UNSUPPORTED_ON_BATCH_API', 'MEDIA_NOT_FOUND',
  'FAILED_AFTER_RETRIES', 'MISSING_OUTPUT', 'MISSING_CLIP', 'UNUSUAL_ACTIVITY',
]

/** The Vietnamese explanation key for an item that needs a person. */
export function reasonKey(errorCode: string | null): TranslationKey {
  return (errorCode && KNOWN_REASONS.includes(errorCode) ? `run.reason.${errorCode}` : 'run.reason.other') as TranslationKey
}

/** The run is still going somewhere (worth live updates). */
export function isActive(status: RunStatus['status']): boolean {
  return status === 'RUNNING' || status === 'AWAITING_APPROVAL' || status === 'PAUSED' || status === 'NEEDS_USER_ACTION'
}
