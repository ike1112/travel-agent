import json
import boto3
import os
from botocore.config import Config

# Client initialization outside handlers for cold start optimization
config = Config(read_timeout=15, connect_timeout=5, retries={"max_attempts": 2})
bedrock = boto3.client("bedrock-runtime", config=config)
MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

def parse_event_data(event):
    """Safely extracts all necessary data from the step function input."""
    # 1. Basic Identifiers
    request_id = event.get("requestId")
    user_intent = event.get("extracted", {})

    # 2. Flight Data
    # Flight output is usually merged into the event at root or under flight_output
    flight_data = event.get("flight_output", {})

    # 3. Parallel Branch Data (Hotel, Weather, Events)
    # Step Functions Parallel state output is a list in order of branches
    parallel_results = event.get("parallel_results", [])
    
    # Safely unpack with defaults if list is short/empty
    hotel_data = parallel_results[0] if len(parallel_results) > 0 else {}
    weather_data = parallel_results[1] if len(parallel_results) > 1 else {}
    events_data = parallel_results[2] if len(parallel_results) > 2 else {}

    return {
        "request_id": request_id,
        "intent": user_intent,
        "flights": flight_data,
        "hotels": hotel_data,
        "weather": weather_data,
        "events": events_data
    }

def construct_prompt(data):
    """Builds the prompt for the LLM."""
    return f"""
You are an expert travel agent. Your goal is to write a personalized, cohesive travel recommendation email.

User Request: {json.dumps(data['intent'], indent=2)}

RESEARCH DATA:

1. FLIGHT OPTIONS:
Source: {data['flights'].get('source', 'Unknown')}
{json.dumps(data['flights'].get('offers', []), indent=2)}

2. HOTELS:
Location: {data['hotels'].get('location', 'Unknown')}
{json.dumps(data['hotels'].get('hotels', []), indent=2)}

3. WEATHER FORECAST:
{data['weather'].get('summary', 'No weather data available.')}

4. LOCAL ACTIVITIES:
{json.dumps(data['events'].get('events', []), indent=2)}

INSTRUCTIONS:
Write a travel recommendation email structured as follows:

1. **Introduction**: Friendly opening acknowledging their specific request.
2. **Flights**: Recommend the best option(s). Explain WHY based on value/convenience.
3. **Accommodation**: Recommend the best hotel. Explain WHY it fits their preferences.
4. **The Plan**: Suggest a high-level itinerary that integrates the weather forecast with the specific activities found. 
   - Example: "Since Saturday is sunny (22°C), that's the perfect day for..."
5. **Curated Spots**: A quick bulleted list of 3-4 specific places/restaurants found in the research.

Tone: Professional, enthusiastic, and personalized. 
"""

def call_bedrock_converse(prompt):
    """Calls Bedrock Converse API."""
    try:
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}]
                }
            ],
            inferenceConfig={
                "maxTokens": 2000,
                "temperature": 0.7
            }
        )
        return response["output"]["message"]["content"][0]["text"]
        
    except Exception as e:
        print(f"Error calling Bedrock: {e}")
        # In production, you might want to re-raise or return a specific error structure.
        # For now, a fallback message ensures the workflow doesn't crash the delivery step.
        return f"Unable to generate narrative due to an internal error: {str(e)}"

def lambda_handler(event, context):
    print("Synthesis Agent: Processing...")
    
    # 1. Parse
    data = parse_event_data(event)
    
    # 2. Prompt
    prompt = construct_prompt(data)
    
    # 3. Generate
    narrative = call_bedrock_converse(prompt)
    
    print("Synthesis Agent: Narrative generated successfully.")
    
    return {
        "status": "success",
        "narrative": narrative,
        "requestId": data["request_id"]
    }
