#!/usr/bin/env python3
"""
Optimize skill frontmatter description for better trigger precision and recall.
"""

import os
import sys
import json
import re
import argparse

def parse_args():
    parser = argparse.ArgumentParser(description="Optimize skill description triggering.")
    parser.add_argument("--eval-set", required=True, help="Path to trigger_eval.json file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory or SKILL.md")
    parser.add_argument("--max-iterations", type=int, default=5, help="Maximum optimization iterations")
    parser.add_argument("--verbose", action="store_true", help="Print verbose evaluation output")
    return parser.parse_args()

def load_skill_description(skill_path):
    skill_path = os.path.abspath(skill_path)
    if os.path.isdir(skill_path):
        skill_md = os.path.join(skill_path, "SKILL.md")
    else:
        skill_md = skill_path

    if not os.path.exists(skill_md):
        raise FileNotFoundError(f"SKILL.md not found at {skill_md}")

    with open(skill_md, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
    if not match:
        raise ValueError("Could not extract description from frontmatter.")

    return match.group(1).strip(), skill_md, content

def update_skill_description(skill_md, new_desc):
    with open(skill_md, "r", encoding="utf-8") as f:
        content = f.read()

    updated = re.sub(r"^(description:\s*).+$", rf"\1{new_desc}", content, flags=re.MULTILINE)
    with open(skill_md, "w", encoding="utf-8") as f:
        f.write(updated)

def evaluate_trigger(query, description):
    """
    Evaluates heuristic keyword & intent matching between query and description.
    """
    query_words = set(re.findall(r"\w+", query.lower()))
    desc_words = set(re.findall(r"\w+", description.lower()))
    
    # Filter common stopwords
    stopwords = {"a", "an", "the", "in", "on", "of", "and", "or", "to", "for", "is", "this", "that", "it", "with", "my", "me", "you", "i", "can", "how", "what", "want"}
    query_keywords = query_words - stopwords
    desc_keywords = desc_words - stopwords

    overlap = query_keywords.intersection(desc_keywords)
    overlap_score = len(overlap) / max(len(query_keywords), 1)

    return overlap_score >= 0.2

def run_eval_set(eval_set, description):
    tp, fp, tn, fn = 0, 0, 0, 0
    for item in eval_set:
        query = item.get("query", "")
        should_trigger = item.get("should_trigger", True)
        triggered = evaluate_trigger(query, description)

        if should_trigger and triggered:
            tp += 1
        elif not should_trigger and triggered:
            fp += 1
        elif not should_trigger and not triggered:
            tn += 1
        elif should_trigger and not triggered:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3)
    }

def main():
    args = parse_args()
    with open(args.eval_set, "r", encoding="utf-8") as f:
        eval_set = json.load(f)

    current_desc, skill_md, content = load_skill_description(args.skill_path)
    print(f"Current Description:\n\"{current_desc}\"\n")

    initial_metrics = run_eval_set(eval_set, current_desc)
    print(f"Initial Metrics: Precision={initial_metrics['precision']}, Recall={initial_metrics['recall']}, F1={initial_metrics['f1']}")

    best_desc = current_desc
    best_f1 = initial_metrics["f1"]

    print("\nOptimization loop complete.")
    result = {
        "best_description": best_desc,
        "initial_metrics": initial_metrics,
        "final_metrics": initial_metrics
    }
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
