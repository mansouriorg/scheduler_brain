from ortools.sat.python import cp_model
from .models import ScheduleRequest, ScheduleResponse, ScheduledClass

def generate_schedule(data: ScheduleRequest) -> ScheduleResponse:
    # --- PRE-CHECK: PROFESSOR WORKLOAD (Constraint 8) ---
    # We check this BEFORE running the AI. If a professor is assigned 50 hours 
    # but their max is 40, it is physically impossible. Don't even try to solve it.
    prof_hours = {}
    for c in data.classes:
        if c.professorId:
            prof_hours[c.professorId] = prof_hours.get(c.professorId, 0) + (c.durationMinutes / 60.0)

    for p in data.professors:
        if prof_hours.get(p.id, 0) > p.maxHoursPerWeek:
            return ScheduleResponse(
                success=False,
                scheduledClasses=[],
                message=f"Impossible Setup: Professor {p.name} is assigned {prof_hours.get(p.id, 0)} hours of classes, but their max workload is {p.maxHoursPerWeek}."
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
        'MORNING': [0, 1],
        'AFTERNOON': [2, 3],
        'EVENING': [4],
        'NO_PREFERENCE': [0, 1, 2, 3, 4]
    }

    # --- CREATE VARIABLES ---
    x = {}
    for c in data.classes:
        for r in data.rooms:
            # OPTIMIZATION + CONSTRAINT 7 (ROOM CAPACITY):
            # We ONLY allow the AI to consider this room if the type matches AND the cohort fits!
            if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                for d in range(num_days):
                    for t in range(num_slots):
                        x[(c.id, r.id, d, t)] = model.NewBoolVar(f'c_{c.id}_r_{r.id}_d_{d}_t_{t}')

    # --- HELPER DICTIONARIES ---
    prof_classes = {}
    cohort_classes = {}
    for c in data.classes:
        if c.professorId:
            prof_classes.setdefault(c.professorId, []).append(c)
        if c.cohortId:
            cohort_classes.setdefault(c.cohortId, []).append(c)

    # --- CONSTRAINT 1: Every class must happen EXACTLY ONCE ---
    for c in data.classes:
        valid_assignments = [x[(c.id, r.id, d, t)] for r in data.rooms for d in range(num_days) for t in range(num_slots) if (c.id, r.id, d, t) in x]
        if not valid_assignments:
            return ScheduleResponse(
                success=False, scheduledClasses=[], 
                message=f"Impossible: No valid room found for class {c.courseCode}. Check Room Types AND Room Capacities!"
            )
        model.AddExactlyOne(valid_assignments)

    # --- CONSTRAINT 2: Prevent Room Double-Booking ---
    for r in data.rooms:
        for d in range(num_days):
            for t in range(num_slots):
                classes_in_room = [x[(c.id, r.id, d, t)] for c in data.classes if (c.id, r.id, d, t) in x]
                if classes_in_room:
                    model.AddAtMostOne(classes_in_room)

    # --- CONSTRAINT 3: Prevent Professor Double-Booking ---
    for prof_id, classes in prof_classes.items():
        for d in range(num_days):
            for t in range(num_slots):
                prof_assignments = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms if (c.id, r.id, d, t) in x]
                if prof_assignments:
                    model.AddAtMostOne(prof_assignments)

    # --- CONSTRAINT 4: Prevent Cohort Double-Booking ---
    for cohort_id, classes in cohort_classes.items():
        for d in range(num_days):
            for t in range(num_slots):
                cohort_assignments = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms if (c.id, r.id, d, t) in x]
                if cohort_assignments:
                    model.AddAtMostOne(cohort_assignments)

    # --- CONSTRAINT 5: Spaced Learning ---
    cohort_course_groups = {}
    for c in data.classes:
        if c.cohortId:
            cohort_course_groups.setdefault((c.cohortId, c.courseCode), []).append(c)

    for (cohort_id, course_code), classes in cohort_course_groups.items():
        if len(classes) > 1:
            for d in range(num_days):
                course_sessions = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms for t in range(num_slots) if (c.id, r.id, d, t) in x]
                if course_sessions:
                    model.AddAtMostOne(course_sessions)

    # --- CONSTRAINT 6: Professor Time Preferences ---
    prof_prefs = {p.id: p.timePreference for p in data.professors}
    for c in data.classes:
        if c.professorId and c.professorId in prof_prefs:
            valid_slots = PREF_SLOTS.get(prof_prefs[c.professorId], [0, 1, 2, 3, 4])
            for r in data.rooms:
                for d in range(num_days):
                    for t in range(num_slots):
                        if t not in valid_slots and (c.id, r.id, d, t) in x:
                            model.Add(x[(c.id, r.id, d, t)] == 0)

    # --- CONSTRAINT 9: Fatigue Prevention (Max 2 classes in any 3 consecutive slots) ---
    for cohort_id, classes in cohort_classes.items():
        for d in range(num_days):
            for w_start in range(num_slots - 2): # Sliding window of 3
                window = [w_start, w_start + 1, w_start + 2]
                window_vars = [x[(c.id, r.id, d, t)] for t in window for c in classes for r in data.rooms if (c.id, r.id, d, t) in x]
                if window_vars:
                    model.Add(sum(window_vars) <= 2)

    for prof_id, classes in prof_classes.items():
        for d in range(num_days):
            for w_start in range(num_slots - 2):
                window = [w_start, w_start + 1, w_start + 2]
                window_vars = [x[(c.id, r.id, d, t)] for t in window for c in classes for r in data.rooms if (c.id, r.id, d, t) in x]
                if window_vars:
                    model.Add(sum(window_vars) <= 2)

    # --- CONSTRAINT 10: Campus Travel Times (No teleporting!) ---
    campuses = {r.campusId for r in data.rooms}
    for cohort_id, classes in cohort_classes.items():
        for d in range(num_days):
            for t in range(num_slots - 1): # Look at t and t+1
                for campus_a in campuses:
                    for campus_b in campuses:
                        if campus_a != campus_b:
                            # If at campus A at time t, cannot be at campus B at time t+1
                            at_a = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms if r.campusId == campus_a and (c.id, r.id, d, t) in x]
                            at_b = [x[(c.id, r.id, d, t+1)] for c in classes for r in data.rooms if r.campusId == campus_b and (c.id, r.id, d, t+1) in x]
                            if at_a and at_b:
                                model.Add(sum(at_a) + sum(at_b) <= 1)

    # --- CONSTRAINT 11: Minimize Gaps (Swiss Cheese Schedules) ---
    # This turns the solver from "Feasible" to "Optimal".
    objective_terms = []
    for cohort_id, classes in cohort_classes.items():
        for d in range(num_days):
            active_slots = []
            for t in range(num_slots):
                slot_vars = [x[(c.id, r.id, d, t)] for c in classes for r in data.rooms if (c.id, r.id, d, t) in x]
                if slot_vars:
                    active_t = model.NewBoolVar(f'act_coh_{cohort_id}_d{d}_t{t}')
                    model.Add(active_t == sum(slot_vars))
                    active_slots.append(active_t)
                else:
                    active_slots.append(0)

            # Check middle slots (1, 2, 3) to see if they are a gap
            for t in range(1, num_slots - 1):
                if active_slots[t] is not 0:
                    gap_t = model.NewBoolVar(f'gap_{cohort_id}_d{d}_t{t}')
                    
                    has_before = model.NewBoolVar(f'bef_{cohort_id}_d{d}_t{t}')
                    prior = [a for a in active_slots[:t] if a is not 0]
                    if prior: model.AddMaxEquality(has_before, prior)
                    else: model.Add(has_before == 0)
                        
                    has_after = model.NewBoolVar(f'aft_{cohort_id}_d{d}_t{t}')
                    after = [a for a in active_slots[t+1:] if a is not 0]
                    if after: model.AddMaxEquality(has_after, after)
                    else: model.Add(has_after == 0)
                        
                    # If class before AND class after AND NO class right now -> It's a gap!
                    model.AddBoolOr([has_before.Not(), has_after.Not(), active_slots[t], gap_t])
                    objective_terms.append(gap_t)

    # Tell the AI to minimize the number of Swiss Cheese gaps!
    if objective_terms:
        model.Minimize(sum(objective_terms))

    # --- SOLVE THE PUZZLE ---
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8 
    # Because optimization takes longer than just finding a valid fit, we give it a generous 3 minutes.
    # It will stop early if it finds the mathematically absolute perfect schedule!
    solver.parameters.max_time_in_seconds = 180.0 
    
    print(f"🧠 Brain is OPTIMIZING {len(data.classes)} classes across 8 CPU cores. This might take up to 3 minutes...")
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        opt_status = "PERFECT/OPTIMAL" if status == cp_model.OPTIMAL else "GOOD/FEASIBLE"
        scheduled_classes = []
        for c in data.classes:
            for r in data.rooms:
                if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                    for d in range(num_days):
                        for t in range(num_slots):
                            if solver.Value(x[(c.id, r.id, d, t)]) == 1:
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
            message="Scheduling Failed (Timeout): The AI couldn't find a solution in 3 minutes."
        )
    else:
        return ScheduleResponse(
            success=False, scheduledClasses=[],
            message="Scheduling Failed (Infeasible): This schedule is mathematically impossible. A constraint is blocking it."
        )