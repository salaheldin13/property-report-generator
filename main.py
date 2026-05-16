import os
import httpx
import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="AI Property Report Generator")
app.mount("/static", StaticFiles(directory="static"), name="static")

FRED_API_KEY = os.getenv("FRED_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

FRED_SERIES = {
    "hpi":       "CSUSHPISA",    # Case-Shiller National Home Price Index
    "mortgage":  "MORTGAGE30US", # 30-Year Fixed Mortgage Rate
    "unemploy":  "UNRATE",       # National Unemployment Rate
    "cpi":       "CPIAUCSL",     # Consumer Price Index
    "permits":   "HOUST",        # New Housing Permits (thousands)
}

ZIP_TO_METRO = {
    "336": "Tampa, FL",
    "100": "New York, NY",
    "900": "Los Angeles, CA",
    "606": "Chicago, IL",
    "770": "Houston, TX",
    "852": "Phoenix, AZ",
    "191": "Philadelphia, PA",
    "331": "Miami, FL",
    "321": "Orlando, FL",
    "941": "San Francisco, CA",
    "980": "Seattle, WA",
    "800": "Denver, CO",
    "021": "Boston, MA",
    "302": "Atlanta, GA",
    "782": "San Antonio, TX",
}


class ReportRequest(BaseModel):
    zip_code: str


async def fetch_fred_series(client: httpx.AsyncClient, series_id: str) -> list[dict]:
    """Fetch the 12 most recent observations for a FRED series."""
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "limit": 12,
        "sort_order": "desc",
    }
    response = await client.get(FRED_BASE, params=params)
    response.raise_for_status()
    data = response.json()
    observations = [o for o in data.get("observations", []) if o["value"] != "."]
    return observations


def compute_yoy_change(obs: list[dict]) -> tuple[float | None, float | None]:
    """Return (current_value, yoy_pct_change) from a list of observations."""
    if not obs:
        return None, None
    current = float(obs[0]["value"])
    if len(obs) >= 12:
        year_ago = float(obs[11]["value"])
        change = ((current - year_ago) / abs(year_ago)) * 100
        return current, round(change, 2)
    return current, None


def build_data_context(city: str, zip_code: str, metrics: dict) -> str:
    """Format FRED metrics into a readable context string for the LLM."""
    lines = [f"Market Data for {city} (Zip: {zip_code}) — Latest FRED Data:\n"]
    labels = {
        "hpi":      ("Case-Shiller Home Price Index", "", ""),
        "mortgage": ("30-Year Fixed Mortgage Rate",   "", "%"),
        "unemploy": ("Unemployment Rate",             "", "%"),
        "cpi":      ("CPI (Inflation proxy)",         "", ""),
        "permits":  ("New Housing Permits",           "", "K"),
    }
    for key, (name, prefix, suffix) in labels.items():
        val, chg = metrics.get(key, (None, None))
        val_str = f"{prefix}{val:.2f}{suffix}" if val is not None else "unavailable"
        chg_str = f"{chg:+.1f}% YoY" if chg is not None else "unavailable"
        lines.append(f"  {name}: {val_str} (12-month change: {chg_str})")
    return "\n".join(lines)


def build_prompt(city: str, zip_code: str, data_context: str) -> str:
    return f"""You are a real estate market analyst. Based on the macroeconomic data below,
generate a concise investment report for a property in {city} (zip code {zip_code}).

{data_context}

Respond ONLY with a valid JSON object in exactly this format (no markdown, no extra text):
{{
  "summary": "2-3 sentence plain-English market summary",
  "outlook": "bullish | neutral | bearish",
  "risks": [
    {{"label": "Risk name (3-5 words)", "level": "low | medium | high"}},
    {{"label": "Risk name (3-5 words)", "level": "low | medium | high"}},
    {{"label": "Risk name (3-5 words)", "level": "low | medium | high"}}
  ],
  "recommendation": "buy | hold | avoid",
  "rec_reason": "1-2 sentences explaining the recommendation",
  "actuarial_note": "1 sentence framing the market from a risk/insurance perspective"
}}"""


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html") as f:
        return f.read()


@app.post("/report")
async def generate_report(req: ReportRequest):
    zip_code = req.zip_code.strip()
    if not zip_code.isdigit() or len(zip_code) != 5:
        raise HTTPException(status_code=400, detail="Invalid zip code.")

    city = ZIP_TO_METRO.get(zip_code[:3]) or ZIP_TO_METRO.get(zip_code[:2]) or f"{zip_code} Area"

    # Fetch all FRED series concurrently
    async with httpx.AsyncClient(timeout=15) as client:
        import asyncio
        results = await asyncio.gather(
            *[fetch_fred_series(client, sid) for sid in FRED_SERIES.values()],
            return_exceptions=True
        )

    metrics = {}
    for key, obs in zip(FRED_SERIES.keys(), results):
        if isinstance(obs, Exception) or not obs:
            metrics[key] = (None, None)
        else:
            metrics[key] = compute_yoy_change(obs)

    data_context = build_data_context(city, zip_code, metrics)
    prompt = build_prompt(city, zip_code, data_context)

    # Call Claude
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    import json
    raw = message.content[0].text.strip().replace("```json", "").replace("```", "")
    ai_analysis = json.loads(raw)

    # Build metric cards for the response
    metric_cards = []
    card_defs = [
        ("hpi",      "HPI (National)", "", ""),
        ("mortgage", "Mortgage rate",  "", "%"),
        ("unemploy", "Unemployment",   "", "%"),
        ("permits",  "New permits",    "", "K"),
    ]
    for key, label, prefix, suffix in card_defs:
        val, chg = metrics[key]
        metric_cards.append({
            "label":  label,
            "value":  f"{prefix}{val:.2f}{suffix}" if val else "N/A",
            "change": f"{chg:+.1f}% YoY" if chg is not None else None,
            "direction": "up" if (chg or 0) > 0 else "down" if (chg or 0) < 0 else "flat",
        })

    return {
        "city":         city,
        "zip_code":     zip_code,
        "metrics":      metric_cards,
        "ai_analysis":  ai_analysis,
        "data_context": data_context,
    }
