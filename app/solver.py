from ortools.sat.python import cp_model
from .models import ScheduleRequest, ScheduleResponse, ScheduledClass

def generate_schedule(data: ScheduleRequest) -> ScheduleResponse:
    # 1. Initialize the Constraint Programming Model
    model = cp_model.CpModel()

    # --- DEFINE THE GRID ---
    DAYS = ['MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY']
    SLOTS = [
        ("08:00", "09:30"), # Slot 0 (Morning)
        ("10:00", "11:30"), # Slot 1 (Morning)
        ("13:00", "14:30"), # Slot 2 (Afternoon)
        ("15:00", "16:30"), # Slot 3 (Afternoon)
        ("17:00", "18:30")  # Slot 4 (Evening)
    ]
    num_days = len(DAYS)
    num_slots = len(SLOTS)

    # Map time preference strings to valid slot indices
    PREF_SLOTS = {
        'MORNING': [0, 1],
        'AFTERNOON': [2, 3],
        'EVENING': [4],
        'NO_PREFERENCE': [0, 1, 2, 3, 4]
    }

    # --- CREATE VARIABLES ---
    # x[c, r, d, t] becomes a massive matrix of booleans (True/False).
    x = {}
    for c in data.classes:
        for r in data.rooms:
            # OPTIMIZATION: We ONLY create a variable if the room type matches!
            if c.requiresRoomType == r.roomType:
                for d in range(num_days):
                    for t in range(num_slots):
                        x[(c.id, r.id, d, t)] = model.NewBoolVar(f'c_{c.id}_r_{r.id}_d_{d}_t_{t}')

    # --- CONSTRAINT 1: Every class must happen EXACTLY ONCE ---
    for c in data.classes:
        valid_assignments = []
        for r in data.rooms:
            if c.requiresRoomType == r.roomType:
                for d in range(num_days):
                    for t in range(num_slots):
                        valid_assignments.append(x[(c.id, r.id, d, t)])
        
        # If a class has no valid rooms, fail gracefully.
        if not valid_assignments:
            return ScheduleResponse(
                success=False, 
                scheduledClasses=[], 
                message=f"Impossible: No valid room found for class {c.courseCode} (Requires {c.requiresRoomType})"
            )
            
        model.AddExactlyOne(valid_assignments)

    # --- CONSTRAINT 2: Prevent Room Double-Booking ---
    for r in data.rooms:
        for d in range(num_days):
            for t in range(num_slots):
                classes_in_room_at_time = []
                for c in data.classes:
                    if c.requiresRoomType == r.roomType:
                        classes_in_room_at_time.append(x[(c.id, r.id, d, t)])
                # A room can hold AT MOST ONE class per time slot
                if classes_in_room_at_time:
                    model.AddAtMostOne(classes_in_room_at_time)

    # --- CONSTRAINT 3: Prevent Professor Double-Booking ---
    prof_classes = {}
    for c in data.classes:
        if c.professorId:
            if c.professorId not in prof_classes:
                prof_classes[c.professorId] = []
            prof_classes[c.professorId].append(c)

    for prof_id, classes in prof_classes.items():
        for d in range(num_days):
            for t in range(num_slots):
                prof_assignments_at_time = []
                for c in classes:
                    for r in data.rooms:
                        if c.requiresRoomType == r.roomType:
                            prof_assignments_at_time.append(x[(c.id, r.id, d, t)])
                # A professor can teach AT MOST ONE class per time slot
                if prof_assignments_at_time:
                    model.AddAtMostOne(prof_assignments_at_time)

    # --- CONSTRAINT 4: Prevent Cohort Double-Booking (Students can't be in 2 places!) ---
    cohort_classes = {}
    for c in data.classes:
        if c.cohortId:
            if c.cohortId not in cohort_classes:
                cohort_classes[c.cohortId] = []
            cohort_classes[c.cohortId].append(c)

    for cohort_id, classes in cohort_classes.items():
        for d in range(num_days):
            for t in range(num_slots):
                cohort_assignments_at_time = []
                for c in classes:
                    for r in data.rooms:
                        if c.requiresRoomType == r.roomType:
                            cohort_assignments_at_time.append(x[(c.id, r.id, d, t)])
                # A cohort can attend AT MOST ONE class per time slot
                if cohort_assignments_at_time:
                    model.AddAtMostOne(cohort_assignments_at_time)

    # --- CONSTRAINT 5: Spaced Learning (Max 1 session per course per day for a cohort) ---
    cohort_course_groups = {}
    for c in data.classes:
        if c.cohortId:
            # Group by BOTH Cohort AND Course Code
            key = (c.cohortId, c.courseCode)
            if key not in cohort_course_groups:
                cohort_course_groups[key] = []
            cohort_course_groups[key].append(c)

    for (cohort_id, course_code), classes in cohort_course_groups.items():
        # We only need to spread them out if there are multiple sessions (e.g., LECTURE and TD)
        if len(classes) > 1:
            for d in range(num_days):
                course_sessions_on_this_day = []
                for c in classes:
                    for r in data.rooms:
                        if c.requiresRoomType == r.roomType:
                            for t in range(num_slots):
                                course_sessions_on_this_day.append(x[(c.id, r.id, d, t)])
                
                # A cohort can take AT MOST ONE session of this specific course per day
                if course_sessions_on_this_day:
                    model.AddAtMostOne(course_sessions_on_this_day)

    # --- CONSTRAINT 6: Professor Time Preferences ---
    # Create a quick lookup dictionary mapping prof IDs to their preferences
    prof_prefs = {p.id: p.timePreference for p in data.professors}
    
    for c in data.classes:
        if c.professorId and c.professorId in prof_prefs:
            pref = prof_prefs[c.professorId]
            valid_slots = PREF_SLOTS.get(pref, [0, 1, 2, 3, 4])
            
            for r in data.rooms:
                if c.requiresRoomType == r.roomType:
                    for d in range(num_days):
                        for t in range(num_slots):
                            # If this time slot is NOT in the professor's preferred slots, absolutely ban it!
                            if t not in valid_slots:
                                model.Add(x[(c.id, r.id, d, t)] == 0)

    # --- SOLVE THE PUZZLE ---
    solver = cp_model.CpSolver()
    
    # ✨ THE MAGIC SAUCE FOR MASSIVE DATASETS ✨
    # 1. Enable Multi-threading: Tell it to use 8 CPU cores simultaneously
    solver.parameters.num_search_workers = 8 
    # 2. Extend Timeout: Give it up to 2 minutes to crunch the massive payload
    solver.parameters.max_time_in_seconds = 120.0 
    
    print(f"🧠 Brain is crunching {len(data.classes)} classes across 8 CPU cores...")
    status = solver.Solve(model)

    # --- EXTRACT THE FINAL SCHEDULE ---
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        scheduled_classes = []
        for c in data.classes:
            for r in data.rooms:
                if c.requiresRoomType == r.roomType:
                    for d in range(num_days):
                        for t in range(num_slots):
                            # If the solver set this variable to True (1), we found a match!
                            if solver.Value(x[(c.id, r.id, d, t)]) == 1:
                                start_time, end_time = SLOTS[t]
                                scheduled_classes.append(
                                    ScheduledClass(
                                        classId=c.id,
                                        roomId=r.id,
                                        dayOfWeek=DAYS[d],
                                        startTime=start_time,
                                        endTime=end_time
                                    )
                                )
        
        return ScheduleResponse(
            success=True,
            scheduledClasses=scheduled_classes,
            message=f"Success! Generated schedule for {len(scheduled_classes)} classes."
        )
    elif status == cp_model.UNKNOWN:
        return ScheduleResponse(
            success=False,
            scheduledClasses=[],
            message="Scheduling Failed (Timeout): The AI needed more than 120 seconds. The puzzle is massive!"
        )
    else:
        return ScheduleResponse(
            success=False,
            scheduledClasses=[],
            message="Scheduling Failed (Infeasible): This schedule is mathematically impossible. Check if a professor has too many classes for their restricted time preference."
        )