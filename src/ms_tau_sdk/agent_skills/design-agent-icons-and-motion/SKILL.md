---
name: design-agent-icons-and-motion
description: Offer general composition and motion guidance when designing or reviewing agent icons. Use for visual identity work, not conversation or session implementation.
---

# Agent Icon Composition and Motion

These are suggestions. Adapt them to the user's direction, the project's identity and the host.

Keep each agent's artwork, animations and build files in its owning project. `ms-tau-sdk`
distributes general guidance and this worked example; it does not own project or provider assets.

## Composition

- Start with the project's or provider's actual logo and preserve its recognisable shape.
- Keep one clear visual focus, balanced spacing and enough empty space for motion.
- Use consistent proportions, stroke weight and restrained accents to relate a family without
  making its members indistinguishable. Avoid adding detail that disappears at display size.
- Fit the host's typography, colours and surfaces; check contrast in both themes.

## Motion

- Consider animation for every agent, with quiet idle motion and distinct activity motion.
- Keep the identity readable while a supporting element communicates activity.
- Let motion reflect the agent's purpose and reported state; never imply unreported progress
  or success. Prefer restrained movement and smooth loop seams.
- Provide a useful still and respect reduced motion.

## Worked Example: MetaTables

Read or open [the visual walkthrough and editable source](references/metatables-example.html).
It builds the real logo into three layers, shows four animated states and actual display sizes,
and includes light/dark and reduced-motion checks. Expand its source section to see the SVG
composition and CSS that draw those same previews.

Follow the walkthrough's adaptation steps: preserve the next project's identity, reserve space
for activity, and design an accent that reflects its purpose. The example's coordinates and
row-scanning motif explain one solution; they are not a template every agent must copy.

Review at the actual display size, checking recognition, spacing, clipping and both themes.
Check the target renderer's supported formats, size limits and states before integration. Keep
this design work within the requested scope; it does not itself publish or replace live icons.
