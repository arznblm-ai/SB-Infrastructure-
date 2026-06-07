# Design Direction

Reference: [i.com](https://i-com-agency.ru/)
Checked: 2026-03-30

## Goal

Turn the current cabinet from a soft demo dashboard into a premium agency interface with a stronger editorial and tech feel.

## What We Borrow From The Reference

- High-contrast, confident presentation instead of a "friendly SaaS" mood
- Large, deliberate typography that carries the page
- Clean modular composition with strong section rhythm
- Premium restraint: fewer decorative tricks, more hierarchy and spacing
- Brand feeling built through composition and tone, not only through color

## What We Should Avoid

- Pastel-purple startup aesthetics
- Excessively rounded "cute" UI
- Emoji-driven production interface
- Too many accent colors fighting each other
- Dense cards with weak hierarchy

## Our Direction For The Cabinet

### 1. Mood

The cabinet should feel like an internal client portal for a high-end creative and AI production studio:

- calm
- confident
- premium
- structured
- modern, but not trendy for the sake of trend

### 2. Typography

Recommended pairing for implementation:

- Display: `Sora`
- Interface/Text: `IBM Plex Sans`

Why:

- `Sora` gives us geometric, modern headlines with a sharper presence than `Inter`
- `IBM Plex Sans` keeps UI text clean, technical, and readable

If we later license the exact display font used in the reference direction, we can swap only the display token and keep the system intact.

### 3. Color System

Base palette:

- `Ink` `#111418`
- `Graphite` `#2A3138`
- `Paper` `#FBF8F2`
- `Canvas` `#F3EEE4`
- `Steel` `#7C8591`
- `Line` `rgba(17, 20, 24, 0.10)`
- `Signal Blue` `#1463FF`
- `Success Green` `#16794D`
- `Warm Amber` `#B7791F`

Usage principles:

- most of the interface should live in paper, ink, graphite, and steel
- accent blue should be intentional and rare
- status colors should feel operational, not playful

### 4. Layout

- Wider breathing room around main content
- Larger section headers with tighter supporting text
- Cards should feel like editorial panels, not default dashboard widgets
- Right-side info panels should be compact, dense, and aligned to a baseline grid
- Timeline and approval screens should look more architectural and less toy-like

### 5. Components

Topbar:

- slimmer and more precise
- logo with stronger typographic presence
- steps reworked into cleaner project stages

Forms:

- larger labels
- quieter fields
- stronger focus states
- less decorative chrome

Cards:

- sharper silhouette
- more disciplined spacing
- less tinted backgrounds

Status blocks:

- use monochrome surfaces plus one accent line or marker
- reserve filled color for true emphasis

Player/review area:

- dark presentation stage
- real media first
- comments panel should feel like a review workspace, not a chat toy

### 6. Media Direction

- Use real product stills or cropped brand imagery whenever possible
- Prefer editorial crops over generic centered thumbnails
- Video poster frames should feel cinematic and high-contrast
- Abstract gradient blobs can stay, but should become more restrained and more premium

### 7. Motion

- Soft reveal on screen change
- Controlled hover elevation
- Progress and status animation should be subtle
- Avoid constant motion that makes the interface feel noisy

## Practical Translation To The Current Screens

Screen 1:

- make the brief screen feel like a client intake studio sheet
- uploads should look like curated asset dropzones, not demo placeholders

Screen 2:

- convert the production state into a more premium operations dashboard
- timeline should become cleaner, with better typography and less color clutter

Screen 3:

- make the review stage the hero moment
- the video area should dominate visually
- comments and approval actions should feel decisive and professional

## Implementation Order

1. Move the current single-file layout into separate HTML, CSS, and JS files.
2. Introduce shared design tokens and typography.
3. Replace the existing color and spacing system.
4. Restyle the three screens without changing their information architecture yet.
5. Swap mock visual blocks for real images/video assets when available.
