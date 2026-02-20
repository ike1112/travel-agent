
import json
import os
import urllib.request
import urllib.parse

# Environment
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")
PLACES_API_URL = "https://places.googleapis.com/v1/places:searchText"

def search_places(query):
    if not GOOGLE_PLACES_API_KEY:
        return []
    
    data = {"textQuery": query, "maxResultCount": 5}
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.websiteUri"
    }
    
    req = urllib.request.Request(PLACES_API_URL, data=json.dumps(data).encode(), headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read())
            return result.get("places", [])
    except Exception as e:
        print(f"Error searching places: {e}")
        return []

def lambda_handler(event, context):
    print("Events Agent: Real Search...")
    
    # Handle Input
    intent = event.get("extracted", event)
    # Check if 'input' is in the intent (legacy/stub logic safe-guard)
    if isinstance(intent, dict) and "input" in intent:
        intent = intent["input"]
        
    destination = intent.get("destination", "London")
    activity_prefs = intent.get("activity_preferences", ["tourist attractions"])
    
    all_places = []
    
    # Simple Loop for each preference
    for pref in activity_prefs[:2]: # Limit to top 2 preferences to avoid timeout/quota
        query = f"{pref} in {destination}"
        places = search_places(query)
        for p in places:
            all_places.append({
                "activity": pref,
                "name": p.get("displayName", {}).get("text"),
                "rating": p.get("rating"),
                "address": p.get("formattedAddress"),
                "website": p.get("websiteUri")
            })
            
    return {
        "source": "GooglePlaces",
        "events": all_places,
        "location": destination
    }
