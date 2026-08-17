import datetime
from app.database import SessionLocal
from app.models import Doctor, Availability

def append_rolling_availability(days_ahead: int = 7):
    db = SessionLocal()
    try:
        # Calculate target date (e.g. 7 days from now)
        target_date = datetime.date.today() + datetime.timedelta(days=days_ahead)
        print(f"Checking availability slots for target date: {target_date}...")

        # Check if slots already exist for this date
        existing = db.query(Availability).filter(Availability.date == target_date).first()
        if existing:
            print(f"Availability slots already exist for {target_date}. Skipping generation.")
            return

        # Fetch active doctors
        doctors = db.query(Doctor).filter(Doctor.active == True).all()
        if not doctors:
            print("No active doctors found to generate slots for.")
            return

        # Predefined time slots
        slots = [
            (datetime.time(9, 0), datetime.time(9, 30)),
            (datetime.time(10, 0), datetime.time(10, 30)),
            (datetime.time(11, 0), datetime.time(11, 30)),
            (datetime.time(14, 0), datetime.time(14, 30)),
            (datetime.time(15, 0), datetime.time(15, 30)),
            (datetime.time(16, 0), datetime.time(16, 30)),
        ]

        # Generate slots
        print(f"Generating slots for {len(doctors)} doctors on {target_date}...")
        for doc in doctors:
            for start, end in slots:
                avail = Availability(
                    doctor_id=doc.id,
                    date=target_date,
                    start_time=start,
                    end_time=end,
                    status="AVAILABLE"
                )
                db.add(avail)
        
        db.commit()
        print(f"Rolling availability slots generated successfully for {target_date}!")
    except Exception as e:
        print(f"Error generating rolling availability: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    append_rolling_availability()
