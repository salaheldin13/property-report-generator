# AI Property Report Generator

An AI-powered web app that generates real estate investment summaries by combining live macroeconomic data from the FRED API with Claude (Anthropic) for natural language analysis.

Enter any U.S. zip code and get an instant report covering market trends, risk flags, and an investment recommendation.

---

## What it does

1. Fetches live data from 5 FRED series via the St. Louis Fed API:
   - `CSUSHPISA` — S&P Case-Shiller National Home Price Index
   - `MORTGAGE30US` — 30-Year Fixed Mortgage Rate (Freddie Mac)
   - `UNRATE` — National Unemployment Rate
   - `CPIAUCSL` — Consumer Price Index (inflation proxy)
   - `HOUST` — New Residential Housing Permits

2. Sends the data as structured context to Claude (claude-sonnet-4) via the Anthropic API

3. Parses the structured JSON response and renders a dashboard with:
   - 4 metric cards with year-over-year change indicators
   - Plain-English market summary
   - Risk flags (low / medium / high)
   - Actuarial risk perspective
   - Buy / Hold / Avoid recommendation with reasoning

---

## Tech stack

| Layer    | Technology                        |
|----------|-----------------------------------|
| Backend  | Python, FastAPI, httpx (async)    |
| AI       | Anthropic API (claude-sonnet-4)   |
| Data     | FRED API (St. Louis Fed)          |
| Frontend | Vanilla HTML/CSS/JS               |

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/property-report-generator.git
cd property-report-generator
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up API keys

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

- **Anthropic API key**: [console.anthropic.com](https://console.anthropic.com)
- **FRED API key**: [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html) (free)

### 4. Run the app

```bash
uvicorn main:app --reload
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Project structure

```
property-report-generator/
├── main.py              # FastAPI backend — data fetching, LLM call, API routes
├── static/
│   └── index.html       # Frontend UI
├── requirements.txt
├── .env.example
└── README.md
```

---

## API

### `POST /report`

**Request body:**
```json
{ "zip_code": "33620" }
```

**Response:**
```json
{
  "city": "Tampa, FL",
  "zip_code": "33620",
  "metrics": [...],
  "ai_analysis": {
    "summary": "...",
    "outlook": "bullish | neutral | bearish",
    "risks": [{"label": "...", "level": "low | medium | high"}],
    "recommendation": "buy | hold | avoid",
    "rec_reason": "...",
    "actuarial_note": "..."
  }
}
```

---

## Notes

- FRED data is national-level. Zip-to-city mapping is used for context labeling only.
- Reports are for educational purposes and do not constitute financial advice.
- Built as a portfolio project demonstrating LLM API integration with real-world data.

---

## Author

Salaheldin Ali · Applied Mathematics & Economics, University of South Florida  
Pursuing actuarial career (SOA track) with a focus on health insurance
