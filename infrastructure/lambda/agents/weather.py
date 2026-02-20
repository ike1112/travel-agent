
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime

# Environment
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY")
OPENWEATHER_BASE_URL = "https://api.openweathermap.org/data/2.5/forecast"

def get_forecast(city):
    if not OPENWEATHER_API_KEY:
        return None
        
    params = {
        "q": city,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
        "cnt": 40 # 5 days usually
    }
    
    query = urllib.parse.urlencode(params)
    url = f"{OPENWEATHER_BASE_URL}?{query}"
    
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read())
    except Exception as e:
        print(f"Error getting weather: {e}")
        return None

def summarize_forecast(forecast_data):
    if not forecast_data:
        return "Weather data unavailable."
        
    city = forecast_data.get("city", {}).get("name", "Destination")
    list_items = forecast_data.get("list", [])
    
    # Simple summary: Avg temp of first few items
    temps = [item["main"]["temp"] for item in list_items[:8]] # First 24h roughly
    avg_temp = sum(temps) / len(temps) if temps else 0
    
    conditions = [item["weather"][0]["description"] for item in list_items[:8]]
    # Most common condition
    condition = max(set(conditions), key=conditions.count) if conditions else "unknown"
    
    return f"Expect around {avg_temp:.1f}°C with {condition} in {city}."

def lambda_handler(event, context):
    print("Weather Agent: Real Forecast...")
    
    intent = event.get("extracted", event)
    if not isinstance(intent, dict):
        intent = {}
        
    destination = intent.get("destination", "London")
    
    forecast = get_forecast(destination)
    summary = summarize_forecast(forecast)
    
    return {
        "source": "OpenWeatherMap",
        "summary": summary,
        "location": destination,
        "raw_temp": forecast["list"][0]["main"]["temp"] if forecast else None
    }
