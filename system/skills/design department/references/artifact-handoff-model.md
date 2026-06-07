# Artifact Handoff Model

Design Department is artifact-driven.

Roles should not rely on vague prompt memory when a concrete handoff artifact can be passed forward.

## 1. Design Intake Brief

Created by: `design-orchestrator`

Required fields:

- `task_type`
- `request`
- `source_artifacts`
- `goal`
- `constraints`
- `desired_fidelity`
- `comments_exist`
- `versioning_required`
- `export_review_loop_required`
- `workflow_selected`
- `success_criteria`

Purpose:

- freezes the current job definition
- gives all downstream roles the same starting point
- prevents cross-thread ambiguity

## 2. Design Direction Artifact

Created by: `presentation-designer`

Required fields:

- `visual_approach`
- `slide_hierarchy_decisions`
- `composition_logic`
- `typography_direction`
- `simplification_decisions`
- `what_should_not_change`

Purpose:

- translates a vague redesign request into a concrete design plan
- becomes the handoff into review or production

## 3. Art Direction Review

Created by: `presentation-art-director`

Required fields:

- `visual_issues`
- `consistency_issues`
- `fidelity_issues`
- `layout_review`
- `spacing_review`
- `typography_review`
- `verdict`

Allowed verdicts:

- `PASS`
- `REVISE`
- `FAIL`

Purpose:

- acts as the quality gate
- decides whether the workflow can move forward

## 4. Iteration Review

Created by: `presentation-generator-critic`

Required fields:

- `version_produced`
- `what_changed`
- `what_improved`
- `remaining_issues`
- `next_pass_notes`
- `critic_verdict`

Purpose:

- keeps iteration loops explicit and version-aware
- prevents the next role from guessing what changed

## 5. Final Design Package

Created by: `design-orchestrator`

Required fields:

- `final_artifact_or_version`
- `workflow_used`
- `roles_run`
- `review_status`
- `final_comments`
- `source_links`
- `prior_version_links`

Purpose:

- returns one canonical deliverable back to the caller
- makes cross-thread usage explicit and auditable
