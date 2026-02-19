"""
Travel intent extraction using AWS Bedrock Converse API.

Parses a natural language travel request and returns structured preferences,
or flags exactly what information is missing before processing can begin.
"""

import json
import re
import sys
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

MODEL_ID = "us.anthropic.claude-3-haiku-20240307-v1:0"

SYSTEM_PROMPT = """You are a travel request parser. Your job is to extract structured travel preferences from natural language input and return ONLY valid JSON — no prose, no markdown, no explanation.

Extract the following fields:

REQUIRED:
- origin_city: The departure city or airport. Must be explicitly stated — do NOT infer from context, location, or prior knowledge.
- destination: The arrival city, region, or country. Must be specific enough for an API search. "somewhere warm" or "a beach" is NOT acceptable.
- travel_dates: An object with "departure" and "return" in YYYY-MM-DD format. Both must be clearly inferable from the request. Flag if ambiguous or contradictory.
- budget_cad: A numeric value in CAD. If given in another currency, convert using approximate rates. If a range is given, use the upper bound.

OPTIONAL (capture if present, null if absent):
- departure_time_preference: "morning" (06:00–10:00), "afternoon" (10:00–17:00), "evening" (17:00+), or null
- traveller_count: integer, default 1 if not stated
- activity_preferences: array of strings (e.g. ["hiking", "Japanese food"])
- accommodation_preference: e.g. "hotel", "Airbnb", "hostel", or null
- notes: any other relevant context from the request

VALIDATION RULES:
1. If origin_city is not explicitly stated, set it to null and add "origin_city" to missing_fields.
2. If destination is vague or non-specific (e.g. "somewhere warm", "a beach", "somewhere cheap"), set it to null and add "destination" to missing_fields.
3. Travel dates must be specific enough to pass to a flight search API (exact dates or a named long weekend that resolves to specific dates). Vague phrases like "next week", "sometime this summer", "in March" without a specific range, or "soon" are NOT acceptable — set travel_dates to null and add "travel_dates" to missing_fields.
4. If budget_cad is absent from the request entirely, set it to null and add "budget_cad" to missing_fields. NEVER set budget_cad to null if a dollar amount was stated — even if it seems unrealistically low. $200 stated = budget_cad: 200. $50 stated = budget_cad: 50. The number the user says is what gets extracted, always.
5. If dates are contradictory (e.g. two different stated departure dates, or a departure date that is after the return date), set travel_dates to null, add "travel_dates" to missing_fields, and explain the conflict in the notes field.
6. If budget appears unrealistically low for the stated trip (e.g. under $500 CAD for international flights), still extract the stated amount into budget_cad but set budget_warning to a brief explanation.
7. If the input is not a travel request at all, set all fields to null and add "not_a_travel_request" to missing_fields.

STATUS RULE — THIS IS ABSOLUTE:
- Set status to "READY_TO_PROCESS" if and only if missing_fields is an empty array AND all four required fields (origin_city, destination, travel_dates, budget_cad) are non-null in extracted.
- Set status to "NEEDS_CLARIFICATION" in every other case — including when a required field is null even if missing_fields was accidentally left empty.
- A non-null budget_warning does NOT affect status. A warning is informational only.

CRITICAL SELF-CHECK — you must evaluate each condition before writing the JSON:

  CONDITION A: Is origin_city set to a non-null string in your extracted object?
    → If NO: missing_fields must include "origin_city" AND status must be "NEEDS_CLARIFICATION"

  CONDITION B: Is destination set to a non-null specific searchable city/country?
    → If NO: missing_fields must include "destination" AND status must be "NEEDS_CLARIFICATION"

  CONDITION C: Are BOTH travel_dates.departure AND travel_dates.return non-null YYYY-MM-DD strings?
    → If NO: missing_fields must include "travel_dates" AND status must be "NEEDS_CLARIFICATION"

  CONDITION D: Is budget_cad set to a non-null number?
    → If NO: missing_fields must include "budget_cad" AND status must be "NEEDS_CLARIFICATION"

  status = "READY_TO_PROCESS" ONLY when conditions A, B, C, and D are ALL true simultaneously.
  This check overrides any other reasoning. There are no exceptions.

BUDGET EXTRACTION RULE — READ THIS CAREFULLY:
  budget_cad must equal the exact number the user stated, regardless of whether you think it is realistic.
  If the user says "$200" → budget_cad: 200
  If the user says "$50" → budget_cad: 50
  If the user says "$1" → budget_cad: 1
  You are not permitted to set budget_cad to null when a dollar amount was explicitly stated.
  Use budget_warning to flag an unrealistic amount. Never use null.

Return this exact JSON shape:
{
  "status": "READY_TO_PROCESS" | "NEEDS_CLARIFICATION",
  "missing_fields": [],
  "extracted": {
    "origin_city": ...,
    "destination": ...,
    "travel_dates": { "departure": ..., "return": ... },
    "budget_cad": ...,
    "departure_time_preference": ...,
    "traveller_count": ...,
    "activity_preferences": [...],
    "accommodation_preference": ...,
    "notes": ...
  },
  "budget_warning": null | "string explaining the concern",
  "clarification_needed": "one sentence explaining what is missing, or null if READY_TO_PROCESS"
}"""


def build_bedrock_client() -> boto3.client:
    config = Config(
        region_name="us-east-1",
        retries={"mode": "adaptive", "max_attempts": 5},
    )
    return boto3.client("bedrock-runtime", config=config)


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (``` and ~~~) if the model wraps JSON despite instructions."""
    text = re.sub(r'^```[a-z]*\s*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^~~~[a-z]*\s*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n?\s*```\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n?\s*~~~\s*$', '', text, flags=re.MULTILINE)
    return text.strip()


def _enforce_status_rules(result: dict) -> dict:
    """Enforce status, missing_fields, and budget_warning rules in code rather than trusting model output."""
    extracted = result.get("extracted", {})
    missing = list(result.get("missing_fields", []))

    if not extracted.get("origin_city"):
        missing.append("origin_city")
    if not extracted.get("destination"):
        missing.append("destination")
    if not extracted.get("budget_cad") and extracted.get("budget_cad") != 0:
        missing.append("budget_cad")

    travel_dates = extracted.get("travel_dates")
    travel_dates_valid = (
        isinstance(travel_dates, dict)
        and bool(travel_dates.get("departure"))
        and bool(travel_dates.get("return"))
    )
    if not travel_dates_valid:
        missing.append("travel_dates")

    result["missing_fields"] = list(dict.fromkeys(missing))

    if result["missing_fields"]:
        result["status"] = "NEEDS_CLARIFICATION"
        if not result.get("clarification_needed"):
            result["clarification_needed"] = (
                f"Missing required fields: {', '.join(result['missing_fields'])}"
            )
    else:
        result["status"] = "READY_TO_PROCESS"
        result["clarification_needed"] = None

    return result


def extract_travel_intent(client: boto3.client, user_input: str) -> dict:
    """Call the Bedrock Converse API and return the parsed extraction result."""
    response = client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [{"text": user_input}],
            }
        ],
        inferenceConfig={
            "maxTokens": 1024,
            "temperature": 0,  # deterministic — extraction not generation
        },
    )

    raw_text = response["output"]["message"]["content"][0]["text"].strip()
    token_usage = response.get("usage", {})

    raw_text = _strip_markdown_fences(raw_text)

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(
            json.dumps({"error": "JSONDecodeError", "message": str(e), "raw_response": raw_text}),
            file=sys.stderr,
        )
        raise

    parsed["_meta"] = {
        "input_tokens": token_usage.get("inputTokens"),
        "output_tokens": token_usage.get("outputTokens"),
        "model": MODEL_ID,
    }

    return _enforce_status_rules(parsed)


def print_result(user_input: str, result: dict) -> None:
    """Print a formatted verdict for one request."""
    status = result.get("status", "UNKNOWN")
    extracted = result.get("extracted", {})
    missing = result.get("missing_fields", [])
    warning = result.get("budget_warning")
    clarification = result.get("clarification_needed")
    meta = result.get("_meta", {})

    separator = "─" * 60
    print(f"\n{separator}")
    print(f"INPUT:  {user_input}")
    print(f"STATUS: {status}")

    if status == "READY_TO_PROCESS":
        print(f"  Origin      : {extracted.get('origin_city')}")
        print(f"  Destination : {extracted.get('destination')}")
        dates = extracted.get("travel_dates") or {}
        print(f"  Departure   : {dates.get('departure')}")
        print(f"  Return      : {dates.get('return')}")
        print(f"  Budget (CAD): {extracted.get('budget_cad')}")
        print(f"  Depart time : {extracted.get('departure_time_preference')}")
        print(f"  Travellers  : {extracted.get('traveller_count')}")
        print(f"  Activities  : {extracted.get('activity_preferences')}")
        if warning:
            print(f"  ⚠ BUDGET WARNING: {warning}")
    else:
        print(f"  Missing     : {', '.join(missing)}")
        if clarification:
            print(f"  Action      : {clarification}")

    print(
        f"  Tokens      : {meta.get('input_tokens')} in "
        f"/ {meta.get('output_tokens')} out"
    )


def main() -> None:
    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
    else:
        print("Enter your travel request (or Ctrl+C to quit):")
        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)

    if not user_input:
        print("No input provided.")
        sys.exit(1)

    client = build_bedrock_client()

    try:
        result = extract_travel_intent(client, user_input)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        message = e.response["Error"]["Message"]
        print(json.dumps({"error": code, "message": message}), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        sys.exit(1)

    print_result(user_input, result)

    print("\nRAW JSON:")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
