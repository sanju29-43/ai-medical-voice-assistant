from sqlalchemy.orm import Session
import datetime
from .models import Doctor, Clinic, Availability, Appointment, AppointmentStatus

def validate_appointment_booking(
    db: Session,
    doctor_id: int,
    clinic_id: int,
    appointment_date: datetime.date,
    appointment_time: datetime.time,
) -> tuple[bool, str]:
    # 1. Doctor exists
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        return False, "Doctor not found."

    # 2. Doctor is active
    if not doctor.active:
        return False, "Doctor is not currently active."

    # 3. Clinic exists
    clinic = db.query(Clinic).filter(Clinic.id == clinic_id).first()
    if not clinic:
        return False, "Clinic not found."
        
    # Check if doctor belongs to clinic (safety rule)
    if doctor.clinic_id != clinic_id:
        return False, "Doctor is not associated with this clinic."

    # 4. Date is valid
    today = datetime.date.today()
    if appointment_date < today:
        return False, "Appointment date cannot be in the past."

    # 5. Time is valid (within clinic hours)
    if appointment_time < clinic.opening_time or appointment_time >= clinic.closing_time:
        return False, f"Clinic is closed. Opening hours: {clinic.opening_time.strftime('%H:%M')} - {clinic.closing_time.strftime('%H:%M')}."

    # 6. Slot exists
    # Find matching slot in availability table
    slot = db.query(Availability).filter(
        Availability.doctor_id == doctor_id,
        Availability.date == appointment_date,
        Availability.start_time <= appointment_time,
        Availability.end_time > appointment_time
    ).first()
    
    if not slot:
        return False, "Selected time slot is not in the doctor's available calendar schedule."

    # 7. Slot is available
    if slot.status != "AVAILABLE":
        return False, f"The selected slot is currently {slot.status.lower()}."

    # 8. No conflicting appointment exists
    conflict = db.query(Appointment).filter(
        Appointment.doctor_id == doctor_id,
        Appointment.appointment_date == appointment_date,
        Appointment.appointment_time == appointment_time,
        Appointment.status == "CONFIRMED"
    ).first()
    
    if conflict:
        return False, "A conflicting appointment already exists at this time."

    # 9. Booking rules satisfied
    # Additional clinic rule: Appointments must be booked at least 15 minutes in advance from current time if today
    if appointment_date == today:
        now_time = datetime.datetime.now().time()
        now_dt = datetime.datetime.combine(today, now_time)
        app_dt = datetime.datetime.combine(appointment_date, appointment_time)
        if app_dt < now_dt + datetime.timedelta(minutes=15):
            return False, "Appointments must be booked at least 15 minutes in advance."

    return True, "Success"
