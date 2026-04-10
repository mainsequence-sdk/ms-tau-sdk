---
name: mainsequence-project-creation
description: Guidance for turning a user's idea into a new Main Sequence project by collecting structured creation intake before validating the name or creating the project.
---

Use this skill when the user wants to create a new Main Sequence project or has an idea that does not map to an existing project yet.

## Core flow

1. Treat new-project creation as a short intake workflow, not as an immediate `project create` command.
2. Collect the missing pieces needed to shape the project well before validating the name or creating it.
3. Ask only for the missing pieces, and prefer a concise guided questionnaire over open-ended prompts.

## Project creation intake

Gather enough detail to define:

- the project goal or business outcome
- the primary workflow the project should handle
- the key requirements or first tasks
- the inputs, data sources, integrations, or external systems involved
- the expected outputs, deliverables, or user-visible behavior
- the acceptance criteria or definition of done
- important constraints, risks, or non-goals
- the project name, either user-provided or explicitly confirmed after you propose one

If the request is still vague after the first pass, keep narrowing until the intended project is concrete enough to brief, name, and create.

## Naming and creation

1. Once the intake is concrete enough, translate it into:
   - a short brief
   - a concrete task list
   - acceptance criteria
   - a proposed project name
2. Confirm the project name if the user did not provide it directly.
3. Only then run:
   - `mainsequence project validate-name "<name>"`
   - `mainsequence project create "<name>"`

## Output expectations

- Keep the intake structured and focused.
- Do not rush into creation with a shallow brief.
- Be explicit about what is still missing if the project is not ready to create.
