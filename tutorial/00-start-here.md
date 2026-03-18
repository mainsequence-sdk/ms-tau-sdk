# Start here

This repository is both:

- a working Main Sequence project orchestrator
- a teaching repo

## Read in this order

1. `tutorial/01-what-loads-when.md`
2. `tutorial/02-before-agent-start.md`
3. `tutorial/03-specialists-and-routing.md`
4. `tutorial/04-agent-files-and-frontmatter.md`
5. `tutorial/05-typescript-runtime.md`

That is the full core tutorial.

If a file is not in that list, it should not exist as a numbered tutorial step.

## The key mental model

You are not building "inside Pi core."

You are shaping Pi from the outside by combining:

- extensions
- specialist prompt files
- prompt templates
- skills
- generated context
- repo-local TypeScript modules

## The first workflow to understand

The parent agent does this:

1. get repo context
2. understand the user request and turn it into a project brief
3. use the Main Sequence CLI to create and set up the project
4. write the target project's `astro/` files
5. delegate implementation to `mainsequence-project-coder`
6. review progress with `doc-bug-auditor`

If you understand that loop, you understand Astro.
