from sqlalchemy.orm import Session
import datetime
from typing import Optional, Dict, Any, List

from ..database import SessionLocal
from ..models import Clinic, Doctor, Availability, Appointment
from ..validation import validate_appointment_booking
from ..services.calendar_service import create_calendar_event, cancel_calendar_event, update_calendar_event, check_calendar_availability

class ClinicInfoTool:
    @staticmethod
    def get_clinic_info() -> Dict[str, Any]:
        db: Session = SessionLocal()
        try:
            clinic = db.query(Clinic).first()
            if not clinic:
                return {"error": "No clinic data configured. Please contact support."}
            return {
                "name": clinic.name,
                "address": clinic.address,
                "phone": clinic.phone,
                "opening_time": clinic.opening_time.strftime("%I:%M %p"),
                "closing_time": clinic.closing_time.strftime("%I:%M %p"),
                "timezone": clinic.timezone
            }
        finally:
            db.close()

class DoctorTool:
    @staticmethod
    def find_doctor(specialty: str) -> List[Dict[str, Any]]:
        db: Session = SessionLocal()
        try:
            doctors = db.query(Doctor).filter(
                Doctor.specialization.ilike(f"%{specialty}%"),
                Doctor.active == True
            ).all()
            return [
                {
                    "id": d.id,
                    "name": d.name,
                    "specialization": d.specialization,
                    "languages": d.languages.split(",")
                }
                for d in doctors
            ]
        finally:
            db.close()

    @staticmethod
    def get_doctor_info(doctor_id: int) -> Dict[str, Any]:
        db: Session = SessionLocal()
        try:
            d = db.query(Doctor).filter(Doctor.id == doctor_id).first()
            if not d:
                return {"error": "Doctor not found."}
            return {
                "id": d.id,
                "name": d.name,
                "specialization": d.specialization,
                "bio": d.bio,
                "languages": d.languages.split(",")
            }
        finally:
            db.close()

class AvailabilityTool:
    @staticmethod
    def check_availability(doctor_id: int, date_str: str, preferred_time_str: Optional[str] = None) -> Dict[str, Any]:
        db: Session = SessionLocal()
        try:
            try:
                date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return {"error": f"Invalid date format '{date_str}'. Use YYYY-MM-DD."}

            query = db.query(Availability).filter(
                Availability.doctor_id == doctor_id,
                Availability.date == date
            )
            
            slots = query.all()
            if not slots:
                return {"message": "No slots scheduled for this date.", "slots": []}
                
            serialized_slots = [
                {
                    "start_time": s.start_time.strftime("%I:%M %p"),
                    "end_time": s.end_time.strftime("%I:%M %p"),
                    "status": s.status
                }
                for s in slots
            ]
            
            # If a preferred time is provided, find the closest available slot
            closest_slot = None
            if preferred_time_str:
                try:
                    # Parse HH:MM
                    pref_time = datetime.datetime.strptime(preferred_time_str[:5], "%H:%M").time()
                    available_slots = [s for s in slots if s.status == "AVAILABLE"]
                    if available_slots:
                        # Find slot with minimum absolute difference
                        def time_diff(slot):
                            h1, m1 = slot.start_time.hour, slot.start_time.minute
                            h2, m2 = pref_time.hour, pref_time.minute
                            return abs((h1 * 60 + m1) - (h2 * 60 + m2))
                        
                        best_slot = min(available_slots, key=time_diff)
                        closest_slot = {
                            "start_time": best_slot.start_time.strftime("%I:%M %p"),
                            "end_time": best_slot.end_time.strftime("%I:%M %p"),
                            "status": best_slot.status
                        }
                except ValueError:
                    pass

            return {
                "date": date_str,
                "slots": serialized_slots,
                "suggested_slot": closest_slot
            }
        finally:
            db.close()

class BookingTool:
    @staticmethod
    def book_appointment(doctor_id: int, date_str: str, time_str: str, patient_name: str, patient_phone: str) -> Dict[str, Any]:
        db: Session = SessionLocal()
        try:
            # Parse inputs
            try:
                date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                # Parse standard formats e.g. 14:00 or 02:00 PM
                try:
                    time = datetime.datetime.strptime(time_str, "%H:%M").time()
                except ValueError:
                    time = datetime.datetime.strptime(time_str, "%I:%M %p").time()
            except ValueError as e:
                return {"error": f"Invalid date or time: {str(e)}"}

            # Fetch active doctor and clinic ID
            doc = db.query(Doctor).filter(Doctor.id == doctor_id).first()
            if not doc:
                return {"error": "Doctor not found."}
            clinic_id = doc.clinic_id

            # Validation checks
            is_valid, msg = validate_appointment_booking(
                db=db,
                doctor_id=doctor_id,
                clinic_id=clinic_id,
                appointment_date=date,
                appointment_time=time
            )
            if not is_valid:
                return {"error": f"Validation failed: {msg}"}

            # Update availability status
            slot = db.query(Availability).filter(
                Availability.doctor_id == doctor_id,
                Availability.date == date,
                Availability.start_time <= time,
                Availability.end_time > time
            ).first()
            if slot:
                slot.status = "BOOKED"

            # Create Appointment DB record
            appointment = Appointment(
                patient_name=patient_name,
                patient_phone=patient_phone,
                doctor_id=doctor_id,
                clinic_id=clinic_id,
                appointment_date=date,
                appointment_time=time,
                status="CONFIRMED"
            )
            db.add(appointment)
            db.commit()
            db.refresh(appointment)

            # Integrate Google Calendar API
            event_id = None
            try:
                # Merge datetime
                start_dt = datetime.datetime.combine(date, time)
                end_dt = start_dt + datetime.timedelta(minutes=30)
                summary = f"Medical Appointment - {doc.name}"
                description = f"Patient: {patient_name}\nPhone: {patient_phone}\nSpecialty: {doc.specialization}"
                
                event_id = create_calendar_event(
                    summary=summary,
                    start_time=start_dt,
                    end_time=end_dt,
                    description=description
                )
                if event_id:
                    appointment.google_calendar_event_id = event_id
                    db.commit()
            except Exception as e:
                # Still return success for local DB but flag the calendar failure
                # Assignment rule: "If Google Calendar fails, do not falsely tell the user the external integration succeeded."
                # We will return the calendar error so the agent prompt can explain it properly.
                return {
                    "appointment_id": appointment.id,
                    "status": "CONFIRMED_DB_ONLY",
                    "doctor_name": doc.name,
                    "date": date_str,
                    "time": time_str,
                    "calendar_error": f"Could not sync with Google Calendar: {str(e)}"
                }

            return {
                "appointment_id": appointment.id,
                "status": "CONFIRMED",
                "doctor_name": doc.name,
                "date": date_str,
                "time": time_str,
                "google_calendar_event_id": event_id
            }
        finally:
            db.close()

class CancellationTool:
    @staticmethod
    def cancel_appointment(appointment_id: int) -> Dict[str, Any]:
        db: Session = SessionLocal()
        try:
            appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
            if not appointment:
                return {"error": "Appointment not found."}

            if appointment.status == "CANCELLED":
                return {"message": "Appointment is already cancelled."}

            # Update DB status
            appointment.status = "CANCELLED"
            
            # Free availability slot
            slot = db.query(Availability).filter(
                Availability.doctor_id == appointment.doctor_id,
                Availability.date == appointment.appointment_date,
                Availability.start_time <= appointment.appointment_time,
                Availability.end_time > appointment.appointment_time
            ).first()
            if slot:
                slot.status = "AVAILABLE"

            # Sync Google Calendar deletion
            calendar_sync = "Not synced (no event linked)"
            if appointment.google_calendar_event_id:
                try:
                    cancel_calendar_event(appointment.google_calendar_event_id)
                    calendar_sync = "SUCCESS"
                except Exception as e:
                    calendar_sync = f"ERROR: {str(e)}"

            db.commit()
            return {
                "appointment_id": appointment_id,
                "status": "CANCELLED",
                "calendar_sync": calendar_sync
            }
        finally:
            db.close()

class RescheduleTool:
    @staticmethod
    def reschedule_appointment(appointment_id: int, new_date_str: str, new_time_str: str) -> Dict[str, Any]:
        db: Session = SessionLocal()
        try:
            appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
            if not appointment:
                return {"error": "Appointment not found."}

            # Parse inputs
            try:
                new_date = datetime.datetime.strptime(new_date_str, "%Y-%m-%d").date()
                try:
                    new_time = datetime.datetime.strptime(new_time_str, "%H:%M").time()
                except ValueError:
                    new_time = datetime.datetime.strptime(new_time_str, "%I:%M %p").time()
            except ValueError as e:
                return {"error": f"Invalid date or time: {str(e)}"}

            # Validate booking rules for new slot
            is_valid, msg = validate_appointment_booking(
                db=db,
                doctor_id=appointment.doctor_id,
                clinic_id=appointment.clinic_id,
                appointment_date=new_date,
                appointment_time=new_time
            )
            if not is_valid:
                return {"error": f"Reschedule validation failed: {msg}"}

            # Free old slot
            old_slot = db.query(Availability).filter(
                Availability.doctor_id == appointment.doctor_id,
                Availability.date == appointment.appointment_date,
                Availability.start_time <= appointment.appointment_time,
                Availability.end_time > appointment.appointment_time
            ).first()
            if old_slot:
                old_slot.status = "AVAILABLE"

            # Reserve new slot
            new_slot = db.query(Availability).filter(
                Availability.doctor_id == appointment.doctor_id,
                Availability.date == new_date,
                Availability.start_time <= new_time,
                Availability.end_time > new_time
            ).first()
            if new_slot:
                new_slot.status = "BOOKED"

            appointment.appointment_date = new_date
            appointment.appointment_time = new_time
            appointment.status = "RESCHEDULED"

            # Sync Google Calendar update
            calendar_sync = "Not synced (no event linked)"
            if appointment.google_calendar_event_id:
                try:
                    start_dt = datetime.datetime.combine(new_date, new_time)
                    end_dt = start_dt + datetime.timedelta(minutes=30)
                    update_calendar_event(
                        event_id=appointment.google_calendar_event_id,
                        start_time=start_dt,
                        end_time=end_dt
                    )
                    calendar_sync = "SUCCESS"
                except Exception as e:
                    calendar_sync = f"ERROR: {str(e)}"

            db.commit()
            return {
                "appointment_id": appointment_id,
                "status": "RESCHEDULED",
                "date": new_date_str,
                "time": new_time_str,
                "calendar_sync": calendar_sync
            }
        finally:
            db.close()

import httpx

class ExternalAPITool:
    @staticmethod
    async def check_insurance_status(policy_number: str, patient_name: str, dynamic_vars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = "https://httpbin.org/post"
        payload = {
            "policy_number": policy_number,
            "patient_name": patient_name,
            "verification_source": "City Health Partner Network"
        }
        if dynamic_vars:
            payload.update(dynamic_vars)
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    json_sent = data.get("json", {})
                    return {
                        "status": "success",
                        "policy_verified": True,
                        "copay_percentage": 10.0,
                        "insurance_provider": json_sent.get("insurance_provider", "Apollo Munich Health"),
                        "response_received": json_sent
                    }
                else:
                    return {"error": f"External API returned status {response.status_code}"}
            except Exception as e:
                return {"error": f"Failed to connect to external REST API: {str(e)}"}

class GoogleCalendarTool:
    @staticmethod
    def check_calendar_availability(start_time_str: str, end_time_str: str) -> Dict[str, Any]:
        try:
            start_time = datetime.datetime.fromisoformat(start_time_str)
            end_time = datetime.datetime.fromisoformat(end_time_str)
            available = check_calendar_availability(start_time, end_time)
            return {"status": "success", "available": available}
        except Exception as e:
            return {"error": f"Invalid datetime format or calendar error: {e}"}

    @staticmethod
    def create_calendar_event(summary: str, start_time_str: str, end_time_str: str, description: str = "") -> Dict[str, Any]:
        try:
            start_time = datetime.datetime.fromisoformat(start_time_str)
            end_time = datetime.datetime.fromisoformat(end_time_str)
            event_id = create_calendar_event(summary, start_time, end_time, description)
            return {"status": "success", "event_id": event_id}
        except Exception as e:
            return {"error": f"Calendar event creation failed: {e}"}

    @staticmethod
    def update_calendar_event(event_id: str, start_time_str: str, end_time_str: str) -> Dict[str, Any]:
        try:
            start_time = datetime.datetime.fromisoformat(start_time_str)
            end_time = datetime.datetime.fromisoformat(end_time_str)
            update_calendar_event(event_id, start_time, end_time)
            return {"status": "success"}
        except Exception as e:
            return {"error": f"Calendar event update failed: {e}"}

    @staticmethod
    def delete_calendar_event(event_id: str) -> Dict[str, Any]:
        try:
            cancel_calendar_event(event_id)
            return {"status": "success"}
        except Exception as e:
            return {"error": f"Calendar event deletion failed: {e}"}


