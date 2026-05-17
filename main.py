import os
import io
import json
import asyncio
import httpx
import anthropic
import csv
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

ZILLOW_ZHVI_URL = (
    "https://files.zillowstatic.com/research/public_csvs/zhvi/"
    "Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
)

FRED_SERIES = {
    "hpi":      "CSUSHPISA",
    "mortgage": "MORTGAGE30US",
    "unemploy": "UNRATE",
    "cpi":      "CPIAUCSL",
    "permits":  "HOUST",
}

METRO_UNEMPLOY = {
    "336": "TAMP823URN", "337": "TAMP823URN", "338": "TAMP823URN",
    "100": "NYCA023URN", "101": "NYCA023URN", "102": "NYCA023URN",
    "900": "LOSA106URN", "901": "LOSA106URN", "902": "LOSA106URN",
    "606": "CHIC917URN", "607": "CHIC917URN",
    "770": "HOUS148URN", "771": "HOUS148URN",
    "852": "PHOE178URN", "853": "PHOE178URN",
    "191": "PHIL242URN", "192": "PHIL242URN",
    "331": "MIAM133URN", "332": "MIAM133URN", "333": "MIAM133URN",
    "321": "ORLA367URN", "322": "ORLA367URN",
    "941": "SANF280URN", "942": "SANF280URN",
    "980": "SEAT316URN", "981": "SEAT316URN",
    "800": "DENV184URN", "801": "DENV184URN",
    "021": "BOST148URN", "022": "BOST148URN",
    "302": "ATLA112URN", "303": "ATLA112URN",
    "782": "SANA411URN", "783": "SANA411URN",
}

ZIP_TO_METRO = {
    "336": "Tampa, FL", "337": "Tampa, FL", "338": "Tampa, FL",
    "100": "New York, NY", "101": "New York, NY",
    "900": "Los Angeles, CA", "606": "Chicago, IL",
    "770": "Houston, TX", "852": "Phoenix, AZ",
    "191": "Philadelphia, PA",
    "331": "Miami, FL", "332": "Miami, FL", "333": "Miami, FL",
    "321": "Orlando, FL", "941": "San Francisco, CA",
    "980": "Seattle, WA", "800": "Denver, CO",
    "021": "Boston, MA", "302": "Atlanta, GA", "782": "San Antonio, TX",
}

_zillow_cache: dict = {}
_zillow_loaded = False


class ReportRequest(BaseModel):
    zip_code: str


async def load_zillow_data():
    global _zillow_cache, _zillow_loaded
    if _zillow_loaded:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(ZILLOW_ZHVI_URL)
            resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            zip_code = str(row.get("RegionName", "")).zfill(5)
            date_cols = sorted(
                [k for k in row.keys() if len(k) == 10 and k[4] == "-"],
                reverse=True,
            )
            if len(date_cols) < 13:
                continue
            current_val = row.get(date_cols[0], "")
            year_ago_val = row.get(date_cols[12], "")
            if current_val and year_ago_val:
                try:
                    cur = float(current_val)
                    yr = float(year_ago_val)
                    chg = ((cur - yr) / abs(yr)) * 100
                    _zillow_cache[zip_code] = (cur, round(chg, 2))
                except ValueError:
                    pass
        _zillow_loaded = True
        print(f"Zillow data loaded: {len(_zillow_cache)} zip codes")
    except Exception as e:
        print(f"Zillow data load failed: {e}")
        _zillow_loaded = True


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(load_zillow_data())


async def fetch_fred_series(client: httpx.AsyncClient, series_id: str) -> list[dict]:
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "limit": 13,
        "sort_order": "desc",
    }
    response = await client.get(FRED_BASE, params=params)
    response.raise_for_status()
    data = response.json()
    return [o for o in data.get("observations", []) if o["value"] != "."]


def compute_yoy_change(obs: list[dict]) -> tuple:
    if not obs:
        return None, None
    current = float(obs[0]["value"])
    if len(obs) >= 12:
        year_ago = float(obs[11]["value"])
        change = ((current - year_ago) / abs(year_ago)) * 100
        return current, round(change, 2)
    return current, None


def build_data_context(city, zip_code, metrics, local_home_value):
    lines = [f"Market Data for {city} (Zip: {zip_code}):\n"]
    zhvi_val, zhvi_chg = local_home_value
    if zhvi_val:
        chg_str = f"{zhvi_chg:+.1f}% YoY" if zhvi_chg is not None else "unavailable"
        lines.append(f"  Local Median Home Value (Zillow, zip {zip_code}): ${zhvi_val:,.0f} ({chg_str})")
    else:
        lines.append(f"  Local Median Home Value (Zillow, zip {zip_code}): unavailable")
    labels = {
        "hpi":         ("Case-Shiller National HPI",  "", ""),
        "mortgage":    ("30-Year Fixed Mortgage Rate", "", "%"),
        "metro_unemp": ("Metro Unemployment Rate",    "", "%"),
        "unemploy":    ("National Unemployment Rate", "", "%"),
        "cpi":         ("CPI Inflation proxy",        "", ""),
        "permits":     ("New Housing Permits",        "", "K"),
    }
    for key, (name, prefix, suffix) in labels.items():
        val, chg = metrics.get(key, (None, None))
        if val is None:
            continue
        chg_str = f"{chg:+.1f}% YoY" if chg is not None else "unavailable"
        lines.append(f"  {name}: {prefix}{val:.2f}{suffix} ({chg_str})")
    return "\n".join(lines)


def build_prompt(city, zip_code, data_context):
    return f"""You are a real estate market analyst. Based on the data below generate a concise investment report for a property in {city} (zip {zip_code}).

{data_context}

Respond ONLY with valid JSON, no markdown:
{{
  "summary": "2-3 sentence market summary referencing local home values",
  "outlook": "bullish | neutral | bearish",
  "risks": [
    {{"label": "Risk name 3-5 words", "level": "low | medium | high"}},
    {{"label": "Risk name 3-5 words", "level": "low | medium | high"}},
    {{"label": "Risk name 3-5 words", "level": "low | medium | high"}}
  ],
  "recommendation": "buy | hold | avoid",
  "rec_reason": "1-2 sentences using local data",
  "actuarial_note": "1 sentence risk/insurance perspective"
}}"""


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html") as f:
        return f.read()


@app.post("/report")
async def generate_report(req: ReportRequest):
    zip_code = req.zip_code.strip().zfill(5)
    if not zip_code.isdigit() or len(zip_code) != 5:
        raise HTTPException(status_code=400, detail="Invalid zip code.")

    city = ZIP_TO_METRO.get(zip_code[:3]) or ZIP_TO_METRO.get(zip_code[:2]) or f"{zip_code} Area"
    metro_series = METRO_UNEMPLOY.get(zip_code[:3])

    fred_keys = list(FRED_SERIES.keys())
    fred_series_ids = list(FRED_SERIES.values())
    if metro_series:
        fred_keys.append("metro_unemp")
        fred_series_ids.append(metro_series)

    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(
            *[fetch_fred_series(client, sid) for sid in fred_series_ids],
            return_exceptions=True,
        )

    metrics = {}
    for key, obs in zip(fred_keys, results):
        metrics[key] = (None, None) if isinstance(obs, Exception) or not obs else compute_yoy_change(obs)

    if not _zillow_loaded:
        await load_zillow_data()
    local_home_value = _zillow_cache.get(zip_code, (None, None))

    data_context = build_data_context(city, zip_code, metrics, local_home_value)
    prompt = build_prompt(city, zip_code, data_context)

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = ai_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip().replace("```json", "").replace("```", "")
    ai_analysis = json.loads(raw)

    zhvi_val, zhvi_chg = local_home_value
    metric_cards = []
    if zhvi_val:
        metric_cards.append({
            "label":     f"Median Home Value ({zip_code})",
            "value":     f"${zhvi_val:,.0f}",
            "change":    f"{zhvi_chg:+.1f}% YoY" if zhvi_chg is not None else None,
            "direction": "up" if (zhvi_chg or 0) > 0 else "down" if (zhvi_chg or 0) < 0 else "flat",
        })

    card_defs = [
        ("mortgage",    "Mortgage rate",         "", "%"),
        ("metro_unemp", "Metro unemployment",    "", "%"),
        ("unemploy",    "National unemployment", "", "%"),
        ("permits",     "New permits",           "", "K"),
    ]
    for key, label, prefix, suffix in card_defs:
        val, chg = metrics.get(key, (None, None))
        if val is None:
            continue
        metric_cards.append({
            "label":     label,
            "value":     f"{prefix}{val:.2f}{suffix}",
            "change":    f"{chg:+.1f}% YoY" if chg is not None else None,
            "direction": "up" if (chg or 0) > 0 else "down" if (chg or 0) < 0 else "flat",
        })

    return {"city": city, "zip_code": zip_code, "metrics": metric_cards, "ai_analysis": ai_analysis}
