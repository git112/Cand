#!/usr/bin/env python3
"""Analyze failing cases to understand what needs fixing."""
import json
import sys
sys.path.insert(0, 'D:/candor')

from memory_system import MemorySystem

mem = MemorySystem('D:/candor/data')

# Test failing questions
failing = [
    ("MEM-TR-04", "Did I send Sarah Patel the pricing proposal I promised?", "2026-09-18T18:00:00-07:00"),
    ("MEM-TR-05", "What pricing did we propose to Acme Freight?", "2026-09-18T18:00:00-07:00"),
    ("MEM-TR-16", "What did Harbor Logistics say about SOC 2?", "2026-09-18T18:00:00-07:00"),
    ("MEM-TR-20", "What did I dictate to Sarah Patel on Sep 10, and did it go out?", "2026-09-18T18:00:00-07:00"),
    ("MEM-TR-21", "Why did the launch slip from September 30?", "2026-09-18T18:00:00-07:00"),
    ("MEM-TR-25", "What's on my calendar the day I fly to Denver?", "2026-09-18T18:00:00-07:00"),
    ("MEM-TR-26", "Has Acme signed the contract?", "2026-09-18T18:00:00-07:00"),
]

for qid, q, as_of in failing:
    result = mem.answer_question(qid, q, as_of)
    print(f"\n{'='*60}")
    print(f"{qid}: {q}")
    print(f"as_of: {as_of}")
    print(f"abstained: {result['abstained']}")
    print(f"answer: {result['answer'][:300]}")
    print(f"sources: {result['sources']}")
    print(f"retrieved (top 10): {result['retrieved'][:10]}")
