from fastapi import FastAPI
from .models import ScheduleRequest, ScheduleResponse
from .solver import generate_schedule

app = FastAPI(title="University Scheduler Brain 🧠")

@app.get("/")
def health_check():
    return {"status": "Brain is online, fully charged, and ready to solve! ⚡"}

@app.post("/solve", response_model=ScheduleResponse)
def solve_schedule(payload: ScheduleRequest):
    print(f"📥 Received puzzle with {len(payload.classes)} classes to schedule!")
    
    # Pass the data to our OR-Tools solver
    result = generate_schedule(payload)
    
    return result