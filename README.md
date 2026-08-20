
# AI Medical Voice Assistant

A production-ready multilingual virtual receptionist designed to automate doctor search, availability checks, appointment scheduling, rescheduling, and cancellation at **City Health Medical Center**.

The assistant supports **English, Hindi, and Kannada** and uses a real-time voice pipeline with selectable Speech-to-Text and Text-to-Speech providers.

---

## Architecture Overview

```text
[ Patient Microphone ]
        │
        │ Audio Chunks
        ▼
[ React Frontend ]
        │
        │ WebSocket
        ▼
[ Selected STT Provider ]
        │
        ▼
[ Gemini LLM + Appointment Tools ]
        │
        ▼
[ Selected TTS Provider ]
        │
        │ Audio Output Chunks
        ▼
[ React Frontend ]
        │
        ▼
[ Patient Speaker ]

                │
                ▼
      [ FastAPI Backend ]
                │
        ┌───────┴────────┐
        ▼                ▼
[ SQLite / Neon DB ] [ Google Calendar ]
````

---

## Technology Stack

### Frontend

* React
* Vite
* TypeScript
* Tailwind CSS
* Lucide Icons

### Backend

* FastAPI
* Python
* WebSockets
* Uvicorn
* SQLAlchemy

### Voice Pipeline

* Pipecat 1.7.0

### Speech-to-Text Providers

* Deepgram STT
* ElevenLabs STT
* Sarvam STT

Users can select the preferred STT provider from the UI.

### LLM

* Google Gemini

### Text-to-Speech Providers

* Sarvam Bulbul v3
* ElevenLabs TTS
* Deepgram TTS

Users can select the preferred TTS provider from the UI.

### Calendar Integration

* Google Calendar API
* Google Service Account

### Database

* SQLite for local development
* Neon PostgreSQL for production

---

## Core Features

### 1. Real-Time Voice Pipeline

The application processes the patient's speech in real time through the following flow:

```text
Patient Speech
      ↓
Selected STT Provider
      ↓
Gemini LLM
      ↓
Appointment Tools / API Calls
      ↓
Selected TTS Provider
      ↓
Voice Response to Patient
```

The Gemini LLM manages the conversation and determines when appointment-related tools should be called.

---

### 2. Multilingual Conversation Support

The assistant supports:

* English
* Hindi
* Kannada

The language selected in the UI is used when the conversation starts.

For example:

* If English is selected, the assistant starts directly in English.
* If Hindi is selected, the assistant starts directly in Hindi.
* If Kannada is selected, the assistant starts directly in Kannada.

The assistant does not ask the user to select a language again after the language has already been selected in the UI.

The patient can also naturally request a language change during the conversation.

---

### 3. Dynamic Variables

The system supports configurable dynamic variables.

Initially, the application includes:

* `patient_name`
* `doctor_name`

Both variables are initially empty.

The values can be collected from the patient's conversation.

Example:

> Patient: "Hi, my name is Sanjana."

The assistant extracts the value and updates:

```text
patient_name = Sanjana
```

If the assistant needs to greet the patient after extracting the name, it can use the extracted value dynamically.

Example:

> "Hello Sanjana, welcome to City Health Medical Center."

---

### 4. Custom Variables

Users can manually add additional variables from the UI.

Examples:

* `insurance`
* `patient_id`
* `preferred_time`
* `insurance_provider`

When a custom variable is manually added:

1. The variable name is stored.
2. The variable value is stored.
3. The variable is persisted in the database.
4. The variable becomes available for the current conversation.

Example:

```text
Variable Name: insurance
Variable Value: ABC Health Insurance
```

The AI does not automatically create new dynamic variables from arbitrary conversation details.

Only variables already configured in the UI are eligible for extraction and updates.

---

### 5. Dynamic Variable Extraction During Conversation

The system can collect values from the patient's conversation and update existing configured variables.

Example:

```text
Configured Variable:
patient_name
```

Patient says:

> "My name is Sanjana."

The system updates:

```text
patient_name = Sanjana
```

Similarly, if `doctor_name` is configured:

> "I want to book an appointment with Dr. John."

The system can update:

```text
doctor_name = Dr. John
```

This ensures that configured dynamic variables can receive values naturally from the conversation.

---

### 6. Dynamic Variables in Tool and API Requests

Values collected during the conversation can be used dynamically in appointment-related tool calls and API requests.

For example:

```text
patient_name = Sanjana
doctor_name = Dr. John
```

These values can be passed to the relevant appointment tools when required.

This demonstrates both:

1. Configuring variables during agent setup.
2. Collecting variable values during the patient's conversation and using them dynamically in tools or API requests.

---

### 7. Appointment Management

The assistant supports:

* Doctor search
* Doctor availability checks
* Appointment scheduling
* Appointment rescheduling
* Appointment cancellation

The LLM uses appointment-related tools to interact with the backend and database.

---

### 8. Live Appointment Extraction

Appointment information is displayed in the **Live Appointment Extractor** section.

The extractor can display:

* Doctor
* Specialty
* Date
* Time
* Appointment ID
* Booking Status

The appointment details are shown only when they are actually collected or confirmed during the conversation.

Default appointment information is not pre-filled based only on unrelated spoken information.

For example, mentioning a doctor's name should not automatically create a default appointment date or time.

---

### 9. UI Layout Separation

The UI is divided into two main sections.

#### Dynamic Session Variables

This section displays:

* `patient_name`
* `doctor_name`
* Manually added custom variables

These variables are initially empty.

Appointment information is not displayed in this section.

#### Live Appointment Extractor

This section displays appointment-related information:

* Doctor
* Specialty
* Date
* Time
* Appointment ID
* Booking Status

---

### 10. Session Isolation

Each voice call uses its own session.

When a call is ended:

* Previous appointment details are cleared from the UI.
* Old appointment information is not shown in the next call.
* The Live Appointment Extractor is reset.
* Dynamic session state is cleared for the new conversation.

This prevents data from one patient session from appearing in another session.

---

### 11. Silence and Inactivity Handling

The assistant handles periods of inactivity during a conversation.

* After **20 seconds of silence**, the assistant repeats the last question.
* After **40 seconds of silence**, the assistant asks:

> "Are you still there? I can help you with your appointment."

* After **60 seconds of silence**, the assistant says:

> "There is no response. The call will end."

The conversation is then terminated.

---

### 12. Selectable STT and TTS Providers

The user can select the Speech-to-Text and Text-to-Speech providers from the UI.

#### STT Options

* Deepgram
* ElevenLabs
* Sarvam

#### TTS Options

* ElevenLabs
* Deepgram
* Sarvam

This allows the application to demonstrate multiple voice provider integrations within the same voice assistant architecture.

---

### 13. Voice Activity Detection

Voice Activity Detection is used to detect when the patient starts and stops speaking.

Audio is processed and segmented before being sent to the appropriate Speech-to-Text service where required.

This helps improve real-time conversation handling and turn detection.

---

## Environment Setup

Create a `.env` file inside the `backend/` directory.

```env
GEMINI_API_KEY=your_gemini_api_key

DEEPGRAM_API_KEY=your_deepgram_api_key

SARVAM_API_KEY=your_sarvam_api_key

ELEVENLABS_API_KEY=your_elevenlabs_api_key

# Local development
DATABASE_URL=sqlite:///./medical_voice_assistant.db

# Production example
# DATABASE_URL=your_neon_postgresql_connection_string

GOOGLE_CALENDAR_ID=your_google_calendar_id

GOOGLE_SERVICE_ACCOUNT_JSON=your_service_account_credentials_json

VOICE_TRANSPORT=websocket
```

---

# Running the Application

## 1. Backend Setup

Navigate to the backend directory:

```bash
cd backend
```

Create a virtual environment:

```bash
python -m venv venv
```

Activate the virtual environment:

### Windows

```bash
venv\Scripts\activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Reset and seed the database with doctor profiles:

```bash
python -c "import sys; sys.path.append('.'); import scratch.clear_and_seed; scratch.clear_and_seed.main()"
```

Run the backend server:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

The backend will run at:

```text
http://127.0.0.1:8000
```

---

## 2. Frontend Setup

Open a new terminal and navigate to the frontend directory:

```bash
cd frontend
```

Install dependencies:

```bash
npm install
```

Run the development server:

```bash
npm run dev
```

The frontend will start on the Vite development server and communicate with the FastAPI backend.

---

## Database Configuration

### Local Development

The application uses SQLite:

```env
DATABASE_URL=sqlite:///./medical_voice_assistant.db
```

### Production

The application uses Neon PostgreSQL.

Example:

```env
DATABASE_URL=your_neon_postgresql_connection_string
```

Dynamic session variables and appointment-related data can be persisted through the database layer.

---

## Dynamic Variable Database Storage

Custom variables manually added through the UI are stored in the database.

Example data:

```text
session_id: abc123
variable_name: insurance
variable_value: ABC Health Insurance
```

This allows configured variables to persist and remain associated with the appropriate session.

The system does not automatically create database variables from arbitrary spoken conversation details.

Only configured variables can be updated through conversation extraction.

---

## Testing

Run the automated test suite:

```bash
cd backend
```

Activate the virtual environment:

```bash
venv\Scripts\activate
```

Run the tests:

```bash
python -m pytest tests/
```

---

## Manual Testing Checklist

### Language Selection

* Select English and verify that the assistant starts directly in English.
* Select Hindi and verify that the assistant starts directly in Hindi.
* Select Kannada and verify that the assistant starts directly in Kannada.
* Change the language during a conversation.

### Dynamic Variables

* Start with an empty `patient_name`.
* Say your name during the conversation.
* Verify that the value is extracted and displayed.
* Verify that the assistant can use the extracted name naturally in its response.

### Custom Variables

* Add a new variable manually from the UI.
* Add a value.
* Verify that it is saved to the database.
* Verify that the assistant only updates configured variables.

### Appointment Extraction

* Mention a doctor.
* Verify that only the doctor field is updated.
* Verify that appointment date and time are not filled until they are actually discussed or confirmed.
* Complete an appointment booking.
* Verify that the Appointment ID and Booking Status are displayed.

### Session Isolation

* Complete or end a call.
* Verify that the appointment details are cleared.
* Start a new call.
* Verify that previous appointment data is not displayed.

### Voice Providers

* Test each available STT provider.
* Test each available TTS provider.
* Verify that the selected provider is used for the active conversation.

---

## Project Summary

The AI Medical Voice Assistant is a multilingual virtual receptionist that combines real-time voice processing, Gemini-based conversation handling, dynamic variables, appointment tools, database persistence, and calendar integration.

The project demonstrates:

* Real-time voice conversations
* Multiple STT providers
* Multiple TTS providers
* Multilingual communication
* Configurable dynamic variables
* Conversation-based variable extraction
* Dynamic values passed to tools and API requests
* Doctor search and availability checks
* Appointment booking
* Appointment rescheduling
* Appointment cancellation
* Live appointment information extraction
* Database persistence
* Google Calendar integration
* Session isolation

````