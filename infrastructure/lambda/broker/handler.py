
import json
import logging
import os
import uuid
import boto3
import hashlib
from botocore.config import Config
from botocore.exceptions import ClientError
from datetime import datetime, timedelta

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
REQUEST_TABLE_NAME = os.environ.get("REQUEST_TABLE_NAME")
MODEL_ID = "us.anthropic.claude-3-haiku-20240307-v1:0"

# Setup Logger
logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)

# AWS Clients
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(REQUEST_TABLE_NAME)

bedrock_config = Config(
    region_name="us-east-1",
    retries={"mode": "adaptive", "max_attempts": 5}
)
bedrock_client = boto3.client("bedrock-runtime", config=bedrock_config)

# System Prompt (copied from script)
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
3. Travel dates must be specific enough to pass to a flight search API. You must NOT infer specific dates from seasons (e.g. "summer", "winter") or vague timeframes. If vague, set logic to null and add "travel_dates" to missing_fields.
4. If budget_cad is absent entirely, set it to null and add "budget_cad" to missing_fields. Never infer null if a dollar amount was stated.
5. If dates are contradictory, set travel_dates to null, add "travel_dates" to missing_fields.
6. If budget appears ridiculously low, set budget_warning but keep the stated amount.
7. If the input is not a travel request at all, set all fields to null and add "not_a_travel_request" to missing_fields.

STATUS RULE — THIS IS ABSOLUTE:
- Set status to "READY_TO_PROCESS" if and only if missing_fields is an empty array AND all four required fields (origin_city, destination, travel_dates, budget_cad) are non-null in extracted.
- Set status to "NEEDS_CLARIFICATION" in every other case.

CRITICAL SELF-CHECK — you must evaluate each condition before writing the JSON:
  CONDITION A: Is origin_city set to a non-null string in your extracted object?
    → If NO: missing_fields must include "origin_city" AND status must be "NEEDS_CLARIFICATION"
  CONDITION B: Is destination set to a non-null specific searchable city/country?
    → If NO: missing_fields must include "destination" AND status must be "NEEDS_CLARIFICATION"
  CONDITION C: Are BOTH travel_dates.departure AND travel_dates.return non-null YYYY-MM-DD strings derived from EXPLICIT user input?
    → If NO: missing_fields must include "travel_dates" AND status must be "NEEDS_CLARIFICATION"
  CONDITION D: Is budget_cad set to a non-null number?
    → If NO: missing_fields must include "budget_cad" AND status must be "NEEDS_CLARIFICATION"

  status = "READY_TO_PROCESS" ONLY when conditions A, B, C, and D are ALL true simultaneously.

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

def _strip_markdown_fences(text):
    text = text.replace("```json", "").replace("```", "")
    return text.strip()

def _enforce_status_rules(result):
    extracted = result.get("extracted", {})
    missing = list(result.get("missing_fields", []))

    if not extracted.get("origin_city"): missing.append("origin_city")
    if not extracted.get("destination"): missing.append("destination")
    if not extracted.get("budget_cad") and extracted.get("budget_cad") != 0: missing.append("budget_cad")

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

def extract_travel_intent(user_input):
    response = bedrock_client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_input}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0}
    )
    
    raw_text = response["output"]["message"]["content"][0]["text"].strip()
    token_usage = response.get("usage", {})
    
    raw_text = _strip_markdown_fences(raw_text)
    
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback error if JSON fails
        parsed = {
             "status": "NEEDS_CLARIFICATION",
             "missing_fields": ["parsing_error"],
             "extracted": {},
             "budget_warning": None,
             "clarification_needed": "System error: Could not parse intent."
        }
    
    parsed["_meta"] = {
        "input_tokens": token_usage.get("inputTokens"),
        "output_tokens": token_usage.get("outputTokens"),
        "model": MODEL_ID
    }
    
    return _enforce_status_rules(parsed)

def lambda_handler(event, context):
    # Default correlation set
    correlation_id = str(uuid.uuid4())
    
    # Check for EventBridge invocation
    if "detail" in event:
        user_input = event["detail"].get("input")
        correlation_id = event["detail"].get("correlationId", correlation_id)
        # We can also use the requestId passed from Intake
        request_id_from_intake = event["detail"].get("requestId")
    
    # Handle direct invocation or API Gateway Proxy (Legacy/Test)
    elif "body" in event:
        try:
            body = json.loads(event["body"])
            user_input = body.get("input")
        except:
            user_input = event.get("body")
    else:
        user_input = event.get("input")
    
    if user_input == "FORCE_CRASH":
        raise Exception("Features: FORCE_CRASH - Deliberate Failure for DLQ Test")

    if not user_input:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "No input provided", "correlationId": correlation_id})
        }

    # Structured Logging
    log_context = {
        "correlationId": correlation_id,
        "phase": "broker_lambda",
        "requestId": context.aws_request_id
    }
    logger.info(json.dumps({**log_context, "status": "start", "input_length": len(user_input)}))

    # Idempotency Check
    # Use request_id from intake if available, otherwise hash input
    if 'request_id_from_intake' in locals() and request_id_from_intake:
        request_hash = request_id_from_intake
    else:
        request_hash = hashlib.sha256(user_input.encode("utf-8")).hexdigest()
    
    try:
        existing_item = table.get_item(Key={"requestId": request_hash})
        if "Item" in existing_item:
            logger.info(json.dumps({**log_context, "status": "cache_hit", "request_hash": request_hash}))
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(existing_item["Item"]["result"])
            }
    except ClientError as e:
        # If DynamoDB fails (e.g. bad table name), we log it but PROCEED to Bedrock
        # We do not want a cache failure to kill the service
        logger.error(json.dumps({**log_context, "status": "dynamodb_read_error", "error": str(e)}))

    # Bedrock Call
    start_time = datetime.now()
    try:
        result = extract_travel_intent(user_input)
        duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        logger.info(json.dumps({**log_context, "status": "bedrock_success", "duration_ms": duration_ms}))

        # Write to DynamoDB (Best Effort)
        try:
            ttl = int((datetime.now() + timedelta(days=30)).timestamp())
            table.put_item(
                Item={
                    "requestId": request_hash,
                    "result": result,
                    "ttl": ttl,
                    "correlationId": correlation_id,
                    "timestamp": datetime.now().isoformat()
                }
            )
        except ClientError as e:
             logger.error(json.dumps({**log_context, "status": "dynamodb_write_error", "error": str(e)}))
        
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result)
        }

    except Exception as e:
        logger.error(json.dumps({**log_context, "status": "error", "error": str(e)}))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal Server Error", "correlationId": correlation_id})
        }
