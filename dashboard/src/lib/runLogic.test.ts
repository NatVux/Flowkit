import { describe, expect, it } from 'vitest'
import {
  approvalCost, canRedo, descendants, isActive, reasonKey, redoStage, sceneNames, scenesWipedByNewReference,
  type SceneLike,
} from './runLogic'
import type { Estimate, RunStatus } from '../types/flow'

const scene = (id: string, order: number, names: string[] | string | null, extra: Partial<SceneLike> = {}): SceneLike =>
  ({ id, display_order: order, parent_scene_id: null, character_names: names, ...extra })

const estimate = (e: Partial<Estimate>): Estimate => ({
  kind: 'minimum', refs: 0, images: 0, image_waves: [], videos: 0, concat: 0,
  scenes_reset_by_new_refs: 0, total_generations: 0, concat_included: true, ...e,
})

describe('sceneNames', () => {
  it('reads a list, a JSON string, or nothing', () => {
    expect(sceneNames({ character_names: ['Minh', 'Lumi'] })).toEqual(['Minh', 'Lumi'])
    expect(sceneNames({ character_names: '["Minh"]' })).toEqual(['Minh'])
    expect(sceneNames({ character_names: 'not json' })).toEqual([])
    expect(sceneNames({ character_names: null })).toEqual([])
  })
})

describe('scenesWipedByNewReference', () => {
  const scenes = [
    scene('s2', 2, ['Minh'], { vertical_image_media_id: 'img2' }),
    scene('s0', 0, ['Minh', 'Old Forest'], { vertical_video_media_id: 'vid0' }),
    scene('s1', 1, ['Lumi'], { vertical_image_media_id: 'img1' }),
    scene('s3', 3, '["minh"]', {}),                                 // uses Minh but has nothing to wipe
    scene('s4', 4, ['Minh'], { horizontal_image_media_id: 'h4' }),  // other orientation
  ]

  it('lists the scenes that use the entity and already have an image or a video, in order', () => {
    expect(scenesWipedByNewReference(scenes, 'Minh', 'vertical').map(s => s.id)).toEqual(['s0', 's2'])
  })

  it('is empty when no scene of that orientation has anything yet', () => {
    expect(scenesWipedByNewReference(scenes, 'Old Forest', 'horizontal')).toEqual([])
  })
})

describe('descendants', () => {
  it('follows continuation chains to the end, in order', () => {
    const scenes = [
      scene('root', 0, []), scene('a', 1, [], { parent_scene_id: 'root' }), scene('b', 2, [], { parent_scene_id: 'a' }),
      scene('other', 3, []), scene('c', 4, [], { parent_scene_id: 'root' }),
    ]
    expect(descendants(scenes, 'root').map(s => s.id)).toEqual(['a', 'b', 'c'])
    expect(descendants(scenes, 'b')).toEqual([])
  })
})

describe('canRedo (mirrors runner._redo)', () => {
  const run = (status: RunStatus['status'], stage: RunStatus['stage']) => ({ status, stage })

  it('allows the current stage while the run waits for a person', () => {
    for (const status of ['AWAITING_APPROVAL', 'NEEDS_USER_ACTION', 'PAUSED'] as const) {
      expect(canRedo(run(status, 'IMAGES'), 'IMAGES', { status: 'COMPLETED' })).toBe(true)
    }
  })

  it('refuses another stage, a missing item, and an item still in flight', () => {
    expect(canRedo(run('AWAITING_APPROVAL', 'IMAGES'), 'REFS', { status: 'COMPLETED' })).toBe(false)
    expect(canRedo(run('AWAITING_APPROVAL', 'IMAGES'), 'IMAGES', undefined)).toBe(false)
    expect(canRedo(run('NEEDS_USER_ACTION', 'IMAGES'), 'IMAGES', { status: 'SUBMITTED' })).toBe(false)
  })

  it('allows only a failed item while RUNNING', () => {
    expect(canRedo(run('RUNNING', 'VIDEOS'), 'VIDEOS', { status: 'NEEDS_USER_ACTION' })).toBe(true)
    expect(canRedo(run('RUNNING', 'VIDEOS'), 'VIDEOS', { status: 'FAILED' })).toBe(true)
    expect(canRedo(run('RUNNING', 'VIDEOS'), 'VIDEOS', { status: 'COMPLETED' })).toBe(false)
  })

  it('treats CONCAT as the videos stage, and nothing is redone once finished', () => {
    expect(redoStage({ stage: 'CONCAT' })).toBe('VIDEOS')
    expect(canRedo(run('NEEDS_USER_ACTION', 'CONCAT'), 'VIDEOS', { status: 'COMPLETED' })).toBe(true)
    expect(canRedo(run('COMPLETED', 'CONCAT'), 'VIDEOS', { status: 'COMPLETED' })).toBe(false)
    expect(canRedo(run('CANCELLED', 'IMAGES'), 'IMAGES', { status: 'COMPLETED' })).toBe(false)
  })
})

describe('approvalCost', () => {
  it('prices the next stage: images are free, videos 10 each, concat free', () => {
    const e = estimate({ images: 3, videos: 3 })
    expect(approvalCost({ stage: 'REFS', estimate_remaining: e })).toEqual({ next: 'IMAGES', credits: 0 })
    expect(approvalCost({ stage: 'IMAGES', estimate_remaining: e })).toEqual({ next: 'VIDEOS', credits: 30 })
    expect(approvalCost({ stage: 'VIDEOS', estimate_remaining: e })).toEqual({ next: 'CONCAT', credits: 0 })
    expect(approvalCost({ stage: null, estimate_remaining: e })).toEqual({ next: null, credits: 0 })
  })
})

describe('reasonKey / isActive', () => {
  it('maps known codes and falls back for the rest', () => {
    expect(reasonKey('MODEL_ACCESS_DENIED')).toBe('run.reason.MODEL_ACCESS_DENIED')
    expect(reasonKey('MINOR_INPUT_IMAGE')).toBe('run.reason.MINOR_INPUT_IMAGE')
    expect(reasonKey('SOMETHING_NEW')).toBe('run.reason.other')
    expect(reasonKey(null)).toBe('run.reason.other')
  })

  it('is active only while the run can still move', () => {
    expect(isActive('RUNNING')).toBe(true)
    expect(isActive('PAUSED')).toBe(true)
    expect(isActive('DRAFT')).toBe(false)
    expect(isActive('COMPLETED')).toBe(false)
  })
})
