
import boto3
import json
import time
import sys
import hashlib
import datetime
from botocore.exceptions import ClientError

# Configuration
STACK_NAME = "TravelAgentIngressStack"
REGION = "us-east-1"

# Clients
cf = boto3.client("cloudformation", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
xray = boto3.client("xray", region_name=REGION)
dynamodb_resource = boto3.resource("dynamodb", region_name=REGION)

def get_lambda_function_name():
    """Retrieve the physical ID of the Broker Lambda from the CloudFormation stack."""
    print(f"🔍 Finding Lambda function in stack '{STACK_NAME}'...")
    try:
        resources = cf.list_stack_resources(StackName=STACK_NAME)
        for r in resources["StackResourceSummaries"]:
            if r["LogicalResourceId"].startswith("BrokerLambda") and r["ResourceType"] == "AWS::Lambda::Function":
                func_name = r["PhysicalResourceId"]
                print(f"   Found Lambda: {func_name}")
                return func_name
        raise Exception("BrokerLambda not found in stack resources.")
    except Exception as e:
        print(f"❌ Error finding lambda: {e}")
        sys.exit(1)

def invoke_lambda(func_name, payload):
    """Invoke the Lambda and return the response payload and logs."""
    try:
        response = lambda_client.invoke(
            FunctionName=func_name,
            InvocationType="RequestResponse",
            LogType="Tail",
            Payload=json.dumps(payload)
        )
        result = json.loads(response["Payload"].read())
        # Decode base64 logs if needed, but we rely on the result mostly
        return result, response
    except ClientError as e:
        print(f"   Invoke failed: {e}")
        return None, None

def test_happy_path(func_name):
    print("\n🧪 TEST 1: Happy Path (Valid Request)")
    payload = {"input": "I want to fly from Edmonton to Paris next week, budget $2000"}
    
    start = time.time()
    result, _ = invoke_lambda(func_name, payload)
    duration = time.time() - start
    
    if result and result.get("statusCode") == 200:
        body = json.loads(result["body"])
        status = body.get("status")
        print(f"   ✅ Success! Status: {status}")
        print(f"      Extracted: {body.get('extracted', {}).get('destination')}")
        print(f"      Time: {duration:.2f}s")
    else:
        print(f"   ❌ Failed. Result: {result}")

def test_idempotency(func_name):
    print("\n🧪 TEST 2: Idempotency (Duplicate Submission)")
    payload = {"input": "I want to fly from Edmonton to Paris next week, budget $2000"}
    
    print("   Invoking again (should be instant cache hit)...")
    start = time.time()
    result, _ = invoke_lambda(func_name, payload)
    duration = time.time() - start
    
    if result and result.get("statusCode") == 200:
        print(f"   ✅ Success! Response returned in {duration:.2f}s")
        if duration < 1.0:
            print("      🚀 Fast response implies cache hit!")
        else:
            print("      ⚠️ Response took > 1s, might not be a cache hit (or cold start overhead).")
    else:
        print(f"   ❌ Failed. Result: {result}")

def test_bad_config(func_name):
    print("\n🧪 TEST 3: Deliberate Failure (Bad DynamoDB Table Name)")
    
    # Get current config
    config = lambda_client.get_function_configuration(FunctionName=func_name)
    original_env = config["Environment"]["Variables"]
    original_table = original_env["REQUEST_TABLE_NAME"]
    
    print(f"   Current Table: {original_table}")
    print("   👉 Updating Lambda config to use invalid table name 'non-existent-table'...")
    
    try:
        # Break it
        lambda_client.update_function_configuration(
            FunctionName=func_name,
            Environment={"Variables": {**original_env, "REQUEST_TABLE_NAME": "non-existent-table"}}
        )
        
        # Wait for update
        time.sleep(5) 
        
        # Invoke
        print("   Invoking Lambda (expecting error)...")
        payload = {"input": "New request that forces a write"}
        result, _ = invoke_lambda(func_name, payload)
        
        if result and result.get("statusCode") == 200:
            print("   ✅ Success! Lambda gracefully handled DynamoDB failure and returned result.")
            print(f"      Body preview: {result.get('body')[:50]}...")
        else:
            print(f"   ❌ Unexpected result (expected 200, got {result.get('statusCode')}): {result}")
            
    except Exception as e:
        print(f"   ❌ Error performing destructive test: {e}")
        
    finally:
        print("   🔄 Reverting Lambda configuration...")
        lambda_client.update_function_configuration(
            FunctionName=func_name,
            Environment={"Variables": {**original_env, "REQUEST_TABLE_NAME": original_table}}
        )
        time.sleep(5)
        print("   ✅ Configuration reverted.")

def verify_dynamodb_count(func_name):
    print("\n🧪 TEST 4: Verify Duplicate Record Count")
    
    # Get table name from Lambda config
    config = lambda_client.get_function_configuration(FunctionName=func_name)
    table_name = config["Environment"]["Variables"]["REQUEST_TABLE_NAME"]
    
    table = dynamodb_resource.Table(table_name)
    
    # scan for our test input hash
    # Hash for "I want to fly from Edmonton to Paris next week, budget $2000"
    test_input = "I want to fly from Edmonton to Paris next week, budget $2000"
    request_hash = hashlib.sha256(test_input.encode("utf-8")).hexdigest()
    
    response = table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("requestId").eq(request_hash)
    )
    
    count = response["Count"]
    if count == 1:
        print(f"   ✅ Success! Found exactly 1 record for request hash: {request_hash}")
    else:
        print(f"   ❌ Failed. Found {count} records (expected 1).")

def verify_xray_traces():
    print("\n🧪 TEST 5: Verify X-Ray Traces")
    
    # Get traces from last 5 minutes
    end_time = datetime.datetime.now()
    start_time = end_time - datetime.timedelta(minutes=5)
    
    try:
        response = xray.get_trace_summaries(
            StartTime=start_time,
            EndTime=end_time,
            FilterExpression='service("BrokerLambda")'
        )
        
        traces = response.get("TraceSummaries", [])
        if traces:
            print(f"   ✅ Success! Found {len(traces)} traces in the last 5 minutes.")
            print(f"      Latest Trace ID: {traces[0]['Id']}")
            print(f"      Duration: {traces[0]['Duration']}s")
        else:
            print("   ⚠️ Warning: No traces found yet. X-Ray might have propagation delay (usually ~30s).")
            
    except Exception as e:
        print(f"   ❌ Error fetching traces: {e}")

def main():
    func_name = get_lambda_function_name()
    test_happy_path(func_name)
    test_idempotency(func_name)
    test_bad_config(func_name)
    verify_dynamodb_count(func_name)
    verify_xray_traces()

if __name__ == "__main__":
    main()
