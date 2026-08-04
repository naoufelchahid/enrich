#!/usr/bin/env python3
"""
Aggregate benchmark results across iteration test runs.
Produces benchmark.json and benchmark.md inside the iteration directory.
"""

import os
import sys
import json
import math
import argparse

def parse_args():
    parser = argparse.ArgumentParser(description="Aggregate benchmark evaluation runs.")
    parser.add_argument("workspace_dir", help="Path to workspace iteration directory (e.g. workspace/iteration-1)")
    parser.add_argument("--skill-name", default="skill", help="Name of the skill being evaluated")
    return parser.parse_args()

def calculate_stats(values):
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values) if len(values) > 1 else 0.0
    return mean, math.sqrt(variance)

def main():
    args = parse_args()
    iteration_dir = os.path.abspath(args.workspace_dir)

    if not os.path.exists(iteration_dir):
        print(f"Error: Iteration directory {iteration_dir} does not exist.")
        sys.exit(1)

    eval_dirs = [d for d in os.listdir(iteration_dir) if d.startswith("eval-") and os.path.isdir(os.path.join(iteration_dir, d))]
    eval_dirs.sort()

    configs_data = {}
    eval_breakdown = []

    for eval_dirname in eval_dirs:
        eval_path = os.path.join(iteration_dir, eval_dirname)
        metadata_path = os.path.join(eval_path, "eval_metadata.json")
        
        eval_id = eval_dirname.replace("eval-", "")
        eval_name = eval_dirname
        prompt = ""
        
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    eval_id = meta.get("eval_id", eval_id)
                    eval_name = meta.get("eval_name", eval_name)
                    prompt = meta.get("prompt", "")
            except Exception as e:
                pass

        eval_record = {
            "eval_id": eval_id,
            "eval_name": eval_name,
            "prompt": prompt
        }

        # Check configs inside eval directory (with_skill, without_skill, old_skill)
        subdirs = [s for s in os.listdir(eval_path) if os.path.isdir(os.path.join(eval_path, s))]
        for config in subdirs:
            config_path = os.path.join(eval_path, config)
            grading_path = os.path.join(config_path, "grading.json")
            timing_path = os.path.join(config_path, "timing.json")

            passed = False
            if os.path.exists(grading_path):
                try:
                    with open(grading_path, "r", encoding="utf-8") as f:
                        grade_data = json.load(f)
                        passed = grade_data.get("overall_pass", False)
                except Exception:
                    pass

            duration = 0.0
            tokens = 0
            if os.path.exists(timing_path):
                try:
                    with open(timing_path, "r", encoding="utf-8") as f:
                        time_data = json.load(f)
                        duration = time_data.get("total_duration_seconds", time_data.get("duration_ms", 0) / 1000.0)
                        tokens = time_data.get("total_tokens", 0)
                except Exception:
                    pass

            eval_record[config] = {
                "passed": passed,
                "duration_seconds": duration,
                "tokens": tokens
            }

            if config not in configs_data:
                configs_data[config] = {
                    "passes": [],
                    "durations": [],
                    "tokens": []
                }
            configs_data[config]["passes"].append(1 if passed else 0)
            configs_data[config]["durations"].append(duration)
            configs_data[config]["tokens"].append(tokens)

        eval_breakdown.append(eval_record)

    aggregated_configs = {}
    for config, data in configs_data.items():
        total = len(data["passes"])
        pass_rate = sum(data["passes"]) / total if total > 0 else 0.0
        mean_dur, std_dur = calculate_stats(data["durations"])
        mean_tok, std_tok = calculate_stats(data["tokens"])

        aggregated_configs[config] = {
            "pass_rate": round(pass_rate, 4),
            "mean_duration_seconds": round(mean_dur, 2),
            "stddev_duration_seconds": round(std_dur, 2),
            "mean_tokens": round(mean_tok, 1),
            "total_evals": total
        }

    benchmark_json = {
        "skill_name": args.skill_name,
        "iteration": os.path.basename(iteration_dir),
        "configurations": aggregated_configs,
        "eval_breakdown": eval_breakdown
    }

    # Save benchmark.json
    benchmark_json_path = os.path.join(iteration_dir, "benchmark.json")
    with open(benchmark_json_path, "w", encoding="utf-8") as f:
        json.dump(benchmark_json, f, indent=2)

    # Generate benchmark.md summary table
    md_lines = [
        f"# Benchmark Report: {args.skill_name} ({os.path.basename(iteration_dir)})",
        "",
        "## Summary",
        "",
        "| Configuration | Pass Rate | Mean Duration (s) | Mean Tokens | Total Evals |",
        "|---|---|---|---|---|"
    ]
    for config, stats in aggregated_configs.items():
        md_lines.append(
            f"| `{config}` | {stats['pass_rate'] * 100:.1f}% | {stats['mean_duration_seconds']}s ± {stats['stddev_duration_seconds']}s | {stats['mean_tokens']:.0f} | {stats['total_evals']} |"
        )

    md_lines.extend([
        "",
        "## Per-Eval Breakdown",
        "",
        "| Eval ID | Name | " + " | ".join(f"`{cfg}` Pass" for cfg in aggregated_configs.keys()) + " |",
        "|---|---| " + " | ".join("---" for _ in aggregated_configs.keys()) + " |"
    ])

    for item in eval_breakdown:
        passes_str = []
        for cfg in aggregated_configs.keys():
            res = item.get(cfg, {})
            status = "✅ Pass" if res.get("passed") else "❌ Fail"
            passes_str.append(status)
        md_lines.append(f"| {item['eval_id']} | {item['eval_name']} | " + " | ".join(passes_str) + " |")

    benchmark_md_path = os.path.join(iteration_dir, "benchmark.md")
    with open(benchmark_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    print(f"Benchmark aggregation complete: {benchmark_json_path}")

if __name__ == "__main__":
    main()
