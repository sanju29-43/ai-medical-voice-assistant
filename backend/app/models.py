from sqlalchemy import Column, Integer, String, Boolean, Date, Time, DateTime, ForeignKey, Enum, text
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
import enum
import datetime

Base = declarative_base()

class AvailabilityStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    BOOKED = "BOOKED"
    BLOCKED = "BLOCKED"

class AppointmentStatus(str, enum.Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"

class Clinic(Base):
    __tablename__ = "clinics"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    opening_time = Column(Time, nullable=False)
    closing_time = Column(Time, nullable=False)
    timezone = Column(String, default="Asia/Kolkata")
    
    doctors = relationship("Doctor", back_populates="clinic")
    appointments = relationship("Appointment", back_populates="clinic")

class Doctor(Base):
    __tablename__ = "doctors"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    specialization = Column(String, nullable=False)
    bio = Column(String, nullable=True)
    languages = Column(String, nullable=False)  # Comma-separated list of languages, e.g. "en,hi,kn"
    clinic_id = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    active = Column(Boolean, default=True)
    
    clinic = relationship("Clinic", back_populates="doctors")
    availability = relationship("Availability", back_populates="doctor")
    appointments = relationship("Appointment", back_populates="doctor")

class Availability(Base):
    __tablename__ = "availability"
    
    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    status = Column(String, default="AVAILABLE")  # AVAILABLE, BOOKED, BLOCKED
    
    doctor = relationship("Doctor", back_populates="availability")

class Appointment(Base):
    __tablename__ = "appointments"
    
    id = Column(Integer, primary_key=True, index=True)
    patient_name = Column(String, nullable=False)
    patient_phone = Column(String, nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    clinic_id = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    appointment_date = Column(Date, nullable=False)
    appointment_time = Column(Time, nullable=False)
    status = Column(String, default="CONFIRMED")  # CONFIRMED, CANCELLED, RESCHEDULED
    google_calendar_event_id = Column(String, nullable=True)
    
    created_at = Column(DateTime, server_default=text("now()"))
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=datetime.datetime.utcnow)
    
    doctor = relationship("Doctor", back_populates="appointments")
    clinic = relationship("Clinic", back_populates="appointments")
