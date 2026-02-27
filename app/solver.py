from ortools.sat.python import cp_model
from .models import ScheduleRequest, ScheduleResponse, ScheduledClass

def generate_schedule(data: ScheduleRequest) -> ScheduleResponse:
    # --- PRE-CHECK: PROFESSOR WORKLOAD ---
    prof_hours = {}
    prof_dict = {p.id: p for p in data.professors}
    for c in data.classes:
        if c.professorId:
            prof_hours[c.professorId] = prof_hours.get(c.professorId, 0) + (c.durationMinutes / 60.0)

    for p in data.professors:
        if prof_hours.get(p.id, 0) > p.maxHoursPerWeek:
            return ScheduleResponse(
                success=False, scheduledClasses=[],
                message=f"Impossible Setup: Professor {p.name} is assigned {prof_hours.get(p.id, 0)} hours, but their max is {p.maxHoursPerWeek}."
            )

    # 1. Initialize the Constraint Programming Model
    model = cp_model.CpModel()

    # --- DEFINE THE GRID ---
    DAYS = ['MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY']
    SLOTS = [
        ("08:00", "09:30"), # 0
        ("10:00", "11:30"), # 1
        ("13:00", "14:30"), # 2
        ("15:00", "16:30"), # 3
        ("17:00", "18:30")  # 4
    ]
    num_days = len(DAYS)
    num_slots = len(SLOTS)

    PREF_SLOTS = {
        'MORNING': [0, 1], 'AFTERNOON': [2, 3], 'EVENING': [4], 'NO_PREFERENCE': [0, 1, 2, 3, 4]
    }

    # --- CREATE VARIABLES (With Capacity & Equipment Filtering!) ---
    x = {}
    for c in data.classes:
        for r in data.rooms:
            # OPTIMIZATION: Check Room Type AND Capacity
            if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                # OPTIMIZATION: Check that the room has ALL required equipment
                has_equipment = all(req in r.equipment for req in c.requiredEquipment)
                if has_equipment:
                    for d in range(num_days):
                        for t in range(num_slots):
                            x[(c.id, r.id, d, t)] = model.NewBoolVar(f'c_{c.id}_r_{r.id}_d_{d}_t_{t}')

    # --- HELPER DICTIONARIES ---
    prof_classes = {}
    cohort_classes = {}
    cohort_course_groups = {}
    student_classes = {} # Tracking individual students!

    for c in data.classes:
        if c.professorId: prof_classes.setdefault(c.professorId, []).append(c)
        if c.cohortId: 
            cohort_classes.setdefault(c.cohortId, []).append(c)
            cohort_course_groups.setdefault((c.cohortId, c.courseCode), []).append(c)
        for s_id in c.studentIds:
            student_classes.setdefault(s_id, []).append(c)

    # --- CONSTRAINT 1: Every class must happen EXACTLY ONCE ---
    for c in data.classes:
        valid_assignments = [x[(c.id, r.id, d, t)] for r in data.rooms for d in range(num_days) for t in range(num_slots) if (c.id, r.id, d, t) in x]
        if not valid_assignments:
            return ScheduleResponse(
                success=False, scheduledClasses=[], 
                message=f"Impossible: No valid room found for class {c.courseCode}. Check Room Types, Capacities, AND Equipment!"
            )
        model.AddExactlyOne(valid_assignments)

    # --- CONSTRAINT 2 & 3: Prevent Double-Booking (Rooms & Profs) ---
    for r in data.rooms:
        for d in range(num_days):
            for t in range(num_slots):
                classes_in_room = [x[(c.id, r.id, d, t)] for c in data.classes if (c.id, r.id, d, t) in x]
                if classes_in_room: model.AddAtMostOne(classes_in_room)

    for prof_id, classes in prof_classes.items():
        for d in range(num_days):
            for t in range(num_slots):
                prof_assignments = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms if (c.id, r.id, d, t) in x]
                if prof_assignments: model.AddAtMostOne(prof_assignments)

    # --- CONSTRAINT 4: Prevent STUDENT Double-Booking (Cross-Cohort Matrix!) ---
    for s_id, classes in student_classes.items():
        if len(classes) > 1: 
            for d in range(num_days):
                for t in range(num_slots):
                    student_assignments = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms if (c.id, r.id, d, t) in x]
                    if len(student_assignments) > 1:
                        model.AddAtMostOne(student_assignments)

    # --- CONSTRAINT 5: Spaced Learning (Max 1 session per course per day) ---
    for (cohort_id, course_code), classes in cohort_course_groups.items():
        if len(classes) > 1:
            for d in range(num_days):
                course_sessions = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms for t in range(num_slots) if (c.id, r.id, d, t) in x]
                if course_sessions: model.AddAtMostOne(course_sessions)

    # --- CONSTRAINT 6: Professor Time Preferences AND Days Off ---
    for c in data.classes:
        if c.professorId and c.professorId in prof_dict:
            prof = prof_dict[c.professorId]
            valid_slots = PREF_SLOTS.get(prof.timePreference, [0, 1, 2, 3, 4])
            
            for r in data.rooms:
                for d in range(num_days):
                    is_day_off = DAYS[d] in prof.unavailableDays
                    for t in range(num_slots):
                        if (c.id, r.id, d, t) in x:
                            if is_day_off or t not in valid_slots:
                                model.Add(x[(c.id, r.id, d, t)] == 0)

    # --- CONSTRAINT 7: Sequencing (Theory BEFORE Practice) ---
    for (cohort_id, course_code), classes in cohort_course_groups.items():
        lectures = [c for c in classes if c.componentType == 'LECTURE']
        practicals = [c for c in classes if c.componentType in ['TD', 'TP', 'WORKSHOP']]
        
        if lectures and practicals:
            for lec in lectures:
                for prac in practicals:
                    lec_time_index = sum((d * 10 + t) * x[(lec.id, r.id, d, t)] for r in data.rooms for d in range(num_days) for t in range(num_slots) if (lec.id, r.id, d, t) in x)
                    prac_time_index = sum((d * 10 + t) * x[(prac.id, r.id, d, t)] for r in data.rooms for d in range(num_days) for t in range(num_slots) if (prac.id, r.id, d, t) in x)
                    model.Add(lec_time_index < prac_time_index)

    # --- CONSTRAINT 8: Fatigue Prevention (Max 2 classes in 3 consecutive slots) ---
    for entity_classes in [cohort_classes, prof_classes]:
        for entity_id, classes in entity_classes.items():
            for d in range(num_days):
                for w_start in range(num_slots - 2):
                    window = [w_start, w_start + 1, w_start + 2]
                    window_vars = [x[(c.id, r.id, d, t)] for t in window for c in classes for r in data.rooms if (c.id, r.id, d, t) in x]
                    if window_vars: model.Add(sum(window_vars) <= 2)

    # --- CONSTRAINT 9: Campus Travel Times ---
    campuses = {r.campusId for r in data.rooms}
    for cohort_id, classes in cohort_classes.items():
        for d in range(num_days):
            for t in range(num_slots - 1):
                for campus_a in campuses:
                    for campus_b in campuses:
                        if campus_a != campus_b:
                            at_a = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms if r.campusId == campus_a and (c.id, r.id, d, t) in x]
                            at_b = [x[(c.id, r.id, d, t+1)] for c in classes for r in data.rooms if r.campusId == campus_b and (c.id, r.id, d, t+1) in x]
                            if at_a and at_b:
                                model.Add(sum(at_a) + sum(at_b) <= 1)

    # =========================================================================
    # --- OPTIMIZATION (THE SOFT CONSTRAINTS) ---
    # =========================================================================
    objective_terms = []

    # 1. Reward Room Stability (Reward = -2)
    for cohort_id, classes in cohort_classes.items():
        if "Open Electives" in cohort_id: continue
        for d in range(num_days):
            for t in range(num_slots - 1):
                for r in data.rooms:
                    room_t1 = [x[(c.id, r.id, d, t)] for c in classes if (c.id, r.id, d, t) in x]
                    room_t2 = [x[(c.id, r.id, d, t+1)] for c in classes if (c.id, r.id, d, t+1) in x]
                    
                    if room_t1 and room_t2:
                        stayed_in_room = model.NewBoolVar(f'stay_{cohort_id}_r{r.id}_d{d}_t{t}')
                        model.Add(sum(room_t1) + sum(room_t2) == 2).OnlyEnforceIf(stayed_in_room)
                        objective_terms.append(stayed_in_room * -2)

    # 2. PROFESSOR FAIRNESS (Load Balancing "Bad Slots")
    max_prof_penalty = model.NewIntVar(0, 100, 'max_prof_penalty')
    
    for prof_id, classes in prof_classes.items():
        bad_assignments = []
        for c in classes:
            for r in data.rooms:
                if (c.id, r.id, 0, 0) in x: bad_assignments.append(x[(c.id, r.id, 0, 0)]) # Mon Morning
                if (c.id, r.id, 4, 4) in x: bad_assignments.append(x[(c.id, r.id, 4, 4)]) # Fri Evening
        
        if bad_assignments:
            model.Add(sum(bad_assignments) <= max_prof_penalty)
            
    objective_terms.append(max_prof_penalty * 20)

    # 3. AVOID SATURDAYS (Last Resort Penalty)
    SATURDAY_INDEX = 5
    SATURDAY_PENALTY = 1000
    for c in data.classes:
        for r in data.rooms:
            for t in range(num_slots):
                if (c.id, r.id, SATURDAY_INDEX, t) in x:
                    objective_terms.append(x[(c.id, r.id, SATURDAY_INDEX, t)] * SATURDAY_PENALTY)

    # --- APPLY ALL OBJECTIVES ---
    if objective_terms:
        model.Minimize(sum(objective_terms))

    # --- SOLVE THE PUZZLE ---
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8 
    # Giving it 4 minutes, but it should solve much faster now without the gap equations!
    solver.parameters.max_time_in_seconds = 240.0 
    
    print(f"🧠 Brain is OPTIMIZING {len(data.classes)} classes. Enforcing Student Overlaps, Professor Fairness, and AVOIDING Saturdays...")
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        opt_status = "PERFECT/OPTIMAL" if status == cp_model.OPTIMAL else "GOOD/FEASIBLE"
        scheduled_classes = []
        for c in data.classes:
            for r in data.rooms:
                if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                    has_eq = all(req in r.equipment for req in c.requiredEquipment)
                    if has_eq:
                        for d in range(num_days):
                            for t in range(num_slots):
                                if (c.id, r.id, d, t) in x and solver.Value(x[(c.id, r.id, d, t)]) == 1:
                                    start_time, end_time = SLOTS[t]
                                    scheduled_classes.append(
                                        ScheduledClass(classId=c.id, roomId=r.id, dayOfWeek=DAYS[d], startTime=start_time, endTime=end_time)
                                    )
        
        return ScheduleResponse(
            success=True,
            scheduledClasses=scheduled_classes,
            message=f"Success! Generated a {opt_status} schedule for {len(scheduled_classes)} classes."
        )
    elif status == cp_model.UNKNOWN:
        return ScheduleResponse(
            success=False, scheduledClasses=[],
            message="Scheduling Failed (Timeout): The AI couldn't find a solution in 4 minutes. Try removing soft constraints or increasing max_time."
        )
    else:
        return ScheduleResponse(
            success=False, scheduledClasses=[],
            message="Scheduling Failed (Infeasible): This schedule is mathematically impossible. A constraint is blocking it."
        )