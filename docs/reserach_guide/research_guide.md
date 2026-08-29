# Research guide

> Historical note:
> This guide started from experiments around the retired `mainsequence-project-coder` role.
> `mainsequence-project-coder` is no longer a supported runtime path. Current implementation work
> belongs on `code-repository-executor`, invoked through the orchestrator/executor workflow
> rather than any direct child-launch surface.

## Research problem: cost-effective agent evaluation for code generation

We want a repeatable way to identify the cheapest coding agent that can complete a code-generation task at an acceptable quality level.

We start with a specific example: the historical `main-sequence-project-coder` agent. That role was
used to take a prompt and generate a full code folder intended to satisfy the prompt and follow
Main Sequence library guidelines.

A second, stronger evaluator agent then compares the generated code against our reference implementation and assigns a score. This gives us a concrete Agent / Evaluator setup that can later be generalized into a broader experiment where the output is code.

For that historical `main-sequence-project-coder` evaluation setup, we will provide several
prompts and project examples.

The first test prompt is:

`Build me a project that replicates the tutorial project in https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs`

The result here can be easily compared with the repository here:
`https://github.com/mainsequence-projects/introduction-tutorial`

The next prompt and project will be shared as public repos in:
`https://github.com/mainsequence-projects`

Each project will also include the prompt used to generate the project.

The current Astro product flow is orchestrator plus code-repository-executor. This document keeps the older
coder example only as research context for the evaluation shape.

## Historical example: `main-sequence-project-coder`

### Inputs

- prompt describing the coding task
- Main Sequence library guidelines
- reference project or code folder

### Output

- a full generated code folder representing the prompt intent and following Main Sequence conventions

### Evaluation

This is where Know Center expertise will be very valuable in designing the experiment and the evaluation measure.

A secondary evaluator agent compares:

- the generated code folder
- our version of the code

The evaluator assigns a score using a stronger model, such as GPT-5.4 or a top Anthropic model.

### Proposed scoring scale

- `0` - does not fulfill the task
- `1` - fulfills the task, but needs significant improvements
- `2` - fulfills the task
- `3` - fulfills the task and improves on the expected solution

## Particular example workflow

```mermaid
flowchart TD
    A[Prompt] --> B["historical main-sequence-project-coder"]
    B --> C[Generated Code Folder]
    D[Reference Code Folder] --> E[Evaluator Agent]
    C --> E
    E --> F[Score: 0 / 1 / 2 / 3]
    F --> G[Per-task result]
```

## Generalized experiment

We want to generalize this into an Agent / Evaluator experiment for code generation.

### Experiment inputs

- a set of prompts
- a target coding library or framework
- an evaluation set
- a set of candidate coding agents or models
- a strong evaluator model

### Experiment goal

1. find the cheapest LLM-based coding agent that can perform the task
2. build a repeatable and measurable process for identifying the cheapest agent that scores at least `2`

## Research objective

The objective is not only to compare models on quality, but to compare them on cost-adjusted usefulness.

The key question is:

> What is the cheapest coding agent that can reliably achieve a score of `2` or higher on the evaluation set?

## Measurement framework

For each candidate coding agent, we run the same evaluation process across the same prompt set and measure:

- score per task
- average score
- distribution of scores
- percentage of tasks scoring `2` or higher
- cost per run
- total cost across the evaluation set
- cost per acceptable result

## Success criterion

An agent is acceptable if it:

- achieves a score of `2` or higher
- does so reliably across the evaluation set
- is cheaper than alternative agents with similar quality

The preferred agent is the lowest-cost agent that still meets the target quality threshold.

## Generalized workflow

```mermaid
flowchart TD
    A[Prompt Set] --> B[Candidate Coding Agent]
    H[Target Library / Guidelines] --> B
    B --> C[Generated Code]
    D[Evaluation Set / Reference Implementations] --> E[Evaluator Agent]
    C --> E
    E --> F[Score per Task]
    F --> G[Aggregate Metrics]
    G --> I[Cost vs Quality Comparison]
    I --> J[Cheapest Agent with Score >= 2]
```

## Expected outcome

The outcome of this research should be a standard evaluation framework for code-generation agents that allows us to:

- test multiple coding agents on the same benchmark
- evaluate them with a consistent judging process
- measure both quality and cost
- select the cheapest acceptable agent for a given coding domain

## Deliverable

A reusable Agent / Evaluator experiment framework for code generation, starting from the historical
`main-sequence-project-coder` case study and extending to any coding library, prompt suite, or
evaluation set.

## Current execution guidance

Do not launch `mainsequence-project-coder` directly. That role is retired.

For current Astro flows:

- use the normal parent flow when orchestration, project selection, or handoff preparation still
  belongs to `astro-orchestrator`
- use the dedicated `code-repository-executor` runtime once implementation work moves onto the
  executor path

The important architecture change is that project implementation is no longer modeled as a direct
child launch inside a coder session.
