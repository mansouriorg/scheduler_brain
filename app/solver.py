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

    model = cp_model.CpModel()

    # --- DEFINE THE 30-MINUTE MICRO-GRID ---
    DAYS = ['MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY']
    num_days = len(DAYS)
    num_slots = 21 # 21 blocks of 30 mins = 10.5 hours (08:00 to 18:30)

    def get_time_string(block_idx):
        hour = 8 + block_idx // 2
        minute = 30 if block_idx % 2 == 1 else 0
        return f"{hour:02d}:{minute:02d}"

    # --- HIGH-SPEED OCCUPANCY CACHES ---
    # Because classes now span multiple blocks, we pre-build lookup tables for instant overlap checking!
    x = {}
    class_start_vars = {} 
    
    room_active = { (r.id, d, b): [] for r in data.rooms for d in range(num_days) for b in range(num_slots) }
    prof_active = { p.id: { (d, b): [] for d in range(num_days) for b in range(num_slots) } for p in data.professors }
    student_active = { s_id: { (d, b): [] for d in range(num_days) for b in range(num_slots) } for c in data.classes for s_id in c.studentIds }

    print(f"⚙️ Building 30-Minute Micro-Grid for {len(data.classes)} dynamic-duration classes...")

    # --- 1. CREATE VARIABLES & POPULATE CACHES ---
    for c in data.classes:
        blocks_needed = int(c.durationMinutes // 30)
        
        for r in data.rooms:
            if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                if all(req in r.equipment for req in c.requiredEquipment):
                    for d in range(num_days):
                        # A class can only start if it has enough time to finish before the university closes
                        for t in range(num_slots - blocks_needed + 1):
                            var = model.NewBoolVar(f'c_{c.id}_r_{r.id}_d_{d}_t_{t}')
                            x[(c.id, r.id, d, t)] = var
                            class_start_vars.setdefault((c.id, d, t), []).append(var)
                            
                            # ⚡ Instantly populate the Occupancy Caches!
                            for b in range(t, t + blocks_needed):
                                room_active[(r.id, d, b)].append(var)
                                if c.professorId:
                                    prof_active[c.professorId][(d, b)].append(var)
                                for s_id in c.studentIds:
                                    student_active[s_id][(d, b)].append(var)

    # --- CONSTRAINT 1: Every class must start EXACTLY ONCE ---
    for c in data.classes:
        blocks_needed = int(c.durationMinutes // 30)
        valid_starts = [var for d in range(num_days) for t in range(num_slots - blocks_needed + 1) for var in class_start_vars.get((c.id, d, t), [])]
        if not valid_starts:
            return ScheduleResponse(success=False, scheduledClasses=[], message=f"Impossible: No valid room/time combo found for class {c.courseCode}.")
        model.AddExactlyOne(valid_starts)

    # --- CONSTRAINT 2, 3, 4: PREVENT DOUBLE-BOOKING ---
    for r in data.rooms:
        for d in range(num_days):
            for b in range(num_slots):
                if room_active[(r.id, d, b)]: model.AddAtMostOne(room_active[(r.id, d, b)])

    for p in data.professors:
        for d in range(num_days):
            for b in range(num_slots):
                if prof_active[p.id][(d, b)]: model.AddAtMostOne(prof_active[p.id][(d, b)])

    for s_id in student_active.keys():
        for d in range(num_days):
            for b in range(num_slots):
                if student_active[s_id][(d, b)]: model.AddAtMostOne(student_active[s_id][(d, b)])

    # --- CONSTRAINT 5: Spaced Learning (Max 1 session per course per day) ---
    cohort_course_groups = {}
    for c in data.classes:
        if c.cohortId: cohort_course_groups.setdefault((c.cohortId, c.courseCode), []).append(c)

    for (cohort_id, course_code), classes in cohort_course_groups.items():
        if len(classes) > 1:
            for d in range(num_days):
                course_starts_today = []
                for c in classes:
                    blocks_needed = int(c.durationMinutes // 30)
                    for t in range(num_slots - blocks_needed + 1):
                        course_starts_today.extend(class_start_vars.get((c.id, d, t), []))
                if course_starts_today: model.AddAtMostOne(course_starts_today)

    # --- CONSTRAINT 6: Professor Time Preferences & Strict Day Requirements ---
    for c in data.classes:
        if c.professorId and c.professorId in prof_dict:
            prof = prof_dict[c.professorId]
            for r in data.rooms:
                for d in range(num_days):
                    is_day_off = DAYS[d] in prof.unavailableDays
                    blocks_needed = int(c.durationMinutes // 30)
                    for t in range(num_slots - blocks_needed + 1):
                        if (c.id, r.id, d, t) in x:
                            if is_day_off:
                                model.Add(x[(c.id, r.id, d, t)] == 0)

    # --- CONSTRAINT 7: Sequencing (Theory BEFORE Practice) ---
    for (cohort_id, course_code), classes in cohort_course_groups.items():
        lectures = [c for c in classes if c.componentType == 'LECTURE']
        practicals = [c for c in classes if c.componentType in ['TD', 'TP', 'WORKSHOP']]
        if lectures and practicals:
            for lec in lectures:
                for prac in practicals:
                    lec_blocks = int(lec.durationMinutes // 30)
                    prac_blocks = int(prac.durationMinutes // 30)
                    
                    lec_time_index = sum((d * 100 + t) * var for d in range(num_days) for t in range(num_slots - lec_blocks + 1) for var in class_start_vars.get((lec.id, d, t), []))
                    prac_time_index = sum((d * 100 + t) * var for d in range(num_days) for t in range(num_slots - prac_blocks + 1) for var in class_start_vars.get((prac.id, d, t), []))
                    model.Add(lec_time_index < prac_time_index)

    # =========================================================================
    # --- OPTIMIZATION: THE QUEST FOR "THE BEST OF THE BEST" ---
    # =========================================================================
    objective_terms = []

    # 🚨 FIX: Re-build the prof_classes dictionary so the fairness check works!
    prof_classes = {}
    for c in data.classes:
        if c.professorId:
            prof_classes.setdefault(c.professorId, []).append(c)

    # 1. PROFESSOR FAIRNESS (Load Balancing "Bad Slots")
    max_prof_penalty = model.NewIntVar(0, 100, 'max_prof_penalty')
    for prof_id, classes in prof_classes.items():
        bad_assignments = []
        for c in classes:
            # Penalize Monday 08:00 (block 0) and Friday 18:00 (block 20)
            bad_assignments.extend(class_start_vars.get((c.id, 0, 0), [])) 
            bad_assignments.extend(class_start_vars.get((c.id, 4, 20), [])) 
        if bad_assignments:
            model.Add(sum(bad_assignments) <= max_prof_penalty)
            
    objective_terms.append(max_prof_penalty * 20)

    # 2. AVOID SATURDAYS (The Ultimate Penalty)
    SATURDAY_INDEX = 5
    SATURDAY_PENALTY = 1000
    for c in data.classes:
        blocks_needed = int(c.durationMinutes // 30)
        for t in range(num_slots - blocks_needed + 1):
            for var in class_start_vars.get((c.id, SATURDAY_INDEX, t), []):
                objective_terms.append(var * SATURDAY_PENALTY)

    # 🚨 Tell the AI to minimize the penalty score
    if objective_terms: 
        model.Minimize(sum(objective_terms))

    # --- SOLVE THE PUZZLE ---
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8 
    
    # Give the AI exactly 20 minutes (1200 seconds)
    solver.parameters.max_time_in_seconds = 1200.0 
    
    print(f"🧠 Brain is searching for the BEST schedule for {len(data.classes)} classes. Time limit: 20 mins...")
    status = solver.Solve(model)

    # 🚨 Handles BOTH "The Best of the Best" (OPTIMAL) and "A Great Working Schedule" (FEASIBLE)
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        opt_status = "🏆 PERFECT/OPTIMAL" if status == cp_model.OPTIMAL else "✅ WORKING/FEASIBLE"
        
        scheduled_classes = []
        for c in data.classes:
            blocks_needed = int(c.durationMinutes // 30)
            for r in data.rooms:
                if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                    if all(req in r.equipment for req in c.requiredEquipment):
                        for d in range(num_days):
                            for t in range(num_slots - blocks_needed + 1):
                                if (c.id, r.id, d, t) in x and solver.Value(x[(c.id, r.id, d, t)]) == 1:
                                    
                                    # DYNAMIC DURATION TRANSLATION
                                    start_time = get_time_string(t)
                                    end_time = get_time_string(t + blocks_needed) 
                                    
                                    scheduled_classes.append(ScheduledClass(
                                        classId=c.id, roomId=r.id, dayOfWeek=DAYS[d], 
                                        startTime=start_time, endTime=end_time
                                    ))
                                    
        return ScheduleResponse(success=True, scheduledClasses=scheduled_classes, message=f"{opt_status} Schedule Generated! Placed {len(scheduled_classes)} dynamic classes.")
    
    elif status == cp_model.UNKNOWN:
        return ScheduleResponse(success=False, scheduledClasses=[], message="Scheduling Failed (Timeout). The grid was so tight the AI couldn't even find a basic working schedule in 20 minutes.")
    else:
        return ScheduleResponse(success=False, scheduledClasses=[], message="Scheduling Failed (Infeasible): This is mathematically impossible. A strict constraint is blocking it.")