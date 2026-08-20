import os
import asyncio
import logging
from typing import Dict, Any, Optional
import datetime
import aiohttp

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.services.elevenlabs.stt import ElevenLabsSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from ..config import settings
from .tools import ClinicInfoTool, DoctorTool, AvailabilityTool, BookingTool, CancellationTool, RescheduleTool, ExternalAPITool, GoogleCalendarTool

logger = logging.getLogger(__name__)

from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame, TextFrame, UserStartedSpeakingFrame, VADUserStartedSpeakingFrame, BotStoppedSpeakingFrame, EndFrame, LLMContextFrame

class AudioSerializer(FrameSerializer):
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)
        return None

# Setup base system prompt
SYSTEM_PROMPT = """
You are the AI medical receptionist for {{clinic_name}}.

Your main job is to help patients book, reschedule, cancel, and inquire about medical appointments through natural voice conversation.

Speak naturally like a real receptionist. Ask only one question at a time.
Keep responses short and conversational because your responses will be converted to speech.

APPOINTMENT BOOKING:
1. Ask which doctor or medical specialty the patient needs.
2. Use find_doctor to find the appropriate doctor.
3. Ask for the preferred appointment date.
4. Ask for the preferred time or time range.
5. Use check_availability to find actual available appointment slots.
6. Tell the patient the available options.
7. Ask for the patient's name and phone number.
8. Confirm the selected doctor, date, and time with the patient.
9. Call book_appointment only after the patient explicitly confirms.
10. Confirm the appointment only after book_appointment returns a successful result.

APPOINTMENT RESCHEDULING:
1. When the patient wants to reschedule, first ask for their Appointment ID.
2. Search or find the existing appointment details.
3. Ask for the preferred new date and time.
4. Call check_availability to find available slots for the doctor.
5. Present options, ask the patient for explicit confirmation to change the slot.
6. Only call reschedule_appointment after the patient confirms.
7. Confirm through voice once rescheduled successfully.

APPOINTMENT CANCELLATION:
1. When the patient wants to cancel, ask for their Appointment ID.
2. Find the existing appointment details, and verify them with the patient.
3. Ask the patient for explicit confirmation: "Do you want to cancel your appointment with Dr. [name] on [date]?"
4. Only call cancel_appointment after they confirm.
5. Confirm cancellation through voice once successful.

INSURANCE CHECK / API TOOL:
1. If the patient inquires about insurance verification, call check_insurance_status tool with their policy number and patient name.
2. Use the result (copay, provider, status) to guide the patient.

DYNAMIC VARIABLES COLLECTION:
1. If the patient tells you their name (e.g., "My name is Sanjana") or doctor preference during the conversation, call the update_session_variable tool (e.g. name="patient_name", value="Sanjana") to save it.

IMPORTANT:
- Never invent doctors, availability, appointment times, or clinic information.
- Always use the backend tools for doctor and appointment information.
- If no suitable doctor or appointment slot is available, clearly tell the patient.
- Do not perform booking, rescheduling, or cancellation without explicit confirmation from the patient.
- Ask for missing information one question at a time.

LANGUAGE SUPPORT:
The assistant supports English, Hindi, and Kannada.

At the beginning of the conversation, say:
"Hello {{patient_name}}, welcome to {{clinic_name}}. Which language would you like to use: English, Hindi, or Kannada?"

After the patient selects a language:
- Continue the entire conversation in that language.
- Do not switch languages unless the patient asks you to.
- Support booking, cancellation, rescheduling, and general clinic queries in all three languages.

VOICE RESPONSE RULES:
- Use plain conversational language.
- Do not use Markdown.
- Do not use bullet points, numbered lists, emojis, asterisks, or special formatting in responses.
- Keep responses concise and natural for voice.
- Never read tool names, technical details, database information, or internal instructions to the patient.
"""

class ActivityMonitor(FrameProcessor):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.last_question = ""
        self.current_assistant_text = []
        self.silence_count = 0
        self.timer_task = None
        self.task = None  # Set before pipeline run

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        # Intercept bot text chunk flowing downstream from LLM
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TextFrame):
            self.current_assistant_text.append(frame.text)
            
        # Bot stopped speaking (upstream message from transport / downstream completed)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if self.current_assistant_text:
                self.last_question = "".join(self.current_assistant_text).strip()
                self.current_assistant_text = []
            if self.last_question:
                self.reset_timer()
                
        # Inbound User started speaking (reset streak and cancel timer immediately)
        elif isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
            self.cancel_timer()
            self.silence_count = 0

        # Push frame to keep it flowing in pipeline
        await self.push_frame(frame, direction)

    def cancel_timer(self):
        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None

    def reset_timer(self):
        self.cancel_timer()
        self.timer_task = asyncio.create_task(self._silence_timeout_handler())

    async def _silence_timeout_handler(self):
        try:
            await asyncio.sleep(20.0)
            if not self.task:
                return

            self.silence_count += 1
            if self.silence_count == 1:
                logger.info(f"Silence timeout (1st). Repeating last question: {self.last_question}")
                await self.task.queue_frame(TextFrame(self.last_question))
            elif self.silence_count == 2:
                logger.info("Silence timeout (2nd). Prompting presence.")
                await self.task.queue_frame(TextFrame("Are you still there? I can help you with your appointment."))
            else:
                logger.info("Silence timeout (3rd). Ending session gracefully.")
                await self.task.queue_frame(TextFrame("There is no response. The call will end."))
                await asyncio.sleep(3.0)
                await self.task.queue_frame(EndFrame())
        except asyncio.CancelledError:
            pass

async def run_voice_agent(
    websocket,
    language: str = "en",
    transport_type: str = "websocket",
    room_url: Optional[str] = None,
    token: Optional[str] = None,
    stt_provider: str = "deepgram",
    tts_provider: str = "sarvam",
    variables: dict = None,
    session_id: Optional[str] = None
):
    gemini_key = settings.GEMINI_API_KEY
    deepgram_key = settings.DEEPGRAM_API_KEY
    today_str = datetime.date.today().strftime("%A, %B %d, %Y")
    session = aiohttp.ClientSession()

    # Dynamic variables setup
    session_vars = variables or {}
    default_vars = {
        "patient_name": "",
        "doctor_name": "",
        "clinic_name": "City Health Medical Center"
    }
    for k, v in default_vars.items():
        if k not in session_vars or session_vars[k] is None:
            session_vars[k] = v
            logger.info(
                f"[DYNAMIC VARIABLE]\n"
                f"source=default\n"
                f"key={k}\n"
                f"old_value=\n"
                f"new_value={v}"
            )
        else:
            logger.info(
                f"[DYNAMIC VARIABLE]\n"
                f"source=frontend\n"
                f"key={k}\n"
                f"old_value=\n"
                f"new_value={session_vars[k]}"
            )

    # Resolve variables in SYSTEM_PROMPT
    resolved_prompt = SYSTEM_PROMPT
    
    greeting_instruction = 'At the beginning of the conversation, say:\n"Hello {{patient_name}}, welcome to {{clinic_name}}. Which language would you like to use: English, Hindi, or Kannada?"'
    
    if language == "hi":
        if session_vars.get("patient_name"):
            new_greeting = 'At the beginning of the conversation, greet the patient by name in Hindi. Say:\n"नमस्ते {{patient_name}}, {{clinic_name}} में आपका स्वागत है। आज मैं आपकी क्या मदद कर सकता हूँ?"\nDo NOT ask them to choose a language.'
        else:
            new_greeting = 'At the beginning of the conversation, greet the patient in Hindi. Say:\n"नमस्ते, {{clinic_name}} में आपका स्वागत है। क्या मैं आपका नाम जान सकता हूँ?"\nDo NOT ask them to choose a language.'
    elif language == "kn":
        if session_vars.get("patient_name"):
            new_greeting = 'At the beginning of the conversation, greet the patient by name in Kannada. Say:\n"ನಮಸ್ಕಾರ {{patient_name}}, {{clinic_name}} ಗೆ ಸುಸ್ವಾಗತ. ಇಂದು ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು?"\nDo NOT ask them to choose a language.'
        else:
            new_greeting = 'At the beginning of the conversation, greet the patient in Kannada. Say:\n"ನಮಸ್ಕಾರ, {{clinic_name}} ಗೆ ಸುಸ್ವಾಗತ. ನಾನು ನಿಮ್ಮ ಹೆಸರನ್ನು ತಿಳಿಯಬಹುದೇ?"\nDo NOT ask them to choose a language.'
    else:
        if session_vars.get("patient_name"):
            new_greeting = 'At the conversation start, greet the patient by name in English. Say:\n"Hello {{patient_name}}, welcome to {{clinic_name}}. How can I help you today?"\nDo NOT ask them to choose a language.'
        else:
            new_greeting = 'At the conversation start, greet the patient in English. Say:\n"Hello, welcome to {{clinic_name}}. May I know your name?"\nDo NOT ask them to choose a language.'

    resolved_prompt = resolved_prompt.replace(greeting_instruction, new_greeting)
    for k, v in session_vars.items():
        resolved_prompt = resolved_prompt.replace(f"{{{{{k}}}}}", str(v))

    dynamic_prompt = f"Today's date is {today_str}.\n\n{resolved_prompt}"

    # 1. Initialize Transport
    if transport_type == "daily":
        from pipecat.transports.services.daily import DailyTransport, DailyTransportParams
        if not room_url:
            raise ValueError("room_url must be provided for Daily transport.")
        params = DailyTransportParams(
            audio_out_enabled=True,
            audio_in_enabled=True,
            vad_enabled=True,
            vad_analyzer=None,  # Uses default system VAD
            token=token
        )
        transport = DailyTransport(room_url, token, "AI Receptionist", params)
    else:
        # FastAPI WebSockets transport
        logger.info("Initializing FastAPIWebsocketTransport for voice agent.")
        ws_params = FastAPIWebsocketParams(
            audio_out_enabled=True,
            audio_out_sample_rate=16000,
            audio_in_enabled=True,
            audio_in_sample_rate=16000,
            serializer=AudioSerializer()
        )
        transport = FastAPIWebsocketTransport(websocket, ws_params)

    # 2. Setup Services (LLM)
    llm = GoogleLLMService(
        api_key=gemini_key,
        settings=GoogleLLMService.Settings(
            model="gemini-3.5-flash-lite",
            system_instruction=dynamic_prompt,
            max_tokens=512,
            thinking=GoogleLLMService.ThinkingConfig(
                thinking_level="minimal",
                include_thoughts=False,
            ),
        ),
    )

    # 3. Setup STT Service
    stt_provider = stt_provider.lower()
    if stt_provider == "elevenlabs":
        eleven_key = settings.ELEVENLABS_API_KEY
        if not eleven_key:
            raise ValueError("ELEVENLABS_API_KEY is not configured on the backend.")
        stt = ElevenLabsSTTService(api_key=eleven_key, aiohttp_session=session)
    elif stt_provider == "sarvam":
        sarvam_key = settings.SARVAM_API_KEY
        if not sarvam_key:
            raise ValueError("SARVAM_API_KEY is not configured on the backend.")
        stt = SarvamSTTService(api_key=sarvam_key)
    else:
        stt = DeepgramSTTService(api_key=deepgram_key, language=language)

    # 4. Setup TTS Service
    tts_provider = tts_provider.lower()
    if tts_provider == "elevenlabs":
        eleven_key = settings.ELEVENLABS_API_KEY
        if not eleven_key:
            raise ValueError("ELEVENLABS_API_KEY is not configured on the backend.")
        tts = ElevenLabsTTSService(
            api_key=eleven_key,
            settings=ElevenLabsTTSService.Settings(
                voice="Xb7hH8MSUJpSbSDYk0k2",
                model="eleven_flash_v2_5",
            )
        )
    elif tts_provider == "deepgram":
        tts = DeepgramTTSService(api_key=deepgram_key)
    else:
        sarvam_key = settings.SARVAM_API_KEY
        if not sarvam_key:
            raise ValueError("SARVAM_API_KEY is not configured on the backend.")
        language_map = {
            "en": "en-IN",
            "hi": "hi-IN",
            "kn": "kn-IN",
        }
        target_language = language_map.get(language, "en-IN")
        tts = SarvamTTSService(
            api_key=sarvam_key,
            sample_rate=16000,
            settings=SarvamTTSService.Settings(
                model="bulbul:v3",
                voice="ritu",
                language=target_language
            )
        )

    # 3. Setup LLM Context & System Prompt & Tool Schemas
    tools = [
        FunctionSchema(
            name="find_doctor",
            description="Search for doctors by their medical specialty.",
            properties={
                "specialty": {
                    "type": "string",
                    "description": "e.g. Dermatologist, Cardiologist, Dentist, or General Physician"
                }
            },
            required=["specialty"]
        ),
        FunctionSchema(
            name="get_doctor_info",
            description="Get detailed profile bio and languages for a doctor by ID.",
            properties={
                "doctor_id": {
                    "type": "integer",
                    "description": "Doctor's unique database ID"
                }
            },
            required=["doctor_id"]
        ),
        FunctionSchema(
            name="check_availability",
            description="Get all scheduled slots for a doctor on a specific date (YYYY-MM-DD) and optionally closest to a preferred time (HH:MM).",
            properties={
                "doctor_id": {
                    "type": "integer",
                    "description": "Doctor's database ID"
                },
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format"
                },
                "preferred_time": {
                    "type": "string",
                    "description": "Optional preferred time in HH:MM format"
                }
            },
            required=["doctor_id", "date"]
        ),
        FunctionSchema(
            name="book_appointment",
            description="Book a confirmed appointment slot for a doctor. Ensure you have the patient's name, phone, date, and time first.",
            properties={
                "doctor_id": {
                    "type": "integer",
                    "description": "Doctor's ID"
                },
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD"
                },
                "time": {
                    "type": "string",
                    "description": "Time format e.g. 10:00"
                },
                "patient_name": {
                    "type": "string",
                    "description": "Full name of the patient"
                },
                "patient_phone": {
                    "type": "string",
                    "description": "Phone number of the patient"
                }
            },
            required=["doctor_id", "date", "time", "patient_name", "patient_phone"]
        ),

        FunctionSchema(
            name="get_clinic_info",
            description="Retrieve clinic contact info, opening hours, and timezone.",
            properties={},
            required=[]
        ),
        FunctionSchema(
            name="cancel_appointment",
            description="Cancel a scheduled appointment by ID.",
            properties={
                "appointment_id": {
                    "type": "integer",
                    "description": "Unique Appointment ID"
                }
            },
            required=["appointment_id"]
        ),
        FunctionSchema(
            name="reschedule_appointment",
            description="Reschedule an existing appointment to a new date (YYYY-MM-DD) and time (HH:MM).",
            properties={
                "appointment_id": {
                    "type": "integer",
                    "description": "The unique Appointment ID"
                },
                "new_date": {
                    "type": "string",
                    "description": "New date in YYYY-MM-DD"
                },
                "new_time": {
                    "type": "string",
                    "description": "New time in HH:MM"
                }
            },
            required=["appointment_id", "new_date", "new_time"]
        ),
        FunctionSchema(
            name="check_insurance_status",
            description="Call the external REST API mock service to verify patient insurance policy and coverage copay details.",
            properties={
                "policy_number": {
                    "type": "string",
                    "description": "The insurance policy number"
                },
                "patient_name": {
                    "type": "string",
                    "description": "Name of the policy holder"
                }
            },
            required=["policy_number", "patient_name"]
        ),
        FunctionSchema(
            name="update_session_variable",
            description="Save or update a generic patient session state variable extracted from natural language (e.g. patient_name, doctor_name).",
            properties={
                "name": {
                    "type": "string",
                    "description": "Name of the variable (e.g. patient_name, doctor_name, etc.)"
                },
                "value": {
                    "type": "string",
                    "description": "Value to store in the variable"
                }
            },
            required=["name", "value"]
        ),
        FunctionSchema(
            name="check_calendar_availability",
            description="Query the external Google Calendar service to check if the time slot (ISO8601 datetimes) is free.",
            properties={
                "start_time_str": {
                    "type": "string",
                    "description": "Start datetime in ISO8601 format (e.g. 2026-08-18T10:00:00)"
                },
                "end_time_str": {
                    "type": "string",
                    "description": "End datetime in ISO8601 format (e.g. 2026-08-18T10:30:00)"
                }
            },
            required=["start_time_str", "end_time_str"]
        ),
        FunctionSchema(
            name="create_calendar_event",
            description="Create a calendar event directly in Google Calendar.",
            properties={
                "summary": {
                    "type": "string",
                    "description": "Short title of the event"
                },
                "start_time_str": {
                    "type": "string",
                    "description": "Start datetime in ISO8601 format"
                },
                "end_time_str": {
                    "type": "string",
                    "description": "End datetime in ISO8601 format"
                },
                "description": {
                    "type": "string",
                    "description": "Optional details description"
                }
            },
            required=["summary", "start_time_str", "end_time_str"]
        ),
        FunctionSchema(
            name="update_calendar_event",
            description="Update an existing Google Calendar event times.",
            properties={
                "event_id": {
                    "type": "string",
                    "description": "Google Calendar event ID"
                },
                "start_time_str": {
                    "type": "string",
                    "description": "New start datetime in ISO8601 format"
                },
                "end_time_str": {
                    "type": "string",
                    "description": "New end datetime in ISO8601 format"
                }
            },
            required=["event_id", "start_time_str", "end_time_str"]
        ),
        FunctionSchema(
            name="delete_calendar_event",
            description="Remove/delete an event from Google Calendar.",
            properties={
                "event_id": {
                    "type": "string",
                    "description": "Google Calendar event ID to delete"
                }
            },
            required=["event_id"]
        )
    ]

    context = LLMContext(messages=[{"role": "system", "content": dynamic_prompt}], tools=tools)
    user_params = LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)]
        )
    )
    aggregators = LLMContextAggregatorPair(context, user_params=user_params)

    # 4. Register LLM Tool Handlers
    async def find_doctor_handler(params: FunctionCallParams):
        specialty = params.arguments.get("specialty", "")
        res = DoctorTool.find_doctor(specialty)
        await params.result_callback(res)
    llm.register_function("find_doctor", find_doctor_handler)

    async def get_doctor_info_handler(params: FunctionCallParams):
        doctor_id = params.arguments.get("doctor_id")
        res = DoctorTool.get_doctor_info(int(doctor_id))
        await params.result_callback(res)
    llm.register_function("get_doctor_info", get_doctor_info_handler)

    async def check_availability_handler(params: FunctionCallParams):
        doctor_id = params.arguments.get("doctor_id")
        date = params.arguments.get("date")
        preferred_time = params.arguments.get("preferred_time")
        res = AvailabilityTool.check_availability(int(doctor_id), date, preferred_time)
        await params.result_callback(res)
    llm.register_function("check_availability", check_availability_handler)

    async def book_appointment_handler(params: FunctionCallParams):
        doctor_id = params.arguments.get("doctor_id")
        date = params.arguments.get("date")
        time = params.arguments.get("time")
        patient_name = params.arguments.get("patient_name")
        patient_phone = params.arguments.get("patient_phone")
        res = BookingTool.book_appointment(int(doctor_id), date, time, patient_name, patient_phone)
        await params.result_callback(res)
    llm.register_function("book_appointment", book_appointment_handler)
    async def cancel_appointment_handler(params: FunctionCallParams):
        appointment_id = params.arguments.get("appointment_id")
        res = CancellationTool.cancel_appointment(int(appointment_id))
        await params.result_callback(res)
    llm.register_function("cancel_appointment", cancel_appointment_handler)

    async def reschedule_appointment_handler(params: FunctionCallParams):
        appointment_id = params.arguments.get("appointment_id")
        new_date = params.arguments.get("new_date")
        new_time = params.arguments.get("new_time")
        res = RescheduleTool.reschedule_appointment(int(appointment_id), new_date, new_time)
        await params.result_callback(res)
    llm.register_function("reschedule_appointment", reschedule_appointment_handler)

    async def check_insurance_status_handler(params: FunctionCallParams):
        policy_number = params.arguments.get("policy_number")
        patient_name = params.arguments.get("patient_name")
        res = await ExternalAPITool.check_insurance_status(policy_number, patient_name, dynamic_vars=session_vars)
        await params.result_callback(res)
    llm.register_function("check_insurance_status", check_insurance_status_handler)

    async def update_session_variable_handler(params: FunctionCallParams):
        import json
        name = params.arguments.get("name")
        value = params.arguments.get("value")
        
        # Enforce rule: do not automatically create arbitrary new variable names
        if name not in session_vars:
            logger.warning(f"Rejection of automatic dynamic variable creation: {name} = {value}")
            await params.result_callback({
                "status": "error",
                "message": f"Variable '{name}' is not configured in this session. You can only update pre-existing variables."
            })
            return

        old_value = session_vars.get(name, "")
        session_vars[name] = value
        logger.info(
            f"[DYNAMIC VARIABLE]\n"
            f"source=conversation\n"
            f"key={name}\n"
            f"old_value={old_value}\n"
            f"new_value={value}"
        )
        
        # Persist updated value in database
        if session_id:
            from ..database import SessionLocal
            from ..models import SessionVariable
            db = SessionLocal()
            try:
                existing = db.query(SessionVariable).filter(
                    SessionVariable.session_id == session_id,
                    SessionVariable.variable_name == name
                ).first()
                if existing:
                    existing.variable_value = value
                else:
                    new_var = SessionVariable(
                        session_id=session_id,
                        variable_name=name,
                        variable_value=value
                    )
                    db.add(new_var)
                db.commit()
            except Exception as e:
                logger.error(f"Failed to persist updated variable to database: {e}")
                db.rollback()
            finally:
                db.close()

        try:
            await websocket.send_text(json.dumps({
                "type": "variable_extracted",
                "name": name,
                "value": value,
                "all_variables": session_vars
            }))
        except Exception as e:
            logger.error(f"Failed to send variables update to client: {e}")
        await params.result_callback({"status": "success", "session_variables": session_vars})
    llm.register_function("update_session_variable", update_session_variable_handler)

    async def get_clinic_info_handler(params: FunctionCallParams):
        res = ClinicInfoTool.get_clinic_info()
        await params.result_callback(res)
    llm.register_function("get_clinic_info", get_clinic_info_handler)

    async def check_calendar_availability_handler(params: FunctionCallParams):
        start_time_str = params.arguments.get("start_time_str")
        end_time_str = params.arguments.get("end_time_str")
        res = GoogleCalendarTool.check_calendar_availability(start_time_str, end_time_str)
        await params.result_callback(res)
    llm.register_function("check_calendar_availability", check_calendar_availability_handler)

    async def create_calendar_event_handler(params: FunctionCallParams):
        summary = params.arguments.get("summary")
        start_time_str = params.arguments.get("start_time_str")
        end_time_str = params.arguments.get("end_time_str")
        description = params.arguments.get("description", "")
        res = GoogleCalendarTool.create_calendar_event(summary, start_time_str, end_time_str, description)
        await params.result_callback(res)
    llm.register_function("create_calendar_event", create_calendar_event_handler)

    async def update_calendar_event_handler(params: FunctionCallParams):
        event_id = params.arguments.get("event_id")
        start_time_str = params.arguments.get("start_time_str")
        end_time_str = params.arguments.get("end_time_str")
        res = GoogleCalendarTool.update_calendar_event(event_id, start_time_str, end_time_str)
        await params.result_callback(res)
    llm.register_function("update_calendar_event", update_calendar_event_handler)

    async def delete_calendar_event_handler(params: FunctionCallParams):
        event_id = params.arguments.get("event_id")
        res = GoogleCalendarTool.delete_calendar_event(event_id)
        await params.result_callback(res)
    llm.register_function("delete_calendar_event", delete_calendar_event_handler)


    # 5. Build Pipeline
    activity_monitor = ActivityMonitor(context)
    pipeline_steps = [transport.input()]
    
    if stt_provider == "elevenlabs":
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.processors.audio.vad_processor import VADProcessor
        pipeline_steps.append(VADProcessor(vad_analyzer=SileroVADAnalyzer()))

    pipeline_steps.extend([
        stt,
        aggregators.user(),
        llm,
        activity_monitor,
        tts,
        transport.output(),
        aggregators.assistant()
    ])
    pipeline = Pipeline(pipeline_steps)

    # Start runner
    runner = PipelineRunner()
    task = PipelineTask(pipeline)
    activity_monitor.task = task

    @transport.event_handler("on_connected")
    async def on_connected(transport, client):
        logger.info("WebSocket connected, sending initial LLMContextFrame to trigger greeting.")
        await task.queue_frame(LLMContextFrame(context))
    try:
        await runner.run(task)
    finally:
        await session.close()
