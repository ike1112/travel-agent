
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime

# Environment Variables
AMADEUS_CLIENT_ID = os.environ.get("AMADEUS_CLIENT_ID")
AMADEUS_CLIENT_SECRET = os.environ.get("AMADEUS_CLIENT_SECRET")
# Use test environment by default
AMADEUS_BASE_URL = "https://test.api.amadeus.com"

def get_access_token():
    """Exchange credentials for an access token."""
    url = f"{AMADEUS_BASE_URL}/v1/security/oauth2/token"
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": AMADEUS_CLIENT_ID,
        "client_secret": AMADEUS_CLIENT_SECRET
    }).encode()
    
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())["access_token"]
    except Exception as e:
        print(f"Error getting Amadeus token: {e}")
        return None

def search_flights(token, origin, destination, departure_date, return_date=None, budget=None):
    """Search for flight offers."""
    params = {
        "originLocationCode": origin, # Needs IATA code, effectively
        "destinationLocationCode": destination, # Needs IATA code
        "departureDate": departure_date,
        "adults": 1,
        "max": 5
    }
    if return_date:
        params["returnDate"] = return_date
    if budget:
        params["maxPrice"] = int(budget)
        params["currencyCode"] = "CAD" # Assuming CAD based on previous context

    query_string = urllib.parse.urlencode(params)
    url = f"{AMADEUS_BASE_URL}/v2/shopping/flight-offers?{query_string}"
    
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            return data.get("data", [])
    except urllib.error.HTTPError as e:
        print(f"Amadeus API Error: {e.code} {e.read().decode()}")
        return []
    except Exception as e:
        print(f"Error searching flights: {e}")
        return []

def lambda_handler(event, context):
    print("Flight Agent: Starting real search...")
    
    # Input Handling
    # Broker passes {"requestId": ..., "extracted": ...}
    intent = event.get("extracted", event)
    
    origin = intent.get("origin_city", "YEG") 
    destination = intent.get("destination", "LHR")
    dates = intent.get("travel_dates", {})
    departure_date = dates.get("departure", "2024-06-01")
    return_date = dates.get("return", "2024-06-05")
    budget = intent.get("budget_cad")
    
    # Note: Amadeus requires IATA codes (e.g., YEG, LHR). 
    # For robust production, we'd need a city -> IATA lookup step. 
    # Here we assume the input might already be codes or we'll face errors if they are city names.
    # To keep it simple for Phase 5, let's assume inputs are somewhat valid or handle the error gracefully.
    # Actually, Amadeus City Search could solve this, but let's stick to simple assumption or mock IATA mapping for common demo cities.
    
    # Quick Mock Mapping for Demo Fairness
    iata_map = {
        "Edmonton": "YEG", "London": "LHR", "Paris": "CDG", "Tokyo": "NRT", 
        "Vancouver": "YVR", "New York": "JFK", "Toronto": "YYZ"
    }
    origin_code = iata_map.get(origin, origin) # Use mapped or original
    dest_code = iata_map.get(destination, destination)

    if not AMADEUS_CLIENT_ID or not AMADEUS_CLIENT_SECRET:
        print("Missing Amadeus Credentials")
        return {"error": "Missing API configuration"}
        
    token = get_access_token()
    if not token:
        # Fallback to stub behavior if AUTH FAILS (for development continuity if user has no keys)
        return {
            "source": "stub-flight-agent (Auth Failed)",
            "flights": [{"id": "fl_1", "airline": "StubAir", "price": "999 CAD"}],
            "input": event,
            "requestId": event.get("requestId")
        }
        
    offers = search_flights(token, origin_code, dest_code, departure_date, return_date, budget)
    
    # Simplify output for next steps
    simplified_offers = []
    for offer in offers[:3]:
        itineraries = offer.get("itineraries", [])
        price = offer.get("price", {})
        if itineraries:
            segments = itineraries[0].get("segments", [])
            carrier = segments[0].get("carrierCode", "Unknown")
            duration = itineraries[0].get("duration")
            simplified_offers.append({
                "carrier": carrier,
                "price": f"{price.get('total')} {price.get('currency')}",
                "duration": duration,
                "id": offer.get("id")
            })

    return {
        "source": "Amadeus",
        "offers": simplified_offers,
        "input": event,
        "requestId": event.get("requestId") # Pass through
    }
