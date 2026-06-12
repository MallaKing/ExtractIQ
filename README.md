# ExtractIQ

**Turn any document into structured data — automatically.**

Drop a PDF into your Google Drive folder. ExtractIQ reads it, extracts every field you defined, validates the result, and writes a clean row into your Google Sheet — in seconds, with no code and no copy-pasting.

---

## What it does

Most extraction tools are domain-locked — one tool for invoices, another for resumes, another for contracts. ExtractIQ works on *any* document with *any* schema you define at runtime.

You describe what you want to extract. ExtractIQ figures out where it is in the document, verifies the answer is actually supported by the text, and flags anything it isn't confident about.

---

## How it works

```
You drop a PDF into a Google Drive folder
          ↓
Google notifies ExtractIQ via webhook
          ↓
ExtractIQ downloads the file (never touches your disk)
          ↓
LLM extracts the fields you defined in your schema
          ↓
6-stage reliability pipeline validates the result
          ↓
Clean row appears in your ExtractIQ_Log Google Sheet
```

---

## The Reliability Pipeline

Raw LLM output can look correct but be completely wrong — hallucinated values, wrong numbers, missing context. ExtractIQ runs every extraction through six deterministic stages before writing a single cell.

| Stage | What it checks |
|---|---|
| **Schema Validation** | Required fields present, correct types, valid structure |
| **Grounding** | Every extracted value traced back to the source document |
| **Hallucination Detection** | Required fields with no document support are flagged |
| **Structural Validation** | Empty strings, negative amounts, invalid emails/dates |
| **LLM Consistency Check** | Arithmetic, counting, contradictions, unsupported inferences |
| **Confidence Scoring** | 0–1 reliability score combining all signals |

If a value scores poorly, the system retries with targeted prompts — only the failing fields, not the whole document.

---

## Getting Started

### 1. Get a free Groq API key

Go to [console.groq.com/keys](https://console.groq.com/keys) and create a key. ExtractIQ uses your personal key — your usage, your rate limits, your cost (usually zero on the free tier).

### 2. Open the app

```
https://your-render-url.onrender.com/dashboard
```

Or locally:
```bash
uvicorn app.main:app --reload
# Open http://localhost:8000/dashboard
```

### 3. Paste your Groq key

On the dashboard, paste your `gsk_...` key. It's stored encrypted with your account — never shared.

### 4. Connect Google Drive

Click **Sign in with Google** and grant Drive access. This is a standard OAuth flow — ExtractIQ never stores your password, only the access token needed to read your files.

### 5. Create a schema

Click **+ New Schema** and define what fields to extract. Example for invoices:

```json
{
  "invoice_number": { "type": "string", "required": true },
  "vendor_name":    { "type": "string", "required": true },
  "amount":         { "type": "float",  "required": true },
  "invoice_date":   { "type": "string", "required": false }
}
```

Your schema gets an ID — say `3`.

### 6. Name your folder

Create a folder in Google Drive with the schema ID as a suffix:

```
Invoices_sch_3
```

The `_sch_3` tells ExtractIQ which schema to use when a file lands in this folder. You can have as many folders as you want — one per schema.

### 7. Start monitoring

Select the folder in the dashboard and click **Start Monitoring**. ExtractIQ registers a webhook with Google — from this point on, any file you drop in is processed automatically.

### 8. Drop a file

Upload any PDF, HTML, or text file into your watched folder. Within a few seconds, a new row appears in `ExtractIQ_Log` — a Google Sheet that ExtractIQ creates automatically in the same folder.

---

## Supported Field Types

| Type | Example value |
|---|---|
| `string` | `"Acme Corp"` |
| `integer` | `42` |
| `float` | `5000.00` |
| `boolean` | `true` |
| `array` | `["Python", "FastAPI"]` |
| `object` | `{"street": "123 Main St"}` |

---

## Bring Your Own Key (BYOK)

ExtractIQ is a BYOK platform. You supply your own Groq API key — this means:

- **No shared rate limits** — your quota is your own
- **No per-extraction charges** from us — Groq's free tier is generous
- **Full cost transparency** — you see exactly what's being used in your Groq dashboard
- **Privacy** — your documents are sent directly from the server to Groq under your key, not routed through any shared inference pool

---

## Manual Extraction

You don't need Google Drive to test ExtractIQ. On the dashboard, scroll to **Try Manual Extraction**, paste any document text, define a schema, and hit **Extract**. You'll see the full pipeline result including grounding status, confidence score, and hallucination flags.

---

## Managing Watch Connections

You can monitor multiple folders simultaneously — each gets its own watch connection. In the **Select Folder to Monitor** section, your active watches are listed. Click **Stop** to remove a watch at any time.

---


---

## Local Development

```bash
# 1. Clone and set up
git clone <your-repo>
cd ExtractIQ
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux
pip install -r requirements.txt

# 2. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your keys

# 3. Start Postgres and Redis (Docker)
docker-compose up postgres redis -d

# 4. Run migrations
python patch_db.py

# 5. Start the server
uvicorn app.main:app --reload

# 6. Start Celery worker (separate terminal)
celery -A app.workers.celery_app worker --loglevel=info -P threads

# 7. Expose locally via ngrok
ngrok http --domain=your-static-domain.ngrok-free.dev 8000
```

Open `http://localhost:8000/dashboard`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| LLM | Groq (Llama 3.3 70B, Qwen 2.5, Gemma 2, DeepSeek R1) |
| Validation | Pydantic v2 |
| Database | PostgreSQL via SQLAlchemy |
| Queue | Celery + Redis |
| Storage | Google Drive API (OAuth2) |
| Sheets | Google Sheets API |
| Auth | Google OAuth 2.0 (PKCE) |
| Frontend | Vanilla HTML/CSS/JS — no build step |

---

## API Reference

The full REST API is available at `/docs` (Swagger UI) and `/redoc`.

Key endpoints:

```
GET  /dashboard                          Frontend app
GET  /api/v1/auth/google/login           Start OAuth flow
GET  /api/v1/auth/google/callback        OAuth callback
POST /api/v1/auth/groq-key               Save Groq API key
GET  /api/v1/auth/folders                List Drive folders
POST /api/v1/auth/watch                  Register folder watch
GET  /api/v1/auth/watches                List active watches
DELETE /api/v1/auth/watches/{channel_id} Stop a watch
POST /schemas/                           Create extraction schema
GET  /schemas/user/{user_id}             List your schemas
DELETE /schemas/{id}                     Delete a schema
POST /extract/                           Manual text extraction
POST /extract/upload                     Manual file extraction
POST /extract/batch                      Async batch extraction
GET  /extract/jobs/{job_id}              Poll batch job status
```

---

## License

MIT
