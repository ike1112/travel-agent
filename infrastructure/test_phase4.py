
import boto3
import json
import time
import requests
import sys
from botocore.exceptions import ClientError

# Configuration
STACK_NAME = "TravelAgentIngressStack"
WORKFLOW_STACK_NAME = "TravelAgentWorkflowStack" # Needed for table lookup? Maybe not.
REGION = "us-east-1"

# Clients
cf = boto3.client("cloudformation", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)

def get_stack_outputs():
    """Retrieve API URL."""
    print(f"🔍 Fetching stack outputs for '{STACK_NAME}'...")
    try:
        response = cf.describe_stacks(StackName=STACK_NAME)
        outputs = response["Stacks"][0]["Outputs"]
        api_url = None
        for o in outputs:
            if "execute-api" in o["OutputValue"]:
                api_url = o["OutputValue"]
                break
        if not api_url:
            # Fallback
            resources = cf.list_stack_resources(StackName=STACK_NAME)
            for r in resources["StackResourceSummaries"]:
                if r["ResourceType"] == "AWS::ApiGateway::RestApi":
                     api_url = f"https://{r['PhysicalResourceId']}.execute-api.{REGION}.amazonaws.com/prod/"
                     break
        return api_url
    except Exception as e:
        print(f"❌ Error fetching stack outputs: {e}")
        sys.exit(1)

def get_table_name():
    # Table moved to WorkflowStack!
    try:
        # Check WorkflowStack first
        resources = cf.list_stack_resources(StackName=WORKFLOW_STACK_NAME)
        for r in resources["StackResourceSummaries"]:
            if r["LogicalResourceId"].startswith("TravelRequestLog"):
                return r["PhysicalResourceId"]
                
        # Fallback to IngressStack (if not moved yet?)
        resources = cf.list_stack_resources(StackName=STACK_NAME)
        for r in resources["StackResourceSummaries"]:
            if r["LogicalResourceId"].startswith("TravelRequestLog"):
                return r["PhysicalResourceId"]
                
    except Exception as e:
        print(f"❌ Error fetching table name: {e}")
        # sys.exit(1)
    return None

API_URL = get_stack_outputs()
TABLE_NAME = get_table_name()

def check_workflow_completion(request_id):
    print(f"   ⏳ Polling DynamoDB for completion status (max 90s)...")
    start_time = time.time()
    table = dynamodb.Table(TABLE_NAME)
    
    while time.time() - start_time < 90:
        try:
            response = table.get_item(Key={"requestId": request_id})
            item = response.get("Item")
            
            if item:
                status = item.get("status")
                # print(f"      Status: {status}") # Too verbose?
                if status == "COMPLETED":
                    print(f"   ✅ Workflow COMPLETED successfully!")
                    print(f"      Delivery Timestamp: {item.get('delivery_timestamp')}")
                    return True
                elif status == "FAILED":
                     print(f"   ❌ Workflow FAILED according to DynamoDB.")
                     return False
        except Exception as e:
            print(f"Error checking DynamoDB: {e}")
                 
        time.sleep(5)
    
    print("   ❌ Timeout waiting for completion.")
    return False

def test_full_workflow():
    print("\n🧪 TEST 4: Full Orchestration (API -> Broker -> SFN -> Agents -> DynamoDB Update)")
    
    unique_suffix = int(time.time())
    # Explicitly asking for a complex trip to trigger all agents
    payload = {"input": f"I want to fly from Edmonton to London next week for theater, budget $5000. Ref: {unique_suffix}"}
    
    # 1. Submit Request
    url = f"{API_URL}travel"
    print(f"   POST {url}")
    response = requests.post(url, json=payload)
    
    if response.status_code != 202:
        print(f"   ❌ API Request Failed: {response.status_code}")
        return

    request_id = response.json()["requestId"]
    print(f"   ✅ Request Accepted. Request ID: {request_id}")

    # 2. Poll DynamoDB for Execution ARN (to confirm start)
    print(f"   ⏳ Looking for execution ARN in DynamoDB...")
    if not TABLE_NAME:
        print("❌ Table Name not found.")
        return
        
    table = dynamodb.Table(TABLE_NAME)
    execution_arn = None
    
    start_time = time.time()
    while time.time() - start_time < 60:
        item = table.get_item(Key={"requestId": request_id}).get("Item")
        if item and "executionArn" in item:
            execution_arn = item["executionArn"]
            print(f"   ✅ Found Execution ARN: {execution_arn}")
            break
        time.sleep(2)
    
    if not execution_arn:
        print("   ❌ Timeout waiting for execution ARN. Workflow did not start?")
        return

    # 3. Verify Workflow Completion via DynamoDB Status Update
    check_workflow_completion(request_id)

if __name__ == "__main__":
    if not API_URL:
        print("❌ Missing API URL configuration.")
        sys.exit(1)
    if not TABLE_NAME:
         print("⚠️ Missing Table Name configuration. Polling might fail.")
    test_full_workflow()
