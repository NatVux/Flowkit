// Types for the "Tạo video mới" flow: story plans (agent/models/ai_content.py) and
// pipeline runs (agent/services/pipeline/runner.py status()). They mirror the Python shapes.

export interface Material {
  id: string
  name: string
  style_instruction: string
  scene_prefix?: string | null
  is_builtin?: boolean
}

export interface PlannedCharacter {
  name: string
  entity_type: 'character' | 'creature' | 'visual_asset' | 'generic_troop' | 'faction'
  description: string
  voice_description?: string | null
}

export interface PlannedLocation {
  name: string
  description: string
}

export interface PlannedScene {
  summary: string
  image_prompt: string
  video_prompt: string
  narration?: string | null
  character_names: string[]
  continues_previous: boolean
}

export interface StoryPlan {
  title: string
  logline: string
  story: string
  characters: PlannedCharacter[]
  locations: PlannedLocation[]
  scenes: PlannedScene[]
}

export interface StoryPlanInput {
  project_id: string
  video_id?: string | null
  brief: string
  scene_count: number
  language?: string | null
}

export interface AIGeneration {
  id: string
  operation: 'STORY_PLAN' | 'YOUTUBE_METADATA'
  status: 'GENERATED' | 'APPLIED'
  provider: string
  model: string
  project_id: string
  video_id: string | null
  request_id: string
  attempts: number
  input: StoryPlanInput | null
  output: StoryPlan
  created_at: string | null
  applied_at: string | null
}

export interface PlanProblem {
  loc: string
  msg: string
}

export type RunState =
  | 'DRAFT' | 'RUNNING' | 'AWAITING_APPROVAL' | 'PAUSED' | 'NEEDS_USER_ACTION'
  | 'COMPLETED' | 'CANCELLED' | 'FAILED'
export type StageName = 'REFS' | 'IMAGES' | 'VIDEOS' | 'CONCAT'
export type ItemState = 'PLANNED' | 'SUBMITTED' | 'COMPLETED' | 'SKIPPED' | 'NEEDS_USER_ACTION' | 'FAILED'

export interface Estimate {
  kind: 'minimum'
  refs: number
  images: number
  image_waves: number[]
  videos: number
  concat: number
  scenes_reset_by_new_refs: number
  total_generations: number
  concat_included: boolean
}

export interface RunItem {
  id: string
  target_type: 'character' | 'scene'
  target_id: string
  label: string
  wave: number | null
  status: ItemState
  request_type: string | null
  request_id: string | null
  request_status: string | null
  error_code: string | null
  error_message: string | null
  local_path: string | null
  local_url: string | null
  download_status: string | null
  redo_count: number
  requests_made: number
  generations_spent: number
}

export interface RunStatus {
  id: string
  project_id: string
  video_id: string
  orientation: 'VERTICAL' | 'HORIZONTAL'
  status: RunState
  stage: StageName | null
  wave: number | null
  pause_reason: 'UNUSUAL_ACTIVITY' | 'EXTENSION_DISCONNECTED' | null
  status_detail: string | null
  checkpoints: StageName[]
  options: { concat?: boolean }
  warnings: string[]
  estimate_at_last_checkpoint: Estimate
  estimate_remaining: Estimate
  generations_spent: number
  generations_spent_note: string
  needs_user_action: RunItem[]
  stages: Partial<Record<StageName, { total: number; done: number; items: RunItem[] }>>
  final_path: string | null
  final_url: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

/** GET /api/pipeline-runs rows: the run without items. */
export type RunSummary = Pick<RunStatus, 'id' | 'project_id' | 'video_id' | 'orientation' | 'status' | 'stage' |
  'pause_reason' | 'status_detail' | 'final_path' | 'created_at' | 'finished_at'> & { updated_at: string }

export interface HealthResponse {
  status: string
  extension_connected: boolean
  ws?: { flow_tab_available?: boolean }
}

export interface FlowStatus {
  connected: boolean
  flow_tab_available: boolean
  generation_throttle?: { cooldown_active: boolean; cooldown_remaining_s: number }
}

export interface AIStatus {
  enabled: boolean
  provider: string | null
  model: string | null
  configured: boolean
}
