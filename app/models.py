from pydantic import BaseModel
from typing import List, Optional

# These represent the puzzle pieces NestJS will send us
class Room(BaseModel):
    id: str
    name: str
    capacity: int
    roomType: str
    campusId: str    
    buildingId: str  
    equipment: List[str]  # What does this room have? (Macs, Hoods, Smartboards, etc.)

class Professor(BaseModel):
    id: str
    name: str
    maxHoursPerWeek: float
    timePreference: str  
    unavailableDays: List[str]  # Days they absolutely cannot teach
    isStrictTimeRequirement: bool = False  # 🚨 THE FIX: Python now knows about strict demands!

class ClassBlock(BaseModel):
    id: str
    courseCode: str
    durationMinutes: int
    requiresRoomType: str
    requiredEquipment: List[str]  # What does this class need to function?
    componentType: str  # Is this a LECTURE, TD, or TP?
    professorId: Optional[str] = None
    cohortId: str  
    cohortHeadcount: int 
    studentIds: List[str] # 👈 NEW TIER 3: The exact individual students enrolled in this class!

class FixedClass(BaseModel):
    # A class that is already in the timetable. It keeps its room, day and time;
    # the new classes are placed around it.
    id: str
    roomId: str
    dayOfWeek: str
    startTime: str  # "HH:mm"
    endTime: str
    durationMinutes: int
    professorId: Optional[str] = None
    cohortId: str
    studentIds: List[str] = []

class ScheduleRequest(BaseModel):
    rooms: List[Room]
    professors: List[Professor]
    classes: List[ClassBlock]
    fixedClasses: List[FixedClass] = []  # none when the timetable is empty

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