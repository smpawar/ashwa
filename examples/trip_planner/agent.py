"""ADK trip-planning agent: one agent, four tools over in-memory data."""

from google.adk.agents import Agent

from harness import model_and_config

WEATHER = {
    "lisbon": {"forecast": "sunny", "high_c": 22, "low_c": 14},
    "paris": {"forecast": "rain most days", "high_c": 13, "low_c": 8},
    "tokyo": {"forecast": "clear and mild", "high_c": 19, "low_c": 12},
    "orlando": {"forecast": "hot with afternoon storms", "high_c": 31, "low_c": 23},
}

FLIGHTS = {
    ("orlando", "lisbon"): [("TP 226", 612, 1), ("UA 64", 745, 1), ("DL 272", 890, 0)],
    ("orlando", "paris"): [("AF 91", 698, 0), ("DL 96", 655, 1)],
    ("orlando", "tokyo"): [("NH 113", 1120, 1), ("UA 837", 980, 1), ("JL 5", 1340, 1)],
}

HOTELS = {
    "lisbon": [("Alfama House", 95), ("Hotel Tejo", 140), ("Avenida Palace", 260)],
    "paris": [("Hotel du Canal", 135), ("Le Marais Suites", 210), ("Rive Gauche Grand", 390)],
    "tokyo": [("Sakura Inn", 120), ("Shinjuku Tower Hotel", 185), ("Ginza Imperial", 420)],
}

USD_RATES = {"USD": 1.0, "EUR": 0.92, "JPY": 150.0, "GBP": 0.79}


def get_weather(city: str) -> dict:
    """Get this week's weather forecast for a city.

    Args:
        city: The city name (e.g. "Lisbon").

    Returns:
        The forecast with high and low temperatures in Celsius, or an error.
    """
    row = WEATHER.get(city.strip().lower())
    if not row:
        return {"error": f"No forecast available for {city}."}
    return {"city": city, **row}


def search_flights(origin: str, destination: str) -> dict:
    """Search round-trip flights between two cities.

    Args:
        origin: The departure city (e.g. "Orlando").
        destination: The arrival city (e.g. "Tokyo").

    Returns:
        The available flights with round-trip price in US dollars and number of stops, or an error.
    """
    rows = FLIGHTS.get((origin.strip().lower(), destination.strip().lower()))
    if not rows:
        return {"error": f"No flights found from {origin} to {destination}."}
    return {"flights": [{"flight": f, "price_usd": p, "stops": s} for f, p, s in rows]}


def search_hotels(city: str, max_price_per_night: float) -> dict:
    """Search hotels in a city under a nightly price limit.

    Args:
        city: The city to stay in.
        max_price_per_night: The highest acceptable nightly price in US dollars.

    Returns:
        The matching hotels with nightly price in US dollars, or an error.
    """
    rows = HOTELS.get(city.strip().lower())
    if rows is None:
        return {"error": f"No hotels listed for {city}."}
    return {"hotels": [{"hotel": h, "price_per_night_usd": p} for h, p in rows if p <= max_price_per_night]}


def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert an amount of money between currencies.

    Args:
        amount: The amount to convert.
        from_currency: The three-letter code of the source currency (USD, EUR, JPY or GBP).
        to_currency: The three-letter code of the target currency.

    Returns:
        The converted amount, or an error.
    """
    src, dst = from_currency.strip().upper(), to_currency.strip().upper()
    if src not in USD_RATES or dst not in USD_RATES:
        return {"error": f"Unsupported currency pair {from_currency}/{to_currency}."}
    return {"amount": round(amount / USD_RATES[src] * USD_RATES[dst], 2), "currency": dst}


BASE = """You are a travel assistant for a small travel agency.
Use the tools for every fact about weather, flights, hotels and exchange rates; never guess them.
Give short, direct answers and include the figures the customer asked for."""

PROMPTS = {
    "a": BASE,
    "b": BASE + "\nAlways check the weather forecast for the destination before recommending a flight or a hotel.",
}


def build_agent(variant: str) -> Agent:
    model, config = model_and_config()
    return Agent(
        name="trip_planner",
        model=model,
        instruction=PROMPTS[variant],
        tools=[get_weather, search_flights, search_hotels, convert_currency],
        generate_content_config=config,
    )
