from pydantic import BaseModel
from typing import List, Optional

# These represent the puzzle pieces NestJS will send us
class Room(BaseModel):
    id: str
    name: str
    capacity: int
    roomType: str
    campusId: str    # 👈 NEW: So the AI knows which campus the room is on!
    buildingId: str  # 👈 NEW: So the AI knows which building the room is in!

class Professor(BaseModel):
    id: str
    name: str
    maxHoursPerWeek: float
    timePreference: str  

class ClassBlock(BaseModel):
    id: str
    courseCode: str
    durationMinutes: int
    requiresRoomType: str
    professorId: Optional[str] = None
    cohortId: str  
    cohortHeadcount: int # 👈 NEW: So the AI can check if they physically fit in the room!

class ScheduleRequest(BaseModel):
    rooms: List[Room]
    professors: List[Professor]
    classes: List[ClassBlock]

class ScheduledClass(BaseModel):
    classId: str
    roomId: str
    dayOfWeek: str
    startTime: str
    endTime: str

class ScheduleResponse(BaseModel):
    success: bool
    scheduledClasses: List[ScheduledClass]
    message: str = ""