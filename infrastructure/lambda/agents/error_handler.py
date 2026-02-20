import os
import boto3

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("REQUEST_TABLE_NAME")

def lambda_handler(event, context):
    print("error_handler: processing failure...")
    
    # Extract requestId if possible
    # We might need to pass it explicitly in the catch block result path?
    # Or assume it's in the input event (which is the original input + error info)
    # Step Functions Catch block: result_path="$.error" usually means the error object is added to the input.
    # So the input event contains the original input properties like "requestId".
    
    request_id = event.get("requestId")
    error_info = event.get("error", {})
    
    error_msg = str(error_info.get("Cause", "Unknown Error"))
    
    if request_id and TABLE_NAME:
        try:
            table = dynamodb.Table(TABLE_NAME)
            table.update_item(
                Key={"requestId": request_id},
                UpdateExpression="SET #s = :status, error = :err",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":status": "FAILED",
                    ":err": error_msg[:1000] # Truncate to avoid DynamoDB limits
                }
            )
            print(f"Updated DynamoDB status to FAILED for {request_id}")
        except Exception as e:
            print(f"Error updating DynamoDB: {e}")
            
    return {
        "status": "FAILED",
        "error": error_msg
    }
