import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/flowkit'
import { flowVi } from '../i18n/flowStrings'
import type { TranslationKey } from '../i18n/translations'
import { CREDIT_PRICES, credits, videoCredits } from './credits'
import { describeError } from './errors'
import { problemMessage } from './problems'

// The Vietnamese dictionary, with {params} filled in, like the app's t().
const t = (key: TranslationKey, params?: Record<string, string | number>) =>
  Object.entries(params ?? {}).reduce(
    (s, [k, v]) => s.replaceAll(`{${k}}`, String(v)),
    (flowVi as Record<string, string>)[key] ?? key,
  )

describe('credits', () => {
  it('prices images at 0 (observed) and videos at 10', () => {
    expect(CREDIT_PRICES).toEqual({ image: 0, video: 10 })
    expect(credits({ refs: 5, images: 3, videos: 3 })).toBe(30)
    expect(videoCredits(1)).toBe(10)
  })
})

describe('describeError', () => {
  it('explains Gemini failures by status', () => {
    expect(describeError(t, new ApiError(429, 'x'), 'gemini')).toBe(flowVi['err.geminiBusy'])
    expect(describeError(t, new ApiError(504, 'x'), 'gemini')).toBe(flowVi['err.geminiSlow'])
    expect(describeError(t, new ApiError(422, 'x'), 'gemini')).toBe(flowVi['err.geminiRefused'])
    expect(describeError(t, new ApiError(503, 'x'), 'gemini')).toBe(flowVi['err.geminiOff'])
  })

  it('explains Flow, network and conflicts', () => {
    expect(describeError(t, new ApiError(0, 'network'), 'flow')).toBe(flowVi['err.network'])
    expect(describeError(t, new ApiError(503, 'x'), 'flow')).toBe(flowVi['err.flowNotConnected'])
    expect(describeError(t, new ApiError(502, 'Flow API error: boom'), 'flow')).toContain('boom')
    expect(describeError(t, new ApiError(409, 'run is RUNNING'))).toContain('run is RUNNING')
  })
})

describe('problemMessage', () => {
  it('turns the backend checks into Vietnamese', () => {
    expect(problemMessage(t, 'cuts to another shot')).toContain('cut to')
    expect(problemMessage(t, '512 chars, max 400')).toBe('512 ký tự, tối đa 400')
    expect(problemMessage(t, '45% accented words')).toContain('45%')
    expect(problemMessage(t, 'String should have at least 1 character')).toBe('không được để trống')
    expect(problemMessage(t, 'String should have at most 100 characters')).toBe('quá dài (tối đa 100 ký tự)')
    expect(problemMessage(t, "Value error, scene 0 references undefined entities: ['Ai Đó']"))
      .toBe('dùng tên không có trong danh sách nhân vật: Ai Đó')
  })

  it('shows an unknown message as it came', () => {
    expect(problemMessage(t, 'something new')).toBe('something new')
  })
})

describe('flowVi', () => {
  it('has no empty translation', () => {
    expect(Object.entries(flowVi).filter(([, v]) => !v.trim())).toEqual([])
  })
})
