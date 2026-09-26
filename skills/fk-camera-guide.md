# Camera Guide — Cinematic Video Prompts (Veo 3)

Reference for writing video prompts optimized for Google Veo 3. Veo 3 generates native audio (dialogue, SFX, ambient) from text — no audio upload needed.

## Prompt Fundamentals

- **One continuous shot per 8-second clip** — never `then cut to`, no second shot, no timestamp beats
- **Length:** at most ~350 characters (2–4 sentences). A long multi-shot prompt (828 characters, "Then cut to") is what Google failed and refunded on 2026-09-26, while one-shot prompts of ~335 characters rendered (see `docs/PIPELINE_RUNNER.md`)
- **Style:** Natural prose — write like briefing a film director
- **Camera movement:** Always a **separate sentence** — never embed in action description
- **Audio and Negative:** leave the `Audio:` / `SFX:` / `Negative:` lines out — the Flow Kit worker appends an Audio line (from the project's `allow_music` / `allow_voice`) and a Negative line when the prompt has none

### 5-Component Structure

```
[Camera/Shot] + [Subject] + [Action] + [Setting] + [Style & Audio]
```

| Component | Role | Rule |
|-----------|------|------|
| Camera | Shot type, angle, movement | Write as **separate sentence** |
| Subject | Character, object | Detailed: age, clothing, hair, identifying features |
| Action | Motion, emotion, dialogue | One continuous action; at most one short line of dialogue |
| Setting | Location, time, weather | Background + environment + props |
| Style | Visual aesthetic + lighting | Audio/Negative lines are added by the worker |

**Critical rule — camera as separate sentence:**
- Wrong: `A woman walks down the street as the camera dollies in with warm lighting`
- Right: `A woman walks down the street. The camera slowly dollies in.`

---

## Shot Types

| Shot | Keywords | Shows |
|------|----------|-------|
| **Extreme wide (EWS)** | `extreme wide shot` | Vast landscape, subject tiny |
| **Wide (WS)** | `wide shot` | Full subject + environment |
| **Medium (MS)** | `medium shot` | Waist up |
| **Close-up (CU)** | `close-up` | Face or detail |
| **Extreme close-up (ECU)** | `extreme close-up` | Eyes, hands, texture |
| **Macro** | `macro shot` | Microscopic detail |

## Camera Movements

| Movement | Keywords | Effect |
|----------|----------|--------|
| **Dolly in/out** | `dolly in`, `dolly out` | Move camera toward/away |
| **Pan** | `pan left`, `pan right` | Horizontal rotation |
| **Tilt** | `tilt up`, `tilt down` | Vertical rotation |
| **Tracking** | `tracking shot` | Follow subject |
| **Crane** | `crane up`, `crane down` | Raise/lower camera |
| **Gimbal glide** | `gimbal glide` | Smooth stabilized movement |
| **Handheld** | `handheld` | Natural shake, raw feel |
| **Whip pan** | `whip pan` | Ultra-fast horizontal snap |
| **Arc shot** | `arc shot` | Orbit around subject |
| **POV** | `POV shot` | First-person perspective |
| **Static** | `locked-off static` | Camera completely fixed |
| **Rack focus** | `rack focus` | Shift focus between subjects |
| **180-degree arc** | `180-degree arc` | Half-orbit around subject |

**Rule:** Write camera movement as its own sentence. One movement per shot.

## Camera Angles

| Angle | Keywords | Effect |
|-------|----------|--------|
| **Eye level** | `eye level shot` | Neutral, natural |
| **Low angle** | `low angle shot, looking up` | Power, dominance |
| **High angle** | `high angle shot, looking down` | Vulnerability |
| **Bird's eye** | `top-down overhead shot` | Scale, patterns |
| **Dutch angle** | `tilted Dutch angle` | Tension, unease |
| **Over-the-shoulder** | `over-the-shoulder shot` | Dialogue connection |
| **Worm's eye** | `extreme low angle from ground` | Dramatic, towering |

## Lens & Focal Length

| Lens | Keywords | Effect |
|------|----------|--------|
| **18mm wide-angle** | `18mm wide-angle` | Exaggerated perspective |
| **35mm** | `35mm` | Classic film look |
| **50mm** | `50mm` | Natural eye perspective |
| **85mm telephoto** | `85mm telephoto` | Compressed perspective, beautiful bokeh |
| **Anamorphic** | `anamorphic lens` | Cinematic widescreen, signature lens flares |

## Depth of Field & Focus

| Technique | Keywords | Effect |
|-----------|----------|--------|
| **Shallow DOF** | `shallow depth of field, soft bokeh` | Subject isolation |
| **Deep focus** | `deep focus, everything sharp` | Full context |
| **Rack focus** | `rack focus from foreground to background` | Shift attention |
| **Tilt-shift** | `tilt-shift, miniature look` | Whimsical, toylike |

---

## Lighting

Lighting creates the biggest difference in output quality. **Always include lighting description.**

| Technique | Keywords | Mood |
|-----------|----------|------|
| **Golden hour** | `golden hour light` | Warmth, nostalgia, romance |
| **High-key** | `high-key lighting` | Bright, even, upbeat |
| **Low-key** | `low-key lighting` | Dark, high contrast, dramatic |
| **Noir** | `noir lighting` | Strong shadows, mysterious |
| **Backlight / rim light** | `backlit, rim light` | Separates subject from background |
| **Soft natural** | `soft natural light` | Gentle, even |
| **Motivated** | `motivated lighting` | Light source logical in scene |
| **Warm/cool practicals** | `warm practicals`, `neon-lit` | In-scene light sources (lamps, neon) |
| **Tungsten** | `tungsten` | Warm yellow incandescent |
| **Fluorescent** | `fluorescent` | Cool greenish office light |
| **Neon** | `neon-lit` | Colorful, vibrant |
| **Candlelight** | `candlelight` | Warm, flickering, intimate |
| **Volumetric** | `volumetric light, god rays` | Ethereal, sacred |
| **Chiaroscuro** | `chiaroscuro, strong contrast` | Drama, film noir |
| **Blue hour** | `blue hour, twilight` | Mystery, melancholy |

---

## Audio — Veo 3's Native Audio Generation

Veo 3 generates all audio from text. Three layers, each with its own label at the end of the prompt:

### Layer 1: Dialogue

```
Character says: "We need to leave now." (no subtitles)
```

**Dialogue rules:**
- Use `:` format to avoid subtitles: `Character says: dialogue text`
- Or use `""` but always add `(no subtitles)`
- Keep dialogue short — must fit in ~8 seconds
- Too long → character speaks unnaturally fast
- Describe voice quality: `in a deep, gravelly voice`, `whispering`, `shouting`
- Delivery verbs: `says`, `whispers`, `shouts`, `asks`, `replies`, `murmurs`, `exclaims`, `gasps`

### Layer 2: Sound Effects (SFX)

Specific, discrete sounds occurring in the scene:

```
SFX: the crack of a bat hitting a ball, crowd roaring
SFX: footsteps on gravel, a door creaking open
```

### Layer 3: Ambient / Background

Continuous background noise creating location realism:

```
Audio: distant city traffic, soft rain on windows
Audio: quiet hum of an office, keyboard typing
```

### Audio Placement

In Flow Kit, **leave these lines out**: the worker appends an Audio line and a Negative line when the prompt has none, and they would push the prompt past ~350 characters. For reference, Veo reads them at the **end** of the prompt, with clear labels:

```
[Visual description...]

Audio: soft café chatter, espresso machine hissing.
SFX: ceramic cup placed on saucer.
Music: faint lo-fi jazz in background.
```

---

## Style Keywords

| Category | Keywords |
|----------|----------|
| **Film genre** | `cinematic`, `documentary`, `film noir`, `horror`, `rom-com` |
| **Camera feel** | `handheld`, `steadicam`, `found footage`, `security camera` |
| **Color grade** | `desaturated`, `teal and orange`, `warm vintage`, `cool blue` |
| **Film stock** | `35mm grain`, `16mm`, `Super 8`, `IMAX` |
| **Art style** | `anime`, `stop-motion`, `LEGO bricks`, `8-bit pixel art`, `watercolor` |
| **Era** | `1970s`, `retro VHS`, `Y2K aesthetic`, `futuristic` |
| **Override** | `In the style of...` → overrides default aesthetic |

## Speed & Time

| Technique | Keywords | Effect |
|-----------|----------|--------|
| **Slow motion** | `slow motion, time slows` | Dramatic emphasis |
| **Timelapse** | `timelapse, time passing` | Passage of time |
| **Speed ramp** | `speed ramp` | Dynamic rhythm change |

---

## One Continuous Shot

Each 8-second clip is **one shot**. Do not write `then cut to`, `finally`, a reverse shot or timestamp beats — build the emotion with a camera move instead: a slow dolly in toward a face, a crane down onto the subject, a pull back to reveal the setting.

```
Medium shot of a woman under a red umbrella at a rainy crosswalk at night,
checking her phone, her worry turning to relief. The camera slowly dollies
in. Neon signs reflecting on wet asphalt, teal and orange grade.
```

Rules:

1. One subject action from start to end; the camera may move, but never cuts
2. At most ~350 characters, lighting described, camera move as its own sentence
3. At most one short line of dialogue: `Name says: "..." (no subtitles)`
4. Several shots of the same moment = several scenes (use `/fk-insert-scene`)

---

## Character Consistency

Since we use reference images via `imageInputs`, **don't describe character appearance** in prompts — write ACTION only. The reference image handles visual consistency.

However, for Veo 3 specifics:
- Save establishing shot as Element → reference for subsequent shots
- First-and-last-frame (start + end frame) is not available on the current Flow batch API; every scene renders as its own clip
- `voice_description` on characters (max ~30 words) — auto-appended to video prompts by the worker

---

## Negative Prompt

Veo 3 supports negative prompts — list keywords to exclude (no instructive language):

- Wrong: `no walls, don't show cars`
- Right: `Negative: subtitles, watermark, text overlay`

**Standard negative** (the worker appends one when the prompt has no `Negative:` line — write your own only to add a situational term):
```
Negative: subtitles, captions, watermark, text on screen, logo, blurry faces, distorted hands
```

**Situational additions:**

| Problem | Add to negative |
|---------|----------------|
| Subtitles appearing | `subtitles, captions` |
| Text overlay | `text overlays, watermarks` |
| Laugh track | `studio audience laughter` |
| Unwanted music | `background music` |
| Over-cinematic | `editorial narration` |

---

## Prompt Template

```
[Shot type] of [subject], [one continuous action/emotion], [setting + time of day].
[Camera movement as separate sentence]. [Lighting + color].
[Optional, at most one line]: Character says: "..." (no subtitles)
```

---

## Examples

### Documentary/Military Scene
```
Medium shot of a soldier sprinting across a barren autumn road toward a
concrete barrier, military jeep smoking behind him. The camera tracks him
handheld. Overcast grey sky, cold diffused light, breath visible in the air.
```

### Emotional Discovery Scene
```
Over-the-shoulder shot of Luna kneeling at a chocolate river, dipping a paw
in. Luna gasps: "What is this place?" (no subtitles) The camera slowly cranes
up. Soft golden hour light, cotton candy clouds towering above.
```

### Action Scene
```
Low angle shot of a hero charging across a castle bridge at dawn, sword
raised, light bursting from the blade. The camera tracks alongside. Warm
golden hour, long shadows on ancient stone, wind whipping his cloak.
```

---

## Quality Checklist

Before submitting any video prompt, verify:

- [ ] One continuous shot — no `then cut to`, no timestamp beats
- [ ] At most ~350 characters, 2–4 sentences
- [ ] Subject described with detail (age, clothing, hair, features) — unless ref image handles it
- [ ] Camera movement written as **separate sentence**
- [ ] Lighting/color temperature described
- [ ] No Audio / SFX / Negative lines (the worker appends them)
- [ ] At most one short line of dialogue (fits in ~8s), with `(no subtitles)`
- [ ] No abstract words — everything is visual/audible and specific
- [ ] Reference characters described by action only, not appearance

## Common Mistakes

| Wrong | Right |
|-------|-------|
| Prompt too generic | Specific per component, still under ~350 characters |
| Several shots joined with `then cut to` | One continuous shot; move the camera instead |
| Camera movement embedded in action sentence | Camera movement = separate sentence |
| Dialogue too long for 8s | Keep dialogue short, fits in clip duration |
| Using `"quotes"` for dialogue → subtitles appear | Use `:` format or add `(no subtitles)` |
| Writing `Audio:` / `SFX:` / `Negative:` lines | Leave them out — the worker appends them |
| Using `no`, `don't` in negative prompt | List keywords: `subtitles, watermark` |
| Long prompt (800+ characters) | At most ~350 characters |
| Missing lighting description | Always include lighting + color temperature |
| Vague words like `"cinematic"` alone | Specify: `shallow DOF + golden hour + dolly in` |
| `"Camera zooms"` — too vague | `The camera slowly dollies in.` (separate sentence) |
| Negative written as instructions (`no text`) | Keywords only, and only to add a situational term |
