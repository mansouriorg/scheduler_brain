from ortools.sat.python import cp_model
from .models import ScheduleRequest, ScheduleResponse, ScheduledClass
import time

def generate_schedule(data: ScheduleRequest) -> ScheduleResponse:
    # =========================================================================
    # 🛑 THE DIAGNOSER: TOGGLE SWITCHES 🛑
    # =========================================================================
    ENABLE_SPACED_LEARNING = True       
    ENABLE_THEORY_BEFORE_PRACTICE = False  # 🚨 TURNED OFF for the 10-Second Test!
    ENABLE_PROF_UNAVAILABILITY = True
    # =========================================================================

    # --- 1. PRE-FLIGHT SANITY CHECK (LOGGING) ---
    print("\n" + "="*50)
    print("📊 PRE-FLIGHT DATA LOGS")
    print("="*50)
    
    total_class_blocks = sum(int(c.durationMinutes // 30) for c in data.classes)
    total_room_blocks = len(data.rooms) * 6 * 21 # 6 days * 21 blocks
    
    print(f"Total Rooms Available: {len(data.rooms)}")
    print(f"Total Professors: {len(data.professors)}")
    print(f"Total Classes to Schedule: {len(data.classes)}")
    print(f"Total 30-Min Blocks Needed: {total_class_blocks}")
    print(f"Total 30-Min Blocks Available (Capacity): {total_room_blocks}")
    print("="*50 + "\n")

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
    num_slots = 21 

    def get_time_string(block_idx):
        hour = 8 + block_idx // 2
        minute = 30 if block_idx % 2 == 1 else 0
        return f"{hour:02d}:{minute:02d}"

    # --- HIGH-SPEED OCCUPANCY CACHES ---
    x = {}
    class_start_vars = {} 
    
    room_active = { (r.id, d, b): [] for r in data.rooms for d in range(num_days) for b in range(num_slots) }
    prof_active = { p.id: { (d, b): [] for d in range(num_days) for b in range(num_slots) } for p in data.professors }
    student_active = { s_id: { (d, b): [] for d in range(num_days) for b in range(num_slots) } for c in data.classes for s_id in c.studentIds }

    print("⚙️ [LOG] Building continuous Matrix and creating variables...")
    start_time_log = time.time()

    # --- CREATE VARIABLES & POPULATE CACHES ---
    for c in data.classes:
        blocks_needed = int(c.durationMinutes // 30)
        
        for r in data.rooms:
            if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                if all(req in r.equipment for req in c.requiredEquipment):
                    for d in range(num_days):
                        for t in range(num_slots - blocks_needed + 1):
                            var = model.NewBoolVar(f'c_{c.id}_r_{r.id}_d_{d}_t_{t}')
                            x[(c.id, r.id, d, t)] = var
                            class_start_vars.setdefault((c.id, d, t), []).append(var)
                            
                            for b in range(t, t + blocks_needed):
                                room_active[(r.id, d, b)].append(var)
                                if c.professorId:
                                    prof_active[c.professorId][(d, b)].append(var)
                                for s_id in c.studentIds:
                                    student_active[s_id][(d, b)].append(var)

    print(f"✅ [LOG] Created {len(x)} boolean variables in {round(time.time() - start_time_log, 2)} seconds.")

    print("⚙️ [LOG] Adding Constraint 1: Every class must start EXACTLY ONCE...")
    # --- CONSTRAINT 1: Every class must start EXACTLY ONCE ---
    for c in data.classes:
        blocks_needed = int(c.durationMinutes // 30)
        valid_starts = [var for d in range(num_days) for t in range(num_slots - blocks_needed + 1) for var in class_start_vars.get((c.id, d, t), [])]
        if not valid_starts:
            return ScheduleResponse(success=False, scheduledClasses=[], message=f"Impossible: No valid room/time combo found for class {c.courseCode}. Check Room capacity vs Headcount!")
        model.AddExactlyOne(valid_starts)

    print("⚙️ [LOG] Adding Constraints 2, 3, 4: Preventing Room, Prof, and Student double-booking...")
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

    cohort_course_groups = {}
    for c in data.classes:
        if c.cohortId: cohort_course_groups.setdefault((c.cohortId, c.courseCode), []).append(c)

    print(f"⚙️ [LOG] Adding Constraint 5: Spaced Learning (Status: {ENABLE_SPACED_LEARNING})...")
    # --- CONSTRAINT 5: Spaced Learning (WITH LOOPHOLE FOR STRICT PROFESSORS) ---
    if ENABLE_SPACED_LEARNING:
        for (cohort_id, course_code), classes in cohort_course_groups.items():
            if len(classes) > 1:
                
                # 🚨 THE FIX: Check if ANY class in this group is taught by a "Strict" professor
                has_strict_prof = False
                for c in classes:
                    if c.professorId and c.professorId in prof_dict:
                        if prof_dict[c.professorId].isStrictTimeRequirement:
                            has_strict_prof = True
                            break
                
                # If there is a strict professor, we skip the Spaced Learning rule entirely for this course!
                if not has_strict_prof:
                    for d in range(num_days):
                        course_starts_today = []
                        for c in classes:
                            blocks_needed = int(c.durationMinutes // 30)
                            for t in range(num_slots - blocks_needed + 1):
                                course_starts_today.extend(class_start_vars.get((c.id, d, t), []))
                        if course_starts_today: model.AddAtMostOne(course_starts_today)

    print(f"⚙️ [LOG] Adding Constraint 6: Professor Unavailability (Status: {ENABLE_PROF_UNAVAILABILITY})...")
    # --- CONSTRAINT 6: Professor Unavailability (TOGGLEABLE) ---
    if ENABLE_PROF_UNAVAILABILITY:
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

    print(f"⚙️ [LOG] Adding Constraint 7: Theory BEFORE Practice (Status: {ENABLE_THEORY_BEFORE_PRACTICE})...")
    # --- CONSTRAINT 7: Theory BEFORE Practice (TOGGLEABLE) ---
    if ENABLE_THEORY_BEFORE_PRACTICE:
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
    # --- OPTIMIZATION (THE SOFT CONSTRAINTS) ---
    # =========================================================================
    objective_terms = []

    prof_classes = {}
    for c in data.classes:
        if c.professorId:
            prof_classes.setdefault(c.professorId, []).append(c)

    # 1. PROFESSOR FAIRNESS
    max_prof_penalty = model.NewIntVar(0, 100, 'max_prof_penalty')
    for prof_id, classes in prof_classes.items():
        bad_assignments = []
        for c in classes:
            bad_assignments.extend(class_start_vars.get((c.id, 0, 0), [])) 
            bad_assignments.extend(class_start_vars.get((c.id, 4, 20), [])) 
        if bad_assignments:
            model.Add(sum(bad_assignments) <= max_prof_penalty)
            
    objective_terms.append(max_prof_penalty * 20)

    # 2. AVOID SATURDAYS
    SATURDAY_INDEX = 5
    SATURDAY_PENALTY = 1000
    for c in data.classes:
        blocks_needed = int(c.durationMinutes // 30)
        for t in range(num_slots - blocks_needed + 1):
            for var in class_start_vars.get((c.id, SATURDAY_INDEX, t), []):
                objective_terms.append(var * SATURDAY_PENALTY)

    # 🚨 SURVIVAL MODE: We are temporarily commenting out the Minimization 
    # so the AI doesn't spend 20 minutes looking for "Perfection".
    # if objective_terms: 
    #     model.Minimize(sum(objective_terms))

    print("🚀 [LOG] Handing over to the C++ SAT Solver Engine. This is where the heavy lifting happens!")

    # --- SOLVER EXECUTION ---
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8 
    solver.parameters.max_time_in_seconds = 1200.0 
    
    # 🚨 INSTANT REFLEX: Stop the exact microsecond you find a working schedule!
    solver.parameters.stop_after_first_solution = True 
    
    print(f"🧠 Brain is in SURVIVAL MODE. Toggles: Spaced={ENABLE_SPACED_LEARNING}, Unavail={ENABLE_PROF_UNAVAILABILITY}, TheoryFirst={ENABLE_THEORY_BEFORE_PRACTICE}")
    
    solve_start = time.time()
    status = solver.Solve(model)
    solve_end = time.time()
    
    print(f"🏁 [LOG] Solver finished in {round(solve_end - solve_start, 2)} seconds. Status Code: {status}")

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        opt_status = "✅ WORKING/SURVIVAL" 
        scheduled_classes = []
        for c in data.classes:
            blocks_needed = int(c.durationMinutes // 30)
            for r in data.rooms:
                if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                    if all(req in r.equipment for req in c.requiredEquipment):
                        for d in range(num_days):
                            for t in range(num_slots - blocks_needed + 1):
                                if (c.id, r.id, d, t) in x and solver.Value(x[(c.id, r.id, d, t)]) == 1:
                                    start_time = get_time_string(t)
                                    end_time = get_time_string(t + blocks_needed) 
                                    scheduled_classes.append(ScheduledClass(
                                        classId=c.id, roomId=r.id, dayOfWeek=DAYS[d], 
                                        startTime=start_time, endTime=end_time
                                    ))
                                    
        return ScheduleResponse(success=True, scheduledClasses=scheduled_classes, message=f"{opt_status} Schedule Generated! Placed {len(scheduled_classes)} dynamic classes.")
    
    elif status == cp_model.UNKNOWN:
        return ScheduleResponse(success=False, scheduledClasses=[], message="Scheduling Failed (Timeout). The AI got lost in the billions of combinations.")
    else:
        return ScheduleResponse(success=False, scheduledClasses=[], message="Scheduling Failed (Infeasible): A paradox has returned!")