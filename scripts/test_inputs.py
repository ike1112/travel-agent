"""
Runs a battery of test inputs through the travel intent extractor.

Covers:
  - Happy path inputs (should return READY_TO_PROCESS)
  - Edge cases (should return NEEDS_CLARIFICATION with clear reasons)
  - Deliberate failure scenarios documented in the build plan

Usage:
  python3 scripts/test_inputs.py              # run all tests
  python3 scripts/test_inputs.py happy        # run only happy-path tests
  python3 scripts/test_inputs.py edge         # run only edge-case tests
  python3 scripts/test_inputs.py failure      # run only deliberate failure tests
"""

import sys
import time
import json
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

sys.path.insert(0, ".")
from scripts.extract_travel_intent import (
    build_bedrock_client,
    extract_travel_intent,
    print_result,
    MODEL_ID,
)

HAPPY_PATH = [
    {
        "label": "complete detailed request",
        "input": "Flying from Edmonton to Vancouver, March 14-17 2026, prefer morning flights, budget $1500 CAD, 2 travellers, like hiking and Japanese food",
        "expect_status": "READY_TO_PROCESS",
    },
    {
        "label": "multi-city / connecting trip",
        "input": "I want to fly from Calgary to Tokyo via Vancouver on April 10th, returning April 20th, budget $4000, interested in temples and street food",
        "expect_status": "READY_TO_PROCESS",
    },
    {
        "label": "casual shorthand",
        "input": "YEG to YVR long weekend March 14-17, morning flights pls, under $1500",
        "expect_status": "READY_TO_PROCESS",
    },
]

EDGE_CASES = [
    {
        "label": "vague destination — somewhere warm",
        "input": "somewhere warm and cheap for two weeks in February",
        "expect_status": "NEEDS_CLARIFICATION",
        "expect_missing": ["destination", "origin_city"],
    },
    {
        "label": "missing origin city",
        "input": "I want to go to Vancouver in March 2026, budget $800",
        "expect_status": "NEEDS_CLARIFICATION",
        "expect_missing": ["origin_city"],
    },
    {
        "label": "conflicting dates — return before departure",
        "input": "fly from Edmonton to Vancouver, departing March 20th and returning March 10th 2026, budget $1200",
        "expect_status": "NEEDS_CLARIFICATION",
        "expect_missing": ["travel_dates"],
    },
    {
        "label": "preferences only, no logistics",
        "input": "I really enjoy hiking, good coffee shops and craft beer. Would love a relaxing weekend.",
        "expect_status": "NEEDS_CLARIFICATION",
        "expect_missing": ["origin_city", "destination", "travel_dates", "budget_cad"],
    },
    {
        "label": "budget too low for trip",
        "input": "fly to Tokyo from Edmonton departing April 5th returning April 15th 2026, budget $200",
        "expect_status": "READY_TO_PROCESS",
    },
    {
        "label": "completely nonsensical input",
        "input": "purple elephants dancing on the ceiling with spaghetti hats",
        "expect_status": "NEEDS_CLARIFICATION",
        "expect_missing": [],
    },
    {
        "label": "request with no budget",
        "input": "Edmonton to New York City, March 20-24 2026, prefer evening departures",
        "expect_status": "NEEDS_CLARIFICATION",
        "expect_missing": ["budget_cad"],
    },
    {
        "label": "vague dates",
        "input": "fly from Toronto to Paris sometime this summer, budget $3000",
        "expect_status": "NEEDS_CLARIFICATION",
        "expect_missing": ["travel_dates"],
    },
]

DELIBERATE_FAILURES = [    {
        "label": "wrong model ID — expect ClientError",
        "input": "Edmonton to Vancouver March 14-17 budget $1000",
        "override_model": "anthropic.claude-WRONG-model-id",
    },
]

def check_result(result: dict, test: dict) -> bool:
    """Returns True if the result matches expectations."""
    expected_status = test.get("expect_status")
    expected_missing = test.get("expect_missing", [])

    got_status = result.get("status")
    got_missing = result.get("missing_fields", [])

    passed = True

    if expected_status and got_status != expected_status:
        print(f"  ✗ Expected status={expected_status}, got {got_status}")
        passed = False
    else:
        print(f"  ✓ Status: {got_status}")

    for field in expected_missing:
        if field not in got_missing:
            print(f"  ✗ Expected '{field}' in missing_fields, got {got_missing}")
            passed = False
        else:
            print(f"  ✓ '{field}' correctly flagged as missing")

    if test.get("note"):
        warning = result.get("budget_warning")
        if warning:
            print(f"  ✓ budget_warning present: {warning}")
        else:
            print(f"  ✗ Expected a budget_warning but got none")
            passed = False

    return passed


def run_batch(client: boto3.client, tests: list[dict], batch_name: str) -> tuple[int, int]:
    passed_count = 0
    total = len(tests)

    print(f"\n{'='*60}")
    print(f"  {batch_name.upper()} ({total} test{'s' if total != 1 else ''})")
    print(f"{'='*60}")

    for i, test in enumerate(tests, 1):
        print(f"\n[{i}/{total}] {test['label']}")

        if "override_model" in test:
            bad_model = test["override_model"]
            print(f"  Deliberately using wrong model ID: {bad_model}")
            try:
                response = client.converse(
                    modelId=bad_model,
                    messages=[{"role": "user", "content": [{"text": test["input"]}]}],
                    inferenceConfig={"maxTokens": 100},
                )
                print("  ✗ Expected an error but got a response — model ID was valid?")
            except ClientError as e:
                code = e.response["Error"]["Code"]
                message = e.response["Error"]["Message"]
                print(f"  ✓ Got expected ClientError: {code}")
                print(f"    Message: {message}")
                passed_count += 1
            time.sleep(3)
            continue

        for attempt in range(1, 4):
            try:
                result = extract_travel_intent(client, test["input"])
                print_result(test["input"], result)
                if check_result(result, test):
                    passed_count += 1
                break
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code == "ThrottlingException" and attempt < 3:
                    wait = 10 * attempt
                    print(f"  ⏳ ThrottlingException — waiting {wait}s before retry {attempt}/2...")
                    time.sleep(wait)
                else:
                    print(f"  ✗ Unexpected ClientError: {code} — {e.response['Error']['Message']}")
                    break
            except json.JSONDecodeError as e:
                print(f"  ✗ JSONDecodeError: {e}")
                break

        if i < total:
            time.sleep(3)

    return passed_count, total


def run_throttle_test(client: boto3.client) -> None:
    """20 rapid calls with no delay — intentional stress test to find the throttle limit."""
    print(f"\n{'='*60}")
    print("  THROTTLE TEST — 20 rapid calls")
    print(f"{'='*60}")
    input_text = "Edmonton to Vancouver March 14-17 budget $1000"
    for i in range(1, 21):
        try:
            result = extract_travel_intent(client, input_text)
            print(f"  Call {i:02d}: OK — status={result.get('status')}")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            print(f"  Call {i:02d}: {code} — {e.response['Error']['Message']}")
            if code == "ThrottlingException":
                print(f"  → Throttling began at call {i}. Stopping.")
                break

def main() -> None:
    filter_arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    client = build_bedrock_client()

    total_passed = 0
    total_run = 0

    if filter_arg in ("all", "happy"):
        p, t = run_batch(client, HAPPY_PATH, "Happy Path")
        total_passed += p
        total_run += t

    if filter_arg in ("all", "edge"):
        p, t = run_batch(client, EDGE_CASES, "Edge Cases")
        total_passed += p
        total_run += t

    if filter_arg in ("all", "failure"):
        p, t = run_batch(client, DELIBERATE_FAILURES, "Deliberate Failures")
        total_passed += p
        total_run += t

    if filter_arg == "throttle":
        run_throttle_test(client)
        return

    if filter_arg not in ("all", "happy", "edge", "failure", "throttle"):
        print(f"Unknown filter '{filter_arg}'. Use: happy | edge | failure | throttle | all")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  RESULTS: {total_passed}/{total_run} tests passed")
    print(f"{'='*60}\n")
    sys.exit(0 if total_passed == total_run else 1)


if __name__ == "__main__":
    main()
