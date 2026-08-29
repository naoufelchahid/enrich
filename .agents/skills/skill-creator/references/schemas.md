# JSON Schemas for Skill Creator

This reference defines the JSON schemas used by `skill-creator` for test cases, timing, grading, benchmarking, feedback, and trigger evaluation.

---

## 1. `evals.json`
Path: `evals/evals.json` inside skill directory or workspace.

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 0,
      "name": "descriptive-eval-name",
      "prompt": "User's task prompt here",
      "expected_output": "Description of expected result",
      "files": ["input_data.csv"],
      "assertions": [
        {
          "id": "assert-1",
          "text": "The generated file contains valid header row"
        }
      ]
    }
  ]
}
```

---

## 2. `eval_metadata.json`
Path: `<workspace>/iteration-<N>/eval-<ID>/eval_metadata.json`

```json
{
  "eval_id": 0,
  "eval_name": "descriptive-eval-name",
  "prompt": "The user's task prompt",
  "assertions": [
    {
      "id": "assert-1",
      "text": "Output file exists and is non-empty"
    }
  ]
}
```

---

## 3. `timing.json`
Path: `<workspace>/iteration-<N>/eval-<ID>/<config>/timing.json` (where `<config>` is `with_skill`, `without_skill`, or `old_skill`).

```json
{
  "total_tokens": 42000,
  "duration_ms": 15200,
  "total_duration_seconds": 15.2
}
```

---

## 4. `grading.json`
Path: `<workspace>/iteration-<N>/eval-<ID>/<config>/grading.json`

```json
{
  "eval_id": 0,
  "config": "with_skill",
  "expectations": [
    {
      "text": "Output file exists and is non-empty",
      "passed": true,
      "evidence": "File output.csv found with 125 lines (4.2 KB)."
    }
  ],
  "overall_pass": true
}
```

> **Note:** The `expectations` array MUST use `text`, `passed`, and `evidence` key names.

---

## 5. `benchmark.json`
Path: `<workspace>/iteration-<N>/benchmark.json`

```json
{
  "skill_name": "example-skill",
  "iteration": 1,
  "configurations": {
    "with_skill": {
      "pass_rate": 1.0,
      "mean_duration_seconds": 18.4,
      "stddev_duration_seconds": 2.1,
      "mean_tokens": 45000,
      "total_evals": 3
    },
    "without_skill": {
      "pass_rate": 0.33,
      "mean_duration_seconds": 25.1,
      "stddev_duration_seconds": 4.5,
      "mean_tokens": 62000,
      "total_evals": 3
    }
  },
  "eval_breakdown": [
    {
      "eval_id": 0,
      "eval_name": "descriptive-eval-name",
      "with_skill": {
        "passed": true,
        "duration_seconds": 16.2,
        "tokens": 42000
      },
      "without_skill": {
        "passed": false,
        "duration_seconds": 28.0,
        "tokens": 65000
      }
    }
  ]
}
```

---

## 6. `feedback.json`
Path: `<workspace>/iteration-<N>/feedback.json`

```json
{
  "reviews": [
    {
      "run_id": "eval-0-with_skill",
      "feedback": "Chart looks great but needs axis labels",
      "timestamp": "2026-08-03T12:00:00Z"
    },
    {
      "run_id": "eval-1-with_skill",
      "feedback": "",
      "timestamp": "2026-08-03T12:00:00Z"
    }
  ],
  "status": "complete"
}
```

---

## 7. `trigger_eval.json`
Path: `<workspace>/trigger_eval.json`

```json
[
  {
    "query": "Can you analyze sales data from Q4 sales final.xlsx and output margin %?",
    "should_trigger": true
  },
  {
    "query": "Write a python function to compute fibonacci sequence",
    "should_trigger": false
  }
]
```
