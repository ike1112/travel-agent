
import json
import logging
import os
import boto3
import uuid
import hashlib
from datetime import datetime

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME")

# Setup Logger
logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)

# AWS Clients
events_client = boto3.client("events")

def lambda_handler(event, context):
    try:
        correlation_id = str(uuid.uuid4())
        
        # Parse Input
        if "body" in event:
            try:
                body = json.loads(event["body"])
                user_input = body.get("input")
            except:
                user_input = event.get("body")
        else:
            user_input = event.get("input")
            
        if not user_input:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "No input provided", "correlationId": correlation_id})
            }
            
        # Generate Request ID (Hash)
        request_id = hashlib.sha256(user_input.encode("utf-8")).hexdigest()
        
        # Log Structured Event
        log_context = {
            "correlationId": correlation_id,
            "requestId": request_id, 
            "phase": "intake_lambda"
        }
        logger.info(json.dumps({**log_context, "status": "received", "length": len(user_input)}))
        
        # Publish Event
        event_entry = {
            "Source": "com.travel.system",
            "DetailType": "TravelRequestSubmitted",
            "Detail": json.dumps({
                "requestId": request_id,
                "input": user_input,
                "correlationId": correlation_id,
                "timestamp": datetime.now().isoformat()
            }),
            "EventBusName": EVENT_BUS_NAME
        }
        
        response = events_client.put_events(Entries=[event_entry])
        
        if response["FailedEntryCount"] > 0:
            logger.error(json.dumps({**log_context, "status": "event_bridge_error", "response": response}))
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to queue request", "correlationId": correlation_id})
            }
            
        logger.info(json.dumps({**log_context, "status": "published", "event_id": response["Entries"][0]["EventId"]}))
        
        return {
            "statusCode": 202,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "message": "Request accepted",
                "requestId": request_id,
                "correlationId": correlation_id
            })
        }
        
    except Exception as e:
        logger.error(json.dumps({"status": "error", "error": str(e), "context": context.aws_request_id}))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal Server Error"})
        }
