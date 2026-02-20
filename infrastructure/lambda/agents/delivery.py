
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("REQUEST_TABLE_NAME")

def lambda_handler(event, context):
    print("stub-delivery-agent: email sent!")
    
    request_id = event.get("requestId")
    if request_id and TABLE_NAME:
        try:
            table = dynamodb.Table(TABLE_NAME)
            
            # Prepare update expression
            update_expr = "SET #s = :status, delivery_timestamp = :ts"
            expr_values = {
                ":status": "COMPLETED",
                ":ts": datetime.now().isoformat()
            }
            expr_names = {"#s": "status"}

            # Save narrative if present
            narrative = event.get("narrative")
            if narrative:
                update_expr += ", narrative = :narrative"
                expr_values[":narrative"] = narrative

            table.update_item(
                Key={"requestId": request_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values
            )
            print(f"Updated DynamoDB status for {request_id}")
        except Exception as e:
            print(f"Error updating DynamoDB: {e}")

    return {
        "status": "DELIVERED",
        "email_sent_to": "user@example.com",
        "requestId": request_id
    }
