import pytest
from fastapi.testclient import TestClient
import os
import datetime

# Set local test DB url before importing app
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import Clinic, Doctor, Availability, Appointment

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    # Clear tables to ensure fresh tests
    db = SessionLocal()
    db.query(Appointment).delete()
    db.query(Availability).delete()
    db.query(Doctor).delete()
    db.query(Clinic).delete()
    db.commit()
    db.close()
    yield
    # Clean up test.db files if desired
    if os.path.exists("./test.db"):
        try:
            os.remove("./test.db")
        except PermissionError:
            pass

def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_clinic_and_doctors_endpoints():
    db = SessionLocal()
    # Add dummy clinic
    clinic = Clinic(
        name="Test Clinic",
        opening_time=datetime.time(9, 0),
        closing_time=datetime.time(17, 0),
        timezone="Asia/Kolkata"
    )
    db.add(clinic)
    db.commit()
    db.refresh(clinic)
    
    # Add dummy doctor
    doc = Doctor(
        name="Dr. Test",
        specialization="Dermatologist",
        languages="en,hi",
        clinic_id=clinic.id,
        active=True
    )
    db.add(doc)
    db.commit()
    db.close()

    # Test /api/clinic
    res = client.get("/api/clinic")
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["name"] == "Test Clinic"

    # Test /api/doctors
    res = client.get("/api/doctors")
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["name"] == "Dr. Test"

    # Test /api/doctors/search
    res = client.get("/api/doctors/search?specialty=Dermatologist")
    assert res.status_code == 200
    assert len(res.json()) == 1
    
    res = client.get("/api/doctors/search?specialty=Cardiologist")
    assert res.status_code == 200
    assert len(res.json()) == 0
