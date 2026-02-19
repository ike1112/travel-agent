
import json

def lambda_handler(event, context):
    print("stub-error-handler: handling error...")
    
    # Log the error details (event usually contains cause)
    print(f"Error cause: {json.dumps(event)}")
    
    return {
        "status": "FAILED",
        "error": "Generic error handled",
        "original_cause": "See logs"
    }
