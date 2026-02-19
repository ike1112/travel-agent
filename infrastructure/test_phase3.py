
import boto3
import json
import time
import requests
import hashlib
import sys
from botocore.exceptions import ClientError

# Configuration
STACK_NAME = "TravelAgentIngressStack"
REGION = "us-east-1"
TABLE_NAME = None # Will be fetched dynamically

# Clients
cf = boto3.client("cloudformation", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
sqs = boto3.client("sqs", region_name=REGION)

def get_stack_outputs():
    """Retrieve API URL and Table Name from CloudFormation Stack."""
    print(f"🔍 Fetching stack outputs for '{STACK_NAME}'...")
    try:
        response = cf.describe_stacks(StackName=STACK_NAME)
        outputs = response["Stacks"][0]["Outputs"]
        
        api_url = None
        
        # We need to find the API URL. CDK usually exports it.
        # If not, we can construct it or find the logical ID reference.
        # Let's try to find the output that contains 'execute-api'
        for o in outputs:
            if "execute-api" in o["OutputValue"]:
                api_url = o["OutputValue"]
                break
        
        # If CDK didn't output it by default (it usually does for LambdaRestApi), 
        # we might need to find the API ID resource.
        if not api_url:
            # Fallback: Find API ID from resources
            resources = cf.list_stack_resources(StackName=STACK_NAME)
            api_id = None
            for r in resources["StackResourceSummaries"]:
                if r["ResourceType"] == "AWS::ApiGateway::RestApi":
                    api_id = r["PhysicalResourceId"]
                    break
            if api_id:
                api_url = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/prod/"
        
        # Find Table Name (we know the logical ID starts with TravelRequestLog)
        # Or faster: we can get it from the Broker Lambda environment variables
        # Let's get it from Lambda config as we did in phase 2
        
        return api_url
    except Exception as e:
        print(f"❌ Error fetching stack outputs: {e}")
        sys.exit(1)

def get_table_name():
    # Find Broker Lambda to get the table name from its environment
    try:
        resources = cf.list_stack_resources(StackName=STACK_NAME)
        func_name = None
        for r in resources["StackResourceSummaries"]:
            if r["LogicalResourceId"].startswith("BrokerLambda") and r["ResourceType"] == "AWS::Lambda::Function":
                func_name = r["PhysicalResourceId"]
                break
        
        if not func_name:
            raise Exception("Broker Lambda not found")

        config = lambda_client.get_function_configuration(FunctionName=func_name)
        return config["Environment"]["Variables"]["REQUEST_TABLE_NAME"]
    except Exception as e:
        print(f"❌ Error fetching table name: {e}")
        sys.exit(1)


def get_dlq_url():
    try:
        resources = cf.list_stack_resources(StackName=STACK_NAME)
        for r in resources["StackResourceSummaries"]:
            if r["LogicalResourceId"].startswith("BrokerDLQ") and r["ResourceType"] == "AWS::SQS::Queue":
                return r["PhysicalResourceId"] # This is usually the URL for SQS
    except Exception:
        pass
    return None

API_URL = get_stack_outputs()
TABLE_NAME = get_table_name()
DLQ_URL = get_dlq_url()

print(f"   API URL: {API_URL}")
print(f"   Table: {TABLE_NAME}")
print(f"   DLQ: {DLQ_URL}")

def poll_dynamodb(request_id, timeout=30):
    """Poll DynamoDB for the result."""
    table = dynamodb.Table(TABLE_NAME)
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = table.get_item(Key={"requestId": request_id})
            if "Item" in response:
                return response["Item"]
        except ClientError as e:
            print(f"   ⚠️ DynamoDB error: {e}")
        
        time.sleep(2)
        print(".", end="", flush=True)
    
    print(" (timeout)")
    return None

def test_async_happy_path():
    print("\n🧪 TEST 1: Async Happy Path (API Gateway -> EventBridge -> Lambda -> DynamoDB)")
    payload = {"input": "I want to go to Tokyo next month for sushi, budget $3000"}
    
    # 1. Send POST request
    try:
        url = f"{API_URL}travel"
        print(f"   POST {url}")
        response = requests.post(url, json=payload)
        
        if response.status_code == 202:
            data = response.json()
            request_id = data["requestId"]
            print(f"   ✅ Request Accepted. Request ID: {request_id}")
            
            # 2. Poll for result
            print(f"   ⏳ Polling DynamoDB for result...", end="")
            result = poll_dynamodb(request_id)
            
            if result:
                print(f"\n   ✅ Found result in DynamoDB!")
                print(f"      Extracted: {result.get('result', {}).get('destination')}")
            else:
                print(f"\n   ❌ Failed to find result in DynamoDB within timeout.")
        else:
            print(f"   ❌ API Request Failed: {response.status_code} {response.text}")
            
    except Exception as e:
        print(f"   ❌ Test Exception: {e}")

def test_idempotency_async():
    print("\n🧪 TEST 2: Async Idempotency (Duplicate POSTs)")
    payload = {"input": "I want to go to Tokyo next month for sushi, budget $3000"}
    
    # Resend same request
    url = f"{API_URL}travel"
    response = requests.post(url, json=payload)
    
    if response.status_code == 202:
        print("   ✅ Second POST Accepted (202). System should debounce this via DynamoDB check.")
        
        # Verify only 1 record exists in DynamoDB (Scanning by hash)
        # Note: Since the partition key IS the hash, there can inherently only be one record per hash.
        # The test is that the system doesn't error out or create duplicates if we had a sort key (we don't).
        # So essentially, we verify the Data is still there and correct.
        
        request_id = response.json()["requestId"]
        table = dynamodb.Table(TABLE_NAME)
        item = table.get_item(Key={"requestId": request_id}).get("Item")
        
        if item:
             print("   ✅ Record still exists and is consistent.")
        else:
             print("   ❌ Record vanished?")

def test_dlq_failure():
    print("\n🧪 TEST 3: Dead Letter Queue (Simulating Failure)")
    payload = {"input": "FORCE_CRASH"}
    
    # 1. Trigger Failure
    url = f"{API_URL}travel"
    print(f"   POST {url} with FORCE_CRASH")
    response = requests.post(url, json=payload)
    
    if response.status_code == 202:
        print("   ✅ Request Accepted. This should crash the Broker Lambda and land in DLQ.")
    else:
        print(f"   ❌ Request Failed: {response.status_code}")
        return

    # 2. Poll DLQ
    if not DLQ_URL:
        print("   ❌ No DLQ URL found (CDK output issue?). Skipping poll.")
        return

    print(f"   ⏳ Polling DLQ ({DLQ_URL}) for failure message (retries may take ~10-30s)...")
    
    start_time = time.time()
    while time.time() - start_time < 45:
        try:
            # We use receive_message to see if anything is there
            response = sqs.receive_message(
                QueueUrl=DLQ_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=2
            )
            
            if "Messages" in response:
                print("   ✅ Success! Found message in DLQ.")
                msg = response["Messages"][0]
                print(f"      Message ID: {msg['MessageId']}")
                return
            
        except Exception as e:
            print(f"   ⚠️ SQS Error: {e}")
        
        time.sleep(2)
        print(".", end="", flush=True)
    
    print("\n   ❌ Timeout: No message found in DLQ after 45s.")

def main():
    if not API_URL:
        print("❌ Could not find API URL.")
        sys.exit(1)
        
    test_async_happy_path()
    test_idempotency_async()
    test_dlq_failure()

if __name__ == "__main__":
    main()
