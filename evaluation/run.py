"""Score recorded JSONL call runs against reusable deterministic criteria.

Each line must contain scenario_id, success, booking_correct, turns, tool_calls,
redundant_questions, failed_tool_calls, and optional latency_ms with total/tool/llm/asr/tts.
"""
import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path


def mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def evaluate(runs: list[dict], scenarios: dict[str, dict]) -> dict:
    details = []
    for run in runs:
        scenario = scenarios[run["scenario_id"]]
        calls = run.get("tool_calls", [])
        required = scenario["required_tools"]
        ordered = all(tool in calls and (i == 0 or calls.index(required[i - 1]) < calls.index(tool))
                      for i, tool in enumerate(required))
        passed = bool(run.get("success") and ordered and run.get("turns", 999) <= scenario["max_turns"])
        details.append({**run, "required_tools_in_order": ordered, "passed": passed})
    latency_names = ("total", "tool", "llm", "asr", "tts")
    metrics = {
        "success_rate": mean([100.0 if d["passed"] else 0.0 for d in details]),
        "booking_accuracy": mean([100.0 if d.get("booking_correct") else 0.0 for d in details
                                  if scenarios[d["scenario_id"]]["intent"] in {"booking", "callback"}]),
        "turns_to_completion": mean([float(d["turns"]) for d in details if d.get("success")]),
        "redundant_questions": sum(int(d.get("redundant_questions", 0)) for d in details),
        "failed_tool_calls": sum(int(d.get("failed_tool_calls", 0)) for d in details),
    }
    for name in latency_names:
        metrics[f"average_{name}_latency_ms"] = mean([
            float(d.get("latency_ms", {}).get(name)) for d in details
            if d.get("latency_ms", {}).get(name) is not None
        ])
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "runs": len(runs),
            "metrics": metrics, "details": details}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", type=Path, help="JSONL observations exported from call tests")
    parser.add_argument("--output", type=Path, default=Path("evaluation/report.json"))
    args = parser.parse_args()
    scenarios = {s["id"]: s for s in json.loads(Path("evaluation/scenarios.json").read_text())}
    runs = [json.loads(line) for line in args.runs.read_text().splitlines() if line.strip()]
    report = evaluate(runs, scenarios)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
