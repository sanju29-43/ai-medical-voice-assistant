from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import datetime
from typing import List, Optional
from pydantic import BaseModel

from .database import get_db, init_db
from .models import Clinic, Doctor, Availability, Appointment, AppointmentStatus, AvailabilityStatus
from .validation import validate_appointment_booking
from .config import settings

app = FastAPI(title="AI Medical Voice Assistant API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize and seed database on startup
@app.on_event("startup")
def startup_event():
    init_db()

# Pydantic Schemas
class ClinicSchema(BaseModel):
    id: int
    name: str
    address: Optional[str]
    phone: Optional[str]
    opening_time: str
    closing_time: str
    timezone: str
    
    class Config:
        orm_mode = True

class DoctorSchema(BaseModel):
    id: int
    name: str
    specialization: str
    bio: Optional[str]
    languages: str
    clinic_id: int
    active: bool
    
    class Config:
        orm_mode = True

class AvailabilitySchema(BaseModel):
    id: int
    doctor_id: int
    date: str
    start_time: str
    end_time: str
    status: str
    
    class Config:
        orm_mode = True

class AppointmentCreateSchema(BaseModel):
    patient_name: str
    patient_phone: str
    doctor_id: int
    clinic_id: int
    appointment_date: str  # YYYY-MM-DD
    appointment_time: str  # HH:MM or HH:MM:SS

class AppointmentUpdateSchema(BaseModel):
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    status: Optional[str] = None

class AppointmentSchema(BaseModel):
    id: int
    patient_name: str
    patient_phone: str
    doctor_id: int
    clinic_id: int
    appointment_date: str
    appointment_time: str
    status: str
    google_calendar_event_id: Optional[str]
    
    class Config:
        orm_mode = True

# REST Endpoints
@app.get("/api/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}

@app.get("/api/clinic", response_model=List[ClinicSchema])
def get_clinics(db: Session = Depends(get_db)):
    clinics = db.query(Clinic).all()
    # Convert times to string for schema compatibility
    result = []
    for c in clinics:
        result.append(ClinicSchema(
            id=c.id,
            name=c.name,
            address=c.address,
            phone=c.phone,
            opening_time=c.opening_time.strftime("%H:%M:%S"),
            closing_time=c.closing_time.strftime("%H:%M:%S"),
            timezone=c.timezone
        ))
    return result

@app.get("/api/doctors", response_model=List[DoctorSchema])
def get_doctors(db: Session = Depends(get_db)):
    return db.query(Doctor).filter(Doctor.active == True).all()

@app.get("/api/doctors/search", response_model=List[DoctorSchema])
def search_doctors(
    specialty: Optional[str] = Query(None, description="Filter by specialty"),
    language: Optional[str] = Query(None, description="Filter by language"),
    db: Session = Depends(get_db)
):
    query = db.query(Doctor).filter(Doctor.active == True)
    if specialty:
        query = query.filter(Doctor.specialization.ilike(f"%{specialty}%"))
    if language:
        query = query.filter(Doctor.languages.ilike(f"%{language}%"))
    return query.all()

@app.get("/api/doctors/{doctor_id}", response_model=DoctorSchema)
def get_doctor(doctor_id: int, db: Session = Depends(get_db)):
    doc = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doc

@app.get("/api/availability", response_model=List[AvailabilitySchema])
def get_availability(
    doctor_id: int,
    date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    query = db.query(Availability).filter(Availability.doctor_id == doctor_id)
    if date:
        try:
            parsed_date = datetime.datetime.strptime(date, "%Y-%m-%d").date()
            query = query.filter(Availability.date == parsed_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
            
    slots = query.all()
    result = []
    for s in slots:
        result.append(AvailabilitySchema(
            id=s.id,
            doctor_id=s.doctor_id,
            date=s.date.strftime("%Y-%m-%d"),
            start_time=s.start_time.strftime("%H:%M:%S"),
            end_time=s.end_time.strftime("%H:%M:%S"),
            status=s.status
        ))
    return result

@app.post("/api/appointments", response_model=AppointmentSchema, status_code=status.HTTP_201_CREATED)
def create_appointment(appointment: AppointmentCreateSchema, db: Session = Depends(get_db)):
    try:
        app_date = datetime.datetime.strptime(appointment.appointment_date, "%Y-%m-%d").date()
        
        # Try HH:MM:SS or HH:MM formats
        time_str = appointment.appointment_time
        if len(time_str.split(":")) == 2:
            app_time = datetime.datetime.strptime(time_str, "%H:%M").time()
        else:
            app_time = datetime.datetime.strptime(time_str, "%H:%M:%S").time()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date/time format: {e}")

    # Validate booking rules
    is_valid, msg = validate_appointment_booking(
        db=db,
        doctor_id=appointment.doctor_id,
        clinic_id=appointment.clinic_id,
        appointment_date=app_date,
        appointment_time=app_time
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=msg)
        
    # Mark the corresponding availability slot as BOOKED
    slot = db.query(Availability).filter(
        Availability.doctor_id == appointment.doctor_id,
        Availability.date == app_date,
        Availability.start_time <= app_time,
        Availability.end_time > app_time
    ).first()
    
    if slot:
        slot.status = "BOOKED"
        
    new_app = Appointment(
        patient_name=appointment.patient_name,
        patient_phone=appointment.patient_phone,
        doctor_id=appointment.doctor_id,
        clinic_id=appointment.clinic_id,
        appointment_date=app_date,
        appointment_time=app_time,
        status="CONFIRMED"
    )
    db.add(new_app)
    db.commit()
    db.refresh(new_app)
    
    return AppointmentSchema(
        id=new_app.id,
        patient_name=new_app.patient_name,
        patient_phone=new_app.patient_phone,
        doctor_id=new_app.doctor_id,
        clinic_id=new_app.clinic_id,
        appointment_date=new_app.appointment_date.strftime("%Y-%m-%d"),
        appointment_time=new_app.appointment_time.strftime("%H:%M:%S"),
        status=new_app.status,
        google_calendar_event_id=new_app.google_calendar_event_id
    )

@app.get("/api/appointments/latest")
def get_latest_appointment(db: Session = Depends(get_db)):
    app_obj = db.query(Appointment).order_by(Appointment.id.desc()).first()
    if not app_obj:
        return {"status": "none"}
    doc = db.query(Doctor).filter(Doctor.id == app_obj.doctor_id).first()
    return {
        "id": app_obj.id,
        "status": app_obj.status,
        "doctor": doc.name if doc else "—",
        "specialty": doc.specialization if doc else "—",
        "date": app_obj.appointment_date.strftime("%Y-%m-%d"),
        "time": app_obj.appointment_time.strftime("%H:%M"),
        "patient_name": app_obj.patient_name
    }

@app.put("/api/appointments/{appointment_id}", response_model=AppointmentSchema)
def update_appointment(appointment_id: int, data: AppointmentUpdateSchema, db: Session = Depends(get_db)):
    app_obj = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not app_obj:
        raise HTTPException(status_code=404, detail="Appointment not found")
        
    # Handle status change (e.g. CANCELLED or RESCHEDULED)
    if data.status:
        valid_statuses = ["CONFIRMED", "CANCELLED", "RESCHEDULED"]
        if data.status.upper() not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {valid_statuses}")
            
        old_status = app_obj.status
        new_status = data.status.upper()
        
        # If cancelling, free up the slot
        if new_status == "CANCELLED" and old_status != "CANCELLED":
            slot = db.query(Availability).filter(
                Availability.doctor_id == app_obj.doctor_id,
                Availability.date == app_obj.appointment_date,
                Availability.start_time <= app_obj.appointment_time,
                Availability.end_time > app_obj.appointment_time
            ).first()
            if slot:
                slot.status = "AVAILABLE"
                
        app_obj.status = new_status

    # Handle rescheduling (date/time change)
    if data.appointment_date or data.appointment_time:
        new_date = datetime.datetime.strptime(data.appointment_date, "%Y-%m-%d").date() if data.appointment_date else app_obj.appointment_date
        
        time_str = data.appointment_time if data.appointment_time else app_obj.appointment_time.strftime("%H:%M:%S")
        if isinstance(time_str, str):
            if len(time_str.split(":")) == 2:
                new_time = datetime.datetime.strptime(time_str, "%H:%M").time()
            else:
                new_time = datetime.datetime.strptime(time_str, "%H:%M:%S").time()
        else:
            new_time = time_str

        # Validate new date/time slot availability
        is_valid, msg = validate_appointment_booking(
            db=db,
            doctor_id=app_obj.doctor_id,
            clinic_id=app_obj.clinic_id,
            appointment_date=new_date,
            appointment_time=new_time
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail=msg)
            
        # Free up the old slot
        old_slot = db.query(Availability).filter(
            Availability.doctor_id == app_obj.doctor_id,
            Availability.date == app_obj.appointment_date,
            Availability.start_time <= app_obj.appointment_time,
            Availability.end_time > app_obj.appointment_time
        ).first()
        if old_slot:
            old_slot.status = "AVAILABLE"
            
        # Book the new slot
        new_slot = db.query(Availability).filter(
            Availability.doctor_id == app_obj.doctor_id,
            Availability.date == new_date,
            Availability.start_time <= new_time,
            Availability.end_time > new_time
        ).first()
        if new_slot:
            new_slot.status = "BOOKED"
            
        app_obj.appointment_date = new_date
        app_obj.appointment_time = new_time
        app_obj.status = "RESCHEDULED"

    db.commit()
    db.refresh(app_obj)
    
    return AppointmentSchema(
        id=app_obj.id,
        patient_name=app_obj.patient_name,
        patient_phone=app_obj.patient_phone,
        doctor_id=app_obj.doctor_id,
        clinic_id=app_obj.clinic_id,
        appointment_date=app_obj.appointment_date.strftime("%Y-%m-%d"),
        appointment_time=app_obj.appointment_time.strftime("%H:%M:%S"),
        status=app_obj.status,
        google_calendar_event_id=app_obj.google_calendar_event_id
    )

@app.delete("/api/appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(appointment_id: int, db: Session = Depends(get_db)):
    app_obj = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not app_obj:
        raise HTTPException(status_code=404, detail="Appointment not found")
        
    # Free up slot before deletion
    slot = db.query(Availability).filter(
        Availability.doctor_id == app_obj.doctor_id,
        Availability.date == app_obj.appointment_date,
        Availability.start_time <= app_obj.appointment_time,
        Availability.end_time > app_obj.appointment_time
    ).first()
    if slot:
        slot.status = "AVAILABLE"
        
    db.delete(app_obj)
    db.commit()
    return None

import asyncio
import traceback
from .voice.agent import run_voice_agent

active_tasks = []

class SessionRequest(BaseModel):
    language: str = "en"
    room_url: Optional[str] = None
    token: Optional[str] = None

async def run_agent_with_logging(*args, **kwargs):
    try:
        await run_voice_agent(*args, **kwargs)
    except Exception as e:
        print(f"CRITICAL: Voice Agent pipeline failed! Error: {e}")
        traceback.print_exc()
        raise e

@app.post("/api/voice/session")
async def start_voice_session(req: SessionRequest):
    # Cancel previous tasks to free port 8765 immediately
    for old_task in active_tasks:
        if not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except asyncio.CancelledError:
                pass
    active_tasks.clear()

    # Determine transport type from environment config setting VOICE_TRANSPORT
    transport_type = settings.VOICE_TRANSPORT.lower()

    if transport_type == "daily":
        if not req.room_url:
            raise HTTPException(status_code=400, detail="room_url is required for daily transport")
        task = asyncio.create_task(run_agent_with_logging(
            transport_type="daily",
            language=req.language,
            room_url=req.room_url,
            token=req.token
        ))
        active_tasks.append(task)
        return {"status": "started", "transport": "daily", "room_url": req.room_url}
    else:
        # Launch local websocket agent
        task = asyncio.create_task(run_agent_with_logging(
            transport_type="websocket",
            language=req.language,
            websocket_port=8765
        ))
        active_tasks.append(task)
        return {"status": "started", "transport": "websocket", "ws_url": "ws://localhost:8765"}


