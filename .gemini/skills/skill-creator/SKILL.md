---
name: skill-creator
description: Create new skills, modify and improve existing skills, and measure skill performance in Google Antigravity. Use when users want to create a skill from scratch, edit, or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.
---

# Skill Creator for Google Antigravity

A skill for creating new Antigravity skills and iteratively improving them.

At a high level, the process of creating a skill goes like this:

- Decide what you want the skill to do and roughly how it should do it.
- Write a draft of the skill in `.gemini/skills/<skill-name>/SKILL.md`.
- Create test prompts and run Antigravity subagents with and without access to the skill.
- Help the user evaluate the results qualitatively and quantitatively:
  - While runs execute in the background, draft quantitative evals/assertions if none exist.
  - Use `scripts/generate_review.py` to launch or generate an HTML review interface for the user to review outputs and benchmark metrics.
- Rewrite the skill based on user feedback and quantitative benchmark findings.
- Repeat until satisfied.
- Package the skill using `scripts/package_skill.py`.

Your job when using this skill is to determine where the user is in this workflow and guide them through these stages.

---

## Communicating with the User

Users range from technical developers to non-technical domain experts. Pay attention to context cues:

- "Evaluation" and "benchmark" are generally acceptable.
- Briefly explain technical terms like "JSON", "schema", or "assertion" unless the user demonstrates familiarity.
- Keep responses clear, professional, and actionable.

---

## Creating a Skill

### 1. Capture Intent

Start by understanding the user's intent. If the current conversation contains a workflow the user wants to capture (e.g. "turn this workflow into a skill"):

1. What should this skill enable Antigravity agents to do?
2. When should this skill trigger? (User phrases, contexts, project conditions)
3. What is the expected output format?
4. Should we set up test cases to verify the skill works? (Skills with deterministic file transforms, data extraction, or code generation strongly benefit from test cases).

### 2. Interview and Research

Ask targeted questions regarding edge cases, input/output formats, example files, success criteria, and dependencies.
Search existing codebase or documentation if needed before writing test prompts.

### 3. Skill Location & Structure

In Google Antigravity, project-level skills live in `.gemini/skills/<skill-name>/` inside the workspace:

```
.gemini/skills/<skill-name>/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/    - Executable scripts (Python/Bash) for deterministic tasks
    ├── references/ - Markdown reference documentation loaded as needed
    └── assets/     - HTML templates, assets, or static files
```

### 4. Write `SKILL.md`

YAML Frontmatter requirements:
- `name`: Lowercase hyphenated identifier (e.g. `twenty-crud`, `skill-creator`).
- `description`: Detailed description explaining **what** the skill does AND **specific trigger contexts** (include keywords and intent variations). Make description pushy so the model triggers it reliably when relevant.

#### Progressive Disclosure
1. **Metadata** (frontmatter) - Loaded in context (~100 words).
2. **SKILL.md body** - Loaded when skill triggers (<500 lines recommended).
3. **Bundled resources** - Loaded dynamically as needed.

#### Writing Style Guidelines
- Use imperative, clear instructions.
- Explain the **why** behind rules so the model understands the underlying rationale.
- Define expected output templates and concrete examples.
- Keep SKILL.md lean; move extensive reference docs into `references/`.

---

## Test Cases & Evaluation Loop

Save test cases in `evals/evals.json`:

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 0,
      "name": "descriptive-eval-name",
      "prompt": "User's task prompt",
      "expected_output": "Description of expected result",
      "files": []
    }
  ]
}
```

### Running Test Cases with Subagents

Organize test outputs in `<skill-name>-workspace/iteration-<N>/eval-<ID>/`:

For each test case, spawn two subagents in parallel using `invoke_subagent`:

1. **With-skill run**:
   - `Role`: "With Skill Evaluator"
   - `TypeName`: "self"
   - `Prompt`: Include task prompt, path to skill (`.gemini/skills/<skill-name>`), and output path (`<workspace>/iteration-<N>/eval-<ID>/with_skill/outputs/`).

2. **Baseline run**:
   - If creating a new skill: `without_skill` (no skill path), saving to `without_skill/outputs/`.
   - If improving an existing skill: `old_skill` (snapshot old version using `cp -r <skill-path> <workspace>/skill-snapshot/`), saving to `old_skill/outputs/`.

Create `eval_metadata.json` in `<workspace>/iteration-<N>/eval-<ID>/eval_metadata.json`:

```json
{
  "eval_id": 0,
  "eval_name": "descriptive-eval-name",
  "prompt": "The user's task prompt",
  "assertions": []
}
```

### Capturing Timing & Token Data

Upon completion of each subagent task, save execution metrics to `timing.json` in the respective run folder:

```json
{
  "total_tokens": 45000,
  "duration_ms": 18200,
  "total_duration_seconds": 18.2
}
```

### Grading and Aggregating Benchmarks

1. **Grade Assertions**: Run a grader pass (or script) following `agents/grader.md` to evaluate assertions against outputs. Output `grading.json` in each run directory with fields `text`, `passed`, and `evidence`.

2. **Aggregate Benchmark**: Run the aggregation script from the skill-creator directory:
   ```bash
   python3 .gemini/skills/skill-creator/scripts/aggregate_benchmark.py <workspace>/iteration-N --skill-name <name>
   ```
   This generates `benchmark.json` and `benchmark.md`.

3. **Launch Review UI**: Generate the interactive HTML review interface:
   ```bash
   python3 .gemini/skills/skill-creator/scripts/generate_review.py <workspace>/iteration-N --skill-name <name> --benchmark <workspace>/iteration-N/benchmark.json
   ```
   Share the generated `review.html` path with the user.

4. **Collect User Feedback**: Once the user completes their review, inspect `feedback.json` and refine the skill based on specific complaints and benchmark findings.

---

## Skill Description Optimization

To maximize trigger precision and recall:

1. **Create Trigger Eval Queries**: Create 20 queries (8-10 should-trigger, 8-10 should-not-trigger) in `trigger_eval.json`.
2. **Review Queries**: Open `assets/eval_review.html` to let the user review and refine trigger queries.
3. **Run Optimization Loop**:
   ```bash
   python3 .gemini/skills/skill-creator/scripts/run_loop.py --eval-set <workspace>/trigger_eval.json --skill-path .gemini/skills/<skill-name>
   ```
4. **Apply Result**: Update SKILL.md frontmatter description with the best performing description.

---

## Packaging the Skill

Validate and package the finished skill for distribution:

```bash
python3 .gemini/skills/skill-creator/scripts/package_skill.py .gemini/skills/<skill-name>
```

This creates `<skill-name>.skill` archive after validating frontmatter, line limits, and file references.
