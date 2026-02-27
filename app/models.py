from pydantic import BaseModel
from typing import List, Optional

# These represent the puzzle pieces NestJS will send us
class Room(BaseModel):
    id: str
    name: str
    capacity: int
    roomType: str

class Professor(BaseModel):
    id: str
    name: str
    maxHoursPerWeek: float

class ClassBlock(BaseModel):
    id: str
    courseCode: str
    durationMinutes: int
    requiresRoomType: str
    professorId: Optional[str] = None
    cohortId: str  # 👈 NEW: The AI needs to know which students take this class!

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