import os
import asyncio
import logging
from typing import Dict, Any, Optional
import datetime

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from ..config import settings
from .tools import ClinicInfoTool, DoctorTool, AvailabilityTool, BookingTool, CancellationTool, RescheduleTool

logger = logging.getLogger(__name__)

from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
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
SYSTEM_PROMPT = """
You are the AI medical receptionist for City Health Medical Center.

Your main job is to help patients book medical appointments through natural voice conversation.

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

IMPORTANT:
- Never invent doctors, availability, appointment times, or clinic information.
- Always use the backend tools for doctor and appointment information.
- If no suitable doctor or appointment slot is available, clearly tell the patient.
- Do not book an appointment without explicit confirmation from the patient.
- Ask for missing information one question at a time.
- Remember information already provided by the patient during the conversation.

LANGUAGE SUPPORT:
The assistant supports English, Hindi, and Kannada.

At the beginning of the conversation, say:
"Hello, welcome to City Health Medical Center. Which language would you like to use: English, Hindi, or Kannada?"

After the patient selects a language:
- Continue the entire conversation in that language.
- Do not switch languages unless the patient asks you to.
- Understand natural speech in English, Hindi, and Kannada.
- The patient may mix English with Hindi or Kannada naturally. Understand the meaning and respond in the selected language.
- Do not use language names or translations unnecessarily in every response.

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
    token: Optional[str] = None
):
    gemini_key = settings.GEMINI_API_KEY
    deepgram_key = settings.DEEPGRAM_API_KEY
    today_str = datetime.date.today().strftime("%A, %B %d, %Y")
    dynamic_prompt = f"Today's date is {today_str}.\n\n{SYSTEM_PROMPT}"

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

    # 2. Setup Services
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
            name="get_clinic_info",
            description="Retrieve clinic contact info, opening hours, and timezone.",
            properties={},
            required=[]
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
