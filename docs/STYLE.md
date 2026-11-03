# Visual style: Pocket Field Notes

Commonplace looks like a small printed library: warm paper, brown-black ink, one red accent, serif type. This guide covers
social images, infographics, charts and illustrations. The app (`android/app/src/main/java/app/commonplace/ui/Theme.kt`)
and the site (`site/index.html`) are the source of truth: when this guide and their tokens differ, the code wins.

Creative direction: a curious little library that lives in your pocket. Warm like an illustrated textbook, clear like a
technical diagram. Intelligent, approachable, a little whimsical, trustworthy, never corporate.

## Colors

Light tokens (app light scheme and site `:root`). Use 3–4 of them in one image.

| Token | Hex | Use |
|---|---|---|
| Paper | `#F5EFE3` | Background |
| Paper 2 | `#EAE2D2` | Cards, panels, tiles |
| Paper 3 | `#E1D7C4` | Inactive tracks, pressed cards |
| Rule | `#D6CAB6` | Hairlines, grid lines, empty progress |
| Ink | `#2A2420` | Headlines, body text, outlines |
| Ink 2 | `#5E544A` | Secondary text, captions, neutral bars |
| Rubric | `#8B2E1F` | The one accent: citation numbers, the bar or value that matters |
| Mark | `#F1DB98` | Highlighter: status chips, highlighted passages |
| Slate | `#2F4F5E` | Devices and technical parts; a second data series |
| Slate light | `#D3E1E6` | Fills behind device drawings |
| Good | `#3F6B3A` | "Passed" or "verified", always with a word or icon |
| Unverified | `#A3561B` | "Unverified", always with a word or icon |

Brand mark (`docs/images/logo.svg`): green `#1F3A34`, cream `#F3E9D2`, gold `#D9A441`. Use these colors for the logo only.

Illustrations may also use Sage `#8DA99B` for library objects (books, packs). Sage is not in the app; keep it out of charts and text.

Dark images (night reading): paper `#1A1714`, paper 2 `#26211D`, ink `#E9DFCC`, ink 2 `#BFB3A0`, rubric `#E39A84`,
mark `#5A4818`, rule `#3E3730`, slate `#A7C7D4`.

## Type

- Literata only, as in the app and the site. Headlines: Literata SemiBold at a display size. Text: Literata Regular.
  Numbers in Literata too. The font files are `android/app/src/main/res/font/literata*.ttf` and
  `site/fonts/literata*.woff2`.
- Headline 64–88 px on a 1600 px canvas, body 22–26 px, captions 18–20 px. Nothing below 16 px.
- Sentence case. No all-caps labels.

## Social images and infographics

The reference image is "On the workbench" (1600 × 1000).

- Canvas 1600 × 1000 or 1600 × 900, paper background, 88 px side margins.
- Headline at the top left, large. A short explanation at the top right or under the headline.
- One dominant element: a table of rows, a 3–5 step diagram, or one chart.
- Rows separated by 1 px rule lines. No boxes around everything.
- Status as a mark-colored chip with the word in ink (Exploring, Building, Testing, Released).
- Footer: the logo square and "Commonplace" at the left, a one-line caption in ink 2 in the middle, `@askcommonplace` at the right.
- A paper grain is optional and never behind small text.
- Each image says where its numbers come from: device, number of questions, date, in the caption or a footnote.

## Charts

- Bars start at zero. Thin marks, small radii (4 px), labels on the bars, not in a legend, when there are few bars.
- Neutral bars in ink 2 or rule, the bar that the image is about in rubric. A second series in slate.
- One axis per chart. No rainbow scales, gradients or 3D.
- Status colors only with a word next to them.
- Write the measured value exactly as `docs/BENCHMARKS.md` gives it. Mixed builds or dates need a footnote.

## Illustrations

- Flat 2D editorial vector with a light cut-paper or screen-print feel. Simple, slightly imperfect shapes, softened corners.
- Outlines in ink, 2.5–3 px on a 1024 px canvas. Flat fills, at most one shadow shape. No realistic lighting.
- One focal point, few objects, much empty space.
- Motifs: numbered source tabs `[1]` `[2]` in rubric, stacked index cards and books, highlighted passages in mark,
  phone outlines with a small diagram inside, dotted paths from evidence to answer, small numbered annotations.
- Never: glowing AI brains, robots, circuit boards, 3D renders, glossy gradients, glassmorphism, neon, anime-style
  characters, dense unreadable charts, or anything that suggests the app sends questions to the cloud.
- Simplify the real architecture. Do not invent network connections or features.

## Mascot: Pip, the pocket archivist (proposed)

A tiny living library index card: a rounded paper body with a folded upper-right corner, a small red numbered citation
tab, two dot eyes, short eyebrows, mitten arms and tiny feet. Helpful, curious, a little nerdy, never smug. Poses:
examining a passage with a magnifier, raising a verified source card, looking through a stack of books, reading beside
a crossed-out Wi-Fi icon, pointing to a missing source instead of inventing an answer. Pip must read as a one-color
silhouette at small sizes. No approved Pip drawing exists yet; until one does, keep Pip out of published images.

## Image-generation prompt

For an image model, fill in the subject:

> Flat 2D editorial vector illustration for Commonplace, an offline research app that answers from a library on the
> phone and cites its sources. Style "Pocket Field Notes": vintage field-guide charm with clean modern technology. Warm
> paper background #F5EFE3, brown-black ink outlines #2A2420, accents of red #8B2E1F, highlighter yellow #F1DB98 and
> slate #2F4F5E; sage #8DA99B for books. Simple rounded geometry, even medium outlines, flat fills, very light paper
> grain, generous empty space, playful but intelligent. Subject: [SCENE]. No realistic rendering, 3D, gradients,
> glowing AI imagery, robots or cloud symbols.

Check every generated image for wrong text, invented features and cloud imagery before use.
