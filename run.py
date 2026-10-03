#!/usr/bin/env python3
"""One-command run: rebuild index, optional evals, start the product UI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_evals(rebuild: bool):
    from generate import provider_name
    from memory_system import MemorySystem
    from actions import ActionSystem

    print("answer generator:", provider_name())

    mem = MemorySystem(ROOT / "data", rebuild=rebuild)
    answers = ROOT / "outputs" / "memory_train_answers.jsonl"
    answers.parent.mkdir(exist_ok=True)
    mem.process_questions_file(str(ROOT / "evals" / "memory_train.jsonl"), str(answers))
    # also copy to repo root for BRIEF "output files"
    (ROOT / "memory_answers.jsonl").write_text(answers.read_text(encoding="utf-8"), encoding="utf-8")

    acts = ActionSystem(ROOT / "data")
    apred = ROOT / "outputs" / "actions_train_predictions.jsonl"
    acts.process_file(str(ROOT / "evals" / "actions_train.jsonl"), str(apred))
    (ROOT / "action_predictions.jsonl").write_text(apred.read_text(encoding="utf-8"), encoding="utf-8")

    env = os.environ.copy()
    py = sys.executable
    subprocess.check_call(
        [py, "score_retrieval.py", "--gold", str(ROOT / "evals/memory_train.jsonl"),
         "--answers", str(answers), "--out", str(ROOT / "results_retrieval.json")],
        cwd=ROOT / "eval_harness",
        env=env,
    )
    subprocess.check_call(
        [py, "score_memory.py", "--gold", str(ROOT / "evals/memory_train.jsonl"),
         "--answers", str(answers), "--judge", "none", "--out", str(ROOT / "results_memory.json")],
        cwd=ROOT / "eval_harness",
        env=env,
    )
    subprocess.check_call(
        [py, "score_actions.py", "--gold", str(ROOT / "evals/actions_train.jsonl"),
         "--predictions", str(apred), "--out", str(ROOT / "results_actions.json")],
        cwd=ROOT / "eval_harness",
        env=env,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--eval", action="store_true", help="Run train evals before serving")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--no-server", action="store_true")
    p.add_argument("--host", default=os.environ.get("CANDOR_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("CANDOR_PORT", "8787")))
    args = p.parse_args()

    if args.eval:
        run_evals(rebuild=True)
    elif args.rebuild:
        from memory_system import MemorySystem
        MemorySystem(ROOT / "data", rebuild=True)

    if args.no_server:
        return

    import uvicorn
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
