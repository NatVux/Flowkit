// ─── Credit prices: edit here ────────────────────────────────────────────
// Flow credits per generation on the Google AI Plus plan with veo_3_1_i2v_lite.
// Images: the account's credit history lists video transactions only; about 11
// image generations charged nothing (observed 2026-09-26). Video: -10 per clip.
export const CREDIT_PRICES = {
  image: 0,   // reference images and scene images
  video: 10,  // one 8-second clip
} as const
export const IMAGE_PRICE_IS_OBSERVED = true
// Google grants 50 free credits a day, used before the monthly credits.
export const DAILY_FREE_CREDITS = 50
// ─────────────────────────────────────────────────────────────────────────

export interface GenerationCounts {
  refs: number
  images: number
  videos: number
}

export function credits(counts: GenerationCounts): number {
  return (counts.refs + counts.images) * CREDIT_PRICES.image + counts.videos * CREDIT_PRICES.video
}

export function videoCredits(videos: number): number {
  return videos * CREDIT_PRICES.video
}

export function imageCredits(images: number): number {
  return images * CREDIT_PRICES.image
}
