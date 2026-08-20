# Workflow Modes

### [[2026-04-16]]

This file defines the canonical workflow modes for `design-orchestrator`.

If any launch template, role doc, or ad hoc prompt conflicts with this file, trust this file.

## 1. New Design Flow

Use when:

- the presentation is new
- the visual system is weak or absent
- a major redesign is required

Sequence:

1. intake
2. `portal-designer`
3. `presentation-art-director`
4. optional `presentation-generator-critic` loop
5. final design package

Expected artifacts:

- `Design Intake Brief`
- `Design Direction Artifact`
- `Art Direction Review`
- optional `Iteration Review`
- `Final Design Package`

## 2. Polish Existing Deck Flow

Use when:

- the deck already exists
- the goal is improvement without rebuilding from zero
- quality and consistency are more important than a new visual system

Sequence:

1. intake
2. `presentation-art-director`
3. `presentation-generator-critic`
4. `presentation-art-director` final pass
5. final design package

Expected artifacts:

- `Design Intake Brief`
- `Art Direction Review`
- `Iteration Review`
- final `Art Direction Review`
- `Final Design Package`

## 3. Iterative Revision Flow

Use when:

- an existing deck already exists
- comments or review notes already exist
- a versioned improvement loop is needed

Sequence:

1. intake
2. `presentation-generator-critic`
3. `presentation-art-director`
4. repeat if needed
5. final design package

Expected artifacts:

- `Design Intake Brief`
- `Iteration Review`
- `Art Direction Review`
- repeated review artifacts if another pass is required
- `Final Design Package`

## 4. Visual QA Only Flow

Use when:

- the design already exists
- only visual review is required
- no generation or redesign should happen

Sequence:

1. intake
2. `presentation-art-director`
3. QA verdict
4. final report or final design package

Expected artifacts:

- `Design Intake Brief`
- `Art Direction Review`
- `Final Design Package` or explicit QA verdict
