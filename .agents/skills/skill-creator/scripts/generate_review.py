#!/usr/bin/env python3
"""
Generate review UI for human evaluation of test run outputs and benchmark metrics.
Supports both local server mode and static standalone HTML file generation.
"""

import os
import sys
import json
import argparse
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.parse

def parse_args():
    parser = argparse.ArgumentParser(description="Generate eval review interface.")
    parser.add_argument("workspace_dir", help="Path to workspace iteration directory")
    parser.add_argument("--skill-name", default="Skill Review", help="Name of skill being reviewed")
    parser.add_argument("--benchmark", help="Path to benchmark.json")
    parser.add_argument("--previous-workspace", help="Path to previous iteration workspace directory")
    parser.add_argument("--static", help="Output static HTML file path (headless / static mode)")
    parser.add_argument("--port", type=int, default=8501, help="Port for review server (default 8501)")
    return parser.parse_args()

def collect_review_data(workspace_dir, benchmark_path=None, prev_workspace_dir=None):
    workspace_dir = os.path.abspath(workspace_dir)
    eval_dirs = [d for d in os.listdir(workspace_dir) if d.startswith("eval-") and os.path.isdir(os.path.join(workspace_dir, d))]
    eval_dirs.sort()

    runs_data = []
    
    for eval_dirname in eval_dirs:
        eval_path = os.path.join(workspace_dir, eval_dirname)
        meta_path = os.path.join(eval_path, "eval_metadata.json")
        
        prompt = ""
        assertions = []
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    prompt = meta.get("prompt", "")
                    assertions = meta.get("assertions", [])
            except Exception:
                pass

        subdirs = [s for s in os.listdir(eval_path) if os.path.isdir(os.path.join(eval_path, s))]
        for config in subdirs:
            config_path = os.path.join(eval_path, config)
            output_dir = os.path.join(config_path, "outputs")
            grading_path = os.path.join(config_path, "grading.json")
            
            outputs = {}
            if os.path.exists(output_dir):
                for fname in os.listdir(output_dir):
                    fpath = os.path.join(output_dir, fname)
                    if os.path.isfile(fpath):
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                                outputs[fname] = f.read()
                        except Exception:
                            outputs[fname] = "<Binary or non-readable content>"

            grading = {}
            if os.path.exists(grading_path):
                try:
                    with open(grading_path, "r", encoding="utf-8") as f:
                        grading = json.load(f)
                except Exception:
                    pass

            run_id = f"{eval_dirname}-{config}"
            runs_data.append({
                "run_id": run_id,
                "eval_name": eval_dirname,
                "config": config,
                "prompt": prompt,
                "assertions": assertions,
                "outputs": outputs,
                "grading": grading
            })

    benchmark_data = {}
    bpath = benchmark_path or os.path.join(workspace_dir, "benchmark.json")
    if os.path.exists(bpath):
        try:
            with open(bpath, "r", encoding="utf-8") as f:
                benchmark_data = json.load(f)
        except Exception:
            pass

    return {
        "workspace_dir": workspace_dir,
        "runs": runs_data,
        "benchmark": benchmark_data
    }

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Eval Review - __SKILL_NAME__</title>
    <style>
        :root {
            --bg-primary: #0f172a;
            --bg-secondary: #1e293b;
            --bg-card: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-hover: #0284c7;
            --pass: #22c55e;
            --fail: #ef4444;
            --border: #475569;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-main);
            margin: 0;
            padding: 0;
            display: flex;
            flex-direction: column;
            height: 100vh;
        }
        header {
            background-color: var(--bg-secondary);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
        }
        h1 { margin: 0; font-size: 1.25rem; font-weight: 600; }
        .tabs { display: flex; gap: 1rem; }
        .tab-btn {
            background: none;
            border: none;
            color: var(--text-muted);
            font-size: 1rem;
            padding: 0.5rem 1rem;
            cursor: pointer;
            border-bottom: 2px solid transparent;
        }
        .tab-btn.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
            font-weight: 600;
        }
        main {
            flex: 1;
            overflow-y: auto;
            padding: 2rem;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .card {
            background-color: var(--bg-secondary);
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            border: 1px solid var(--border);
        }
        .prompt-box {
            background-color: var(--bg-primary);
            padding: 1rem;
            border-radius: 6px;
            font-family: monospace;
            white-space: pre-wrap;
            margin-top: 0.5rem;
        }
        .output-box {
            background-color: var(--bg-primary);
            padding: 1rem;
            border-radius: 6px;
            font-family: monospace;
            white-space: pre-wrap;
            max-height: 400px;
            overflow-y: auto;
            margin-top: 0.5rem;
        }
        .feedback-input {
            width: 100%;
            height: 80px;
            background-color: var(--bg-primary);
            color: var(--text-main);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 0.75rem;
            font-family: inherit;
            resize: vertical;
            box-sizing: border-box;
        }
        .submit-btn {
            background-color: var(--accent);
            color: #000;
            font-weight: 600;
            padding: 0.75rem 1.5rem;
            border: none;
            border-radius: 6px;
            cursor: pointer;
        }
        .submit-btn:hover { background-color: var(--accent-hover); }
        .status-tag {
            display: inline-block;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.85rem;
            font-weight: 600;
        }
        .status-pass { background-color: rgba(34, 197, 94, 0.2); color: var(--pass); }
        .status-fail { background-color: rgba(239, 68, 68, 0.2); color: var(--fail); }
        table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
        th, td { padding: 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }
        th { background-color: var(--bg-card); }
    </style>
</head>
<body>
    <header>
        <h1>Eval Review: __SKILL_NAME__</h1>
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('outputs')">Outputs</button>
            <button class="tab-btn" onclick="switchTab('benchmark')">Benchmark</button>
        </div>
        <button class="submit-btn" onclick="submitFeedback()">Submit All Reviews</button>
    </header>
    <main>
        <div class="container">
            <div id="outputs-tab">
                <div id="runs-list"></div>
            </div>
            <div id="benchmark-tab" style="display:none;">
                <div class="card">
                    <h2>Quantitative Benchmark Summary</h2>
                    <div id="benchmark-summary"></div>
                </div>
            </div>
        </div>
    </main>

    <script>
        const reviewData = __REVIEW_DATA_JSON__;
        const feedbackMap = {};

        function switchTab(tab) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            if (tab === 'outputs') {
                document.getElementById('outputs-tab').style.display = 'block';
                document.getElementById('benchmark-tab').style.display = 'none';
                event.target.classList.add('active');
            } else {
                document.getElementById('outputs-tab').style.display = 'none';
                document.getElementById('benchmark-tab').style.display = 'block';
                event.target.classList.add('active');
            }
        }

        function renderOutputs() {
            const list = document.getElementById('runs-list');
            list.innerHTML = '';
            reviewData.runs.forEach(run => {
                const card = document.createElement('div');
                card.className = 'card';
                
                let outputsHtml = '';
                for (const [fname, content] of Object.entries(run.outputs)) {
                    outputsHtml += `<h4>${fname}</h4><div class="output-box">${escapeHtml(content)}</div>`;
                }

                let gradingHtml = '';
                if (run.grading && run.grading.expectations) {
                    gradingHtml += '<h4>Formal Expectations</h4><ul>';
                    run.grading.expectations.forEach(exp => {
                        const cls = exp.passed ? 'status-pass' : 'status-fail';
                        gradingHtml += `<li><span class="status-tag ${cls}">${exp.passed ? 'PASS' : 'FAIL'}</span> ${escapeHtml(exp.text)} - <em>${escapeHtml(exp.evidence || '')}</em></li>`;
                    });
                    gradingHtml += '</ul>';
                }

                card.innerHTML = `
                    <h3>${run.eval_name} (${run.config})</h3>
                    <div><strong>Prompt:</strong><div class="prompt-box">${escapeHtml(run.prompt)}</div></div>
                    <div style="margin-top: 1rem;">${outputsHtml}</div>
                    <div style="margin-top: 1rem;">${gradingHtml}</div>
                    <div style="margin-top: 1rem;">
                        <strong>Your Feedback:</strong>
                        <textarea class="feedback-input" onchange="feedbackMap['${run.run_id}'] = this.value" placeholder="Enter comments or suggestions for improvement..."></textarea>
                    </div>
                `;
                list.appendChild(card);
            });
        }

        function renderBenchmark() {
            const container = document.getElementById('benchmark-summary');
            const bm = reviewData.benchmark;
            if (!bm || !bm.configurations) {
                container.innerHTML = '<p>No benchmark data available.</p>';
                return;
            }
            let html = '<table><thead><tr><th>Configuration</th><th>Pass Rate</th><th>Mean Duration</th><th>Mean Tokens</th></tr></thead><tbody>';
            for (const [cfg, stats] of Object.entries(bm.configurations)) {
                html += `<tr><td><strong>${cfg}</strong></td><td>${(stats.pass_rate*100).toFixed(1)}%</td><td>${stats.mean_duration_seconds}s ± ${stats.stddev_duration_seconds}s</td><td>${stats.mean_tokens}</td></tr>`;
            }
            html += '</tbody></table>';
            container.innerHTML = html;
        }

        function escapeHtml(text) {
            return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
        }

        function submitFeedback() {
            const payload = {
                reviews: Object.entries(feedbackMap).map(([run_id, feedback]) => ({
                    run_id: run_id,
                    feedback: feedback,
                    timestamp: new Date().toISOString()
                })),
                status: "complete"
            };

            const jsonStr = JSON.stringify(payload, null, 2);

            // In static mode or if API fails, download file
            const blob = new Blob([jsonStr], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "feedback.json";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            alert("Feedback exported to feedback.json!");
        }

        renderOutputs();
        renderBenchmark();
    </script>
</body>
</html>
"""

def main():
    args = parse_args()
    data = collect_review_data(args.workspace_dir, args.benchmark, args.previous_workspace)

    html_content = HTML_TEMPLATE.replace("__SKILL_NAME__", args.skill_name)
    html_content = html_content.replace("__REVIEW_DATA_JSON__", json.dumps(data))

    if args.static:
        static_path = os.path.abspath(args.static)
        os.makedirs(os.path.dirname(static_path), exist_ok=True)
        with open(static_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Generated static eval review UI: {static_path}")
    else:
        out_path = os.path.join(args.workspace_dir, "review.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Eval review HTML generated at: {out_path}")

if __name__ == "__main__":
    main()
