
import json
import os
import urllib.request
import urllib.parse

# Environment
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")
PLACES_API_URL = "https://places.googleapis.com/v1/places:searchText"

def search_hotels(destination, budget_level=None):
    if not GOOGLE_PLACES_API_KEY:
        return []
        
    query = f"hotels in {destination}"
    # Basic logic: just search. Budget filtering strictly by API is hard without price range
    # mapped to priceLevel. We'll filter post-search if possible or just return general results.
    
    data = {
        "textQuery": query,
        "maxResultCount": 5
    }
    
    # Field Mask is critical for v1
    # priceLevel is Enum: PRICE_LEVEL_UNSPECIFIED, PRICE_LEVEL_FREE, PRICE_LEVEL_INEXPENSIVE, ...
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.priceLevel,places.rating,places.userRatingCount,places.websiteUri"
    }
    
    req = urllib.request.Request(PLACES_API_URL, data=json.dumps(data).encode(), headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read())
            return result.get("places", [])
    except Exception as e:
        print(f"Error searching hotels: {e}")
        return []

def lambda_handler(event, context):
    print("Hotel Agent: Real Search...")
    
    # Handle Input from Parallel state
    # Result of Flight step is merged into input, so `extracted` should still be at root.
    intent = event.get("extracted", event)
    if "input" in intent: # Handle nested input from stub logic if present
         intent = intent["input"]
         
    destination = intent.get("destination", "London")
    
    places = search_hotels(destination)
    
    simplified_hotels = []
    for p in places:
        simplified_hotels.append({
            "name": p.get("displayName", {}).get("text"),
            "address": p.get("formattedAddress"),
            "rating": p.get("rating"),
            "price_level": p.get("priceLevel", "UNKNOWN"),
            "website": p.get("websiteUri")
        })
        
    return {
        "source": "GooglePlaces",
        "hotels": simplified_hotels,
        "location": destination
    }
