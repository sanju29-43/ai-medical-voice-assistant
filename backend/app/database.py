from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import datetime
from .config import settings
from .models import Base, Clinic, Doctor, Availability

# Use local sqlite for development fallback if DATABASE_URL is not set or default postgres isn't running
DATABASE_URL = settings.DATABASE_URL
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def seed_data():
    db = SessionLocal()
    try:
        # Check if clinic already exists to avoid duplicate seeding
        if db.query(Clinic).first() is not None:
            print("Database already seeded.")
            return
            
        # Create a clinic
        clinic = Clinic(
            name="City Health Medical Center",
            address="123 Main Street, Bangalore",
            phone="+91 80 1234 5678",
            opening_time=datetime.time(9, 0),
            closing_time=datetime.time(18, 0),
            timezone="Asia/Kolkata"
        )
        db.add(clinic)
        db.commit()
        db.refresh(clinic)

        # Create Doctors (20 specific records)
        doctors = [
            Doctor(id=1, name="Dr. Ananya Sharma", specialization="Dermatologist", bio="Experienced dermatologist specializing in acne, pigmentation, hair loss, and common skin conditions.", languages="en,hi", clinic_id=clinic.id, active=True),
            Doctor(id=2, name="Dr. Rahul Mehta", specialization="Cardiologist", bio="Cardiologist specializing in preventive cardiology, hypertension, heart health, and cardiovascular risk management.", languages="en,hi", clinic_id=clinic.id, active=True),
            Doctor(id=3, name="Dr. Priya Nair", specialization="Dentist", bio="Dentist specializing in preventive dentistry, dental fillings, oral hygiene, and routine dental care.", languages="en,kn", clinic_id=clinic.id, active=True),
            Doctor(id=4, name="Dr. Arjun Rao", specialization="General Physician", bio="General physician providing primary care for common illnesses, fever, infections, and general health concerns.", languages="en,kn,hi", clinic_id=clinic.id, active=True),
            Doctor(id=5, name="Dr. Sneha Kulkarni", specialization="Dermatologist", bio="Dermatologist specializing in acne treatment, skin allergies, pigmentation, and hair and scalp conditions.", languages="en,hi,kn", clinic_id=clinic.id, active=True),
            Doctor(id=6, name="Dr. Vikram Singh", specialization="Cardiologist", bio="Cardiologist with experience in heart disease prevention, hypertension, cholesterol management, and cardiac health.", languages="en,hi", clinic_id=clinic.id, active=True),
            Doctor(id=7, name="Dr. Kavya Shetty", specialization="Dentist", bio="Dentist specializing in preventive care, cosmetic dentistry, root canal treatment, and oral health.", languages="en,kn,hi", clinic_id=clinic.id, active=True),
            Doctor(id=8, name="Dr. Kiran Kumar", specialization="General Physician", bio="General physician experienced in primary healthcare, respiratory infections, fever, diabetes monitoring, and routine consultations.", languages="en,kn", clinic_id=clinic.id, active=True),
            Doctor(id=9, name="Dr. Meera Iyer", specialization="Dermatologist", bio="Dermatologist specializing in skin allergies, acne, eczema, pigmentation, and general dermatological care.", languages="en,kn", clinic_id=clinic.id, active=True),
            Doctor(id=10, name="Dr. Aditya Verma", specialization="Cardiologist", bio="Cardiologist specializing in hypertension, cardiac risk assessment, lifestyle-related heart conditions, and preventive care.", languages="en,hi", clinic_id=clinic.id, active=True),
            Doctor(id=11, name="Dr. Pooja Desai", specialization="Dentist", bio="Dentist providing general dental care, oral hygiene guidance, fillings, teeth cleaning, and preventive treatment.", languages="en,hi,kn", clinic_id=clinic.id, active=True),
            Doctor(id=12, name="Dr. Rohan Bhat", specialization="General Physician", bio="General physician specializing in primary care, seasonal illnesses, general health evaluations, and chronic condition monitoring.", languages="en,kn,hi", clinic_id=clinic.id, active=True),
            Doctor(id=13, name="Dr. Neha Joshi", specialization="Dermatologist", bio="Dermatologist focusing on acne, skin infections, pigmentation, allergies, and hair-related concerns.", languages="en,hi", clinic_id=clinic.id, active=True),
            Doctor(id=14, name="Dr. Suresh Rao", specialization="Cardiologist", bio="Cardiologist specializing in cardiovascular health, hypertension, preventive cardiology, and heart disease management.", languages="en,kn,hi", clinic_id=clinic.id, active=True),
            Doctor(id=15, name="Dr. Divya Menon", specialization="Dentist", bio="Dentist specializing in preventive dentistry, restorative treatments, oral hygiene, and routine dental consultations.", languages="en,kn", clinic_id=clinic.id, active=True),
            Doctor(id=16, name="Dr. Manish Gupta", specialization="General Physician", bio="General physician providing primary medical care, health checkups, common illness treatment, and chronic disease monitoring.", languages="en,hi", clinic_id=clinic.id, active=True),
            Doctor(id=17, name="Dr. Aishwarya Pai", specialization="Dermatologist", bio="Dermatologist specializing in acne, eczema, pigmentation, hair loss, and common skin disorders.", languages="en,kn,hi", clinic_id=clinic.id, active=True),
            Doctor(id=18, name="Dr. Naveen Hegde", specialization="Cardiologist", bio="Cardiologist specializing in preventive heart care, hypertension, cardiovascular risk assessment, and cardiac wellness.", languages="en,kn", clinic_id=clinic.id, active=True),
            Doctor(id=19, name="Dr. Shreya Kapoor", specialization="Dentist", bio="Dentist specializing in preventive dental care, cosmetic dentistry, oral hygiene, and routine dental procedures.", languages="en,hi", clinic_id=clinic.id, active=True),
            Doctor(id=20, name="Dr. Ajay Prasad", specialization="General Physician", bio="General physician experienced in primary healthcare, general consultations, fever, infections, and routine health assessments.", languages="en,kn,hi", clinic_id=clinic.id, active=True),
        ]
        
        for doc in doctors:
            db.add(doc)
        db.commit()

        # Create 7 days of availability starting from today
        today = datetime.date.today()
        for i in range(7):
            current_date = today + datetime.timedelta(days=i)
            # Create availability slots for each doctor
            for doc in db.query(Doctor).all():
                # Define some slots between 9 AM and 5 PM
                slots = [
                    (datetime.time(9, 0), datetime.time(9, 30)),
                    (datetime.time(10, 0), datetime.time(10, 30)),
                    (datetime.time(11, 0), datetime.time(11, 30)),
                    (datetime.time(14, 0), datetime.time(14, 30)),
                    (datetime.time(15, 0), datetime.time(15, 30)),
                    (datetime.time(16, 0), datetime.time(16, 30)),
                ]
                for idx, (start, end) in enumerate(slots):
                    # Make some slots BLOCKED or BOOKED for demonstration/testing
                    status = "AVAILABLE"
                    if idx == 2 and doc.specialization == "Dermatologist":
                        status = "BLOCKED"  # Doctor unavailable at 11:00 AM
                    elif idx == 4 and doc.specialization == "Cardiologist":
                        status = "BOOKED"   # Already booked slot
                        
                    avail = Availability(
                        doctor_id=doc.id,
                        date=current_date,
                        start_time=start,
                        end_time=end,
                        status=status
                    )
                    db.add(avail)
        db.commit()
        print("Database seed complete.")
    except Exception as e:
        print(f"Error seeding database: {e}")
        db.rollback()
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=engine)
    seed_data()
