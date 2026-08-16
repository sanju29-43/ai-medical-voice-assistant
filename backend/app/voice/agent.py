import os
import asyncio
import logging
from typing import Dict, Any, Optional

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from ..config import settings
from .tools import ClinicInfoTool, DoctorTool, AvailabilityTool, BookingTool, CancellationTool, RescheduleTool

logger = logging.getLogger(__name__)

from pipecat.transports.websocket.server import WebsocketServerTransport, SingleClientWebsocketServerParams
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame, TextFrame, UserStartedSpeakingFrame, VADUserStartedSpeakingFrame, BotStoppedSpeakingFrame, EndFrame

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
SYSTEM_PROMPT = """You are a polite, helpful AI medical receptionist for City Health Medical Center. 
Your goal is to assist patients with booking, rescheduling, cancelling appointments, and answering clinic or doctor-related queries.

CORE RULES:
1. Speak naturally.
2. Ask only ONE question at a time to prevent overwhelming the user.
3. Maintain session conversation context.
4. Use backend tools whenever clinic data or actions are required.
5. NEVER invent or hallucinate doctors, appointment slots, or clinic info. Rely solely on backend tool output.
6. Ask for patient name and phone number and confirm details explicitly BEFORE calling book_appointment.
7. Maintain the selected language (English, Hindi, or Kannada) throughout the conversation.
8. NEVER use markdown symbols (like bolding with **), asterisks (*), headers, or bullet points. Write only plain conversational text so that the text-to-speech system does not read out punctuation/symbols.

BOOKING PROCEDURE:
Step 1: Ask what specialty (Dermatologist, Cardiologist, Dentist, or General Physician) or doctor name they are looking for.
Step 2: Use `find_doctor` or `get_clinic_info` to get doctor options.
Step 3: Suggest doctor options to the patient.
Step 4: Identify preferred date (e.g. tomorrow, next Monday) and approximate preferred time.
Step 5: Call `check_availability` to check actual slots.
Step 6: Tell the patient the actual available slot.
Step 7: Ask for patient's name and phone number.
Step 8: Ask for confirmation before booking (e.g. "I can book Dr. Sharma for tomorrow at 10 AM. Would you like me to book it?").
Step 9: Call `book_appointment` only after confirmation.
Step 10: Confirm success only after receiving a successful tool response.

LANGUAGE PREFERENCE:
- At the start, greet the patient by saying: "Hello, welcome to City Health Medical Center. Which language would you like to use: English, Hindi, or Kannada?". Do not write or say any native scripts or parenthetical translations like (हिंदी) or (ಕನ್ನಡ).
- Once the user selects, immediately switch and conduct the complete conversation in that language.
- Under language examples:
  - English: "I want to book a dermatologist tomorrow at 4 PM."
  - Hindi: "मैं कल 4 बजे एक त्वचा विशेषज्ञ (dermatologist) के साथ अपॉइंटमेंट बुक करना चाहता हूँ।"
  - Kannada: "ನಾನು ನಾಳೆ ಸಂಜೆ 4 ಗಂಟೆಗೆ ಚರ್ಮರೋಗ ತಜ್ಞರ (dermatologist) ಭೇಟಿಯನ್ನು ಕಾಯ್ದಿರಿಸಲು ಬಯಸುತ್ತೇನೆ."

FALLBACK RULES:
- If a tool returns no data or an error: say "I don't have that information."
- If database/clinic config is empty: say "No clinic data configured. Please contact support."
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
    transport_type: str = "websocket",
    language: str = "en",
    room_url: Optional[str] = None,
    token: Optional[str] = None,
    websocket_port: int = 8765
):
    # Select components
    groq_key = settings.GROQ_API_KEY
    deepgram_key = settings.DEEPGRAM_API_KEY

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
        # Fallback to local Websocket transport
        logger.info(f"Starting agent on Websocket transport host: localhost, port: {websocket_port}")
        ws_params = SingleClientWebsocketServerParams(
            audio_out_enabled=True,
            audio_in_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            serializer=AudioSerializer()
        )
        transport = WebsocketServerTransport(ws_params, host="localhost", port=websocket_port)

    # 2. Setup Services
    # LLM
    llm = GroqLLMService(api_key=groq_key, model="openai/gpt-oss-20b")
    
    # STT (Deepgram is required)
    stt = DeepgramSTTService(api_key=deepgram_key, language=language)

    # TTS (Sarvam Bulbul v3 is the fixed TTS provider)
    sarvam_key = settings.SARVAM_API_KEY
    if not sarvam_key:
        logger.error("SARVAM_API_KEY environment variable is missing or empty!")
        raise ValueError("SARVAM_API_KEY is not configured on the backend. Please add it to your .env file.")

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
                    "type": "integer"
                },
                "new_date": {
                    "type": "string"
                },
                "new_time": {
                    "type": "string"
                }
            },
            required=["appointment_id", "new_date", "new_time"]
        ),
        FunctionSchema(
            name="get_clinic_info",
            description="Retrieve clinic contact info, opening hours, and timezone.",
            properties={},
            required=[]
        )
    ]

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}], tools=tools)
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

    async def get_clinic_info_handler(params: FunctionCallParams):
        res = ClinicInfoTool.get_clinic_info()
        await params.result_callback(res)
    llm.register_function("get_clinic_info", get_clinic_info_handler)

    # 5. Build Pipeline
    activity_monitor = ActivityMonitor(context)
    pipeline = Pipeline([
        transport.input(),
        stt,
        aggregators.user(),
        llm,
        activity_monitor,
        tts,
        transport.output(),
        aggregators.assistant()
    ])

    # Start runner
    runner = PipelineRunner()
    task = PipelineTask(pipeline)
    activity_monitor.task = task
    await runner.run(task)
