# AI Medical Voice Assistant

A production-ready multilingual (English, Hindi, Kannada) virtual receptionist designed to automate doctor search, availability checks, and appointment scheduling, rescheduling, and cancellation at City Health Medical Center.

---

## Architecture Overview

```
[ Patient Mic ] ──(Audio Chunks)──> [ React UI ]
                                        │ (Local WebSockets)
                                        ▼
[ Sarvam Bulbul v3 TTS ] <── [ Groq LLM (Tools) ] <── [ Deepgram STT ]
           │                                                ▲
           └───────────(Audio Output Chunks)────────────────┘
```

*   **Frontend:** React (Vite, TypeScript, Tailwind CSS, Lucide icons)
*   **Backend:** FastAPI (Python, WebSockets, Uvicorn, SQLAlchemy)
*   **Voice Engine:** Pipecat 1.7.0
*   **Speech-to-Text (STT):** Deepgram STT
*   **LLM Service:** Groq (`openai/gpt-oss-20b` fallback model)
*   **Text-to-Speech (TTS):** Sarvam Bulbul v3 (`ritu` voice reception style, `16000` Hz)
*   **Calendar Sync:** Google Calendar API (Service Account integration)
*   **Database:** SQLite locally / Neon PostgreSQL in production

---

## Core Features

1.  **Strict Voice Pipeline:** High-speed real-time translation and tool execution routing.
2.  **Voice-Driven Multilingual Switching:** Greets initially in English and shifts seamlessly to Hindi or Kannada depending on user voice request.
3.  **Inactivity Silence Timeout (20 seconds):**
    *   **20s Silence:** Assistant repeats the exact last question.
    *   **40s Silence:** Assistant asks: *"Are you still there? I can help you with your appointment."*
    *   **60s Silence:** Assistant states *"There is no response. The call will end."* and terminates the connection.
4.  **Automatic Panel Extraction:** React dashboard only populates confirmed appointment cards when a new appointment is booked during that active call session.
5.  **Seeded Doctor Baseline:** Pre-populated database featuring 20 medical experts across Cardiologists, Dermatologists, Dentists, and General Physicians.

---

## Environment Setup

Create a `.env` file in the `backend/` directory based on the configuration template below:

```env
GROQ_API_KEY=your_groq_key
DEEPGRAM_API_KEY=your_deepgram_key
SARVAM_API_KEY=your_sarvam_key
DATABASE_URL=sqlite:///./medical_voice_assistant.db
DAILY_API_KEY=
GOOGLE_CALENDAR_ID=your_google_calendar_id
GOOGLE_SERVICE_ACCOUNT_JSON=your_service_account_credentials_json_content
VOICE_TRANSPORT=websocket
```

---

## Running the Application

### 1. Backend Setup
Navigate to the `backend/` directory:

```bash
# Set up virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Reset and seed database with 20 doctor profiles
python -c "import sys; sys.path.append('.'); import scratch.clear_and_seed; scratch.clear_and_seed.main()"

# Run dev server
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend Setup
Navigate to the `frontend/` directory:

```bash
# Install packages
npm install

# Run frontend (proxied to port 8000)
npm run dev
```

---

## Testing

You can run the automated FastAPI test suite using `pytest`:

```bash
cd backend
venv\Scripts\activate
python -m pytest tests/
```
