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
    # How long to look for the times that teachers prefer, after the first timetable.
    PREFERENCE_SEARCH_SECONDS = 30.0
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
    # The classes already in the timetable count too: they take place every week.
    for f in data.fixedClasses:
        if f.professorId:
            prof_hours[f.professorId] = prof_hours.get(f.professorId, 0) + (f.durationMinutes / 60.0)

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
    # A cohort follows one class at a time, also when no students are enrolled.
    cohort_active = {}

    # --- CLASSES ALREADY IN THE TIMETABLE (fixedClasses) ---
    # They keep their room, day and time. The 30-minute blocks they use are
    # taken: no new class may use the same room, teacher, cohort or student then.
    def to_minutes(clock):
        hours, minutes = clock.split(":")
        return int(hours) * 60 + int(minutes)

    def blocks_between(start, end):
        # The blocks of the grid (block 0 starts at 08:00) from start to end.
        first = (to_minutes(start) - 8 * 60) // 30
        after = -(-(to_minutes(end) - 8 * 60) // 30)
        return range(max(first, 0), min(after, num_slots))

    taken = {}  # ("room" | "prof" | "cohort" | "student", id) -> {(day, block), ...}
    for f in data.fixedClasses:
        if f.dayOfWeek not in DAYS:
            continue
        d = DAYS.index(f.dayOfWeek)
        owners = [("room", f.roomId), ("cohort", f.cohortId)] + [("student", s_id) for s_id in f.studentIds]
        if f.professorId:
            owners.append(("prof", f.professorId))
        for owner in owners:
            taken.setdefault(owner, set()).update((d, b) for b in blocks_between(f.startTime, f.endTime))

    def busy_times(c):
        # When the teacher, the cohort or a student of class c is already taken.
        owners = [("cohort", c.cohortId)] + [("student", s_id) for s_id in c.studentIds]
        if c.professorId:
            owners.append(("prof", c.professorId))
        return set().union(*(taken.get(owner, set()) for owner in owners))

    pushed_out = set()  # classes that lost some room or time to the fixed classes

    # --- WHEN TEACHERS CANNOT TEACH (Constraint 6) ---
    # The blocks of their days off (unavailableDays) and of their times off
    # (unavailableTimes): no class of theirs may use them.
    away = {}  # teacher id -> {(day, block), ...}
    if ENABLE_PROF_UNAVAILABILITY:
        for p in data.professors:
            blocks = away.setdefault(p.id, set())
            for day in p.unavailableDays:
                if day in DAYS:
                    blocks.update((DAYS.index(day), b) for b in range(num_slots))
            for u in p.unavailableTimes:
                if u.dayOfWeek in DAYS:
                    blocks.update((DAYS.index(u.dayOfWeek), b) for b in blocks_between(u.startTime, u.endTime))
    kept_away = set()  # classes that lost some time to their teacher's times off

    # --- THE TIMES TEACHERS PREFER (a wish, not a rule) ---
    # The parts of the day of the Teachers page, in blocks of the grid (block 0
    # starts at 08:00): from the first block up to the end block. Their classes go
    # there when the timetable allows it (see the second search below).
    PREFERRED_BLOCKS = {
        "MORNING": (0, 8),  # 08:00 to 12:00
        "AFTERNOON": (10, 18),  # 13:00 to 17:00
        "EVENING": (18, num_slots),  # 17:00 to 18:30
    }

    def blocks_outside(c, t):
        # How many 30-minute blocks of class c, when it starts at block t, are
        # outside the part of the day its teacher prefers (0 without a wish).
        prof = prof_dict.get(c.professorId) if c.professorId else None
        wish = PREFERRED_BLOCKS.get(prof.timePreference) if prof else None
        if wish is None:
            return 0
        first, end = wish
        return sum(1 for b in range(t, t + int(c.durationMinutes // 30)) if b < first or b >= end)

    print("⚙️ [LOG] Building continuous Matrix and creating variables...")
    start_time_log = time.time()

    # --- CREATE VARIABLES & POPULATE CACHES ---
    for c in data.classes:
        blocks_needed = int(c.durationMinutes // 30)
        busy = busy_times(c)
        off = away.get(c.professorId, set()) if c.professorId else set()
        
        for r in data.rooms:
            if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                if all(req in r.equipment for req in c.requiredEquipment):
                    room_busy = taken.get(("room", r.id), set())
                    for d in range(num_days):
                        for t in range(num_slots - blocks_needed + 1):
                            if off and any((d, b) in off for b in range(t, t + blocks_needed)):
                                kept_away.add(c.id)
                                continue
                            if (busy or room_busy) and any((d, b) in busy or (d, b) in room_busy for b in range(t, t + blocks_needed)):
                                pushed_out.add(c.id)
                                continue
                            var = model.NewBoolVar(f'c_{c.id}_r_{r.id}_d_{d}_t_{t}')
                            x[(c.id, r.id, d, t)] = var
                            class_start_vars.setdefault((c.id, d, t), []).append(var)
                            
                            for b in range(t, t + blocks_needed):
                                room_active[(r.id, d, b)].append(var)
                                cohort_active.setdefault((c.cohortId, d, b), []).append(var)
                                if c.professorId:
                                    prof_active[c.professorId][(d, b)].append(var)
                                for s_id in c.studentIds:
                                    student_active[s_id][(d, b)].append(var)

    print(f"✅ [LOG] Created {len(x)} boolean variables in {round(time.time() - start_time_log, 2)} seconds.")

    print("⚙️ [LOG] Adding Constraint 1: Every class must start EXACTLY ONCE...")
    # --- CONSTRAINT 1: Every class must start EXACTLY ONCE ---
    # A class whose every possible room and time is taken by the classes already
    # in the timetable cannot be placed: say so plainly.
    placeable = {key[0] for key in x}
    # The same when the teacher of the class cannot teach at any time left.
    for c in data.classes:
        if c.id in kept_away and c.id not in placeable:
            name = prof_dict[c.professorId].name if c.professorId in prof_dict else "its teacher"
            also = " (the other times are taken by the classes already in the timetable)" if c.id in pushed_out else ""
            return ScheduleResponse(
                success=False, scheduledClasses=[],
                message=f"No time is left for {c.courseCode} {c.componentType}: {name} cannot teach at any time it could take place{also}. Change the times off of {name} on the Teachers page, or choose another teacher for it."
            )
    for c in data.classes:
        if c.id in pushed_out and c.id not in placeable:
            return ScheduleResponse(
                success=False, scheduledClasses=[],
                message=f"No room and time is left for {c.courseCode} {c.componentType}: at every time it could take place, the classes already in the timetable use every room it fits in, or its teacher, cohort or students. Clear the timetable and generate it again to place all classes together."
            )
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

    for cohort_vars in cohort_active.values():
        model.AddAtMostOne(cohort_vars)

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
    # Done where the variables are made: a class of a teacher has no start at a
    # time that teacher cannot teach (see "WHEN TEACHERS CANNOT TEACH" above).

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
        # --- THE TIMES TEACHERS PREFER: a second, short search ---
        # It starts from the first timetable (a hint) and looks for one with fewer
        # 30-minute blocks outside the times teachers prefer, for at most
        # PREFERENCE_SEARCH_SECONDS. Without a better one in time, the first stays.
        best = solver
        classes_by_id = {c.id: c for c in data.classes}
        outside_cost = {key: blocks_outside(classes_by_id[key[0]], key[3]) for key in x}
        cost_now = sum(outside_cost[key] for key, var in x.items() if solver.Value(var) == 1)
        if cost_now > 0:
            model.Minimize(sum(outside_cost[key] * var for key, var in x.items() if outside_cost[key] > 0))
            for var in x.values():
                model.AddHint(var, solver.Value(var))
            better = cp_model.CpSolver()
            better.parameters.num_search_workers = 8
            better.parameters.max_time_in_seconds = PREFERENCE_SEARCH_SECONDS
            print(f"[LOG] {cost_now} half hours are outside the times teachers prefer: looking for better times (at most {PREFERENCE_SEARCH_SECONDS:g} seconds)...")
            better_status = better.Solve(model)
            if better_status in (cp_model.OPTIMAL, cp_model.FEASIBLE) and better.ObjectiveValue() < cost_now:
                best = better
                cost_now = round(better.ObjectiveValue())
            print(f"[LOG] {cost_now} half hours stay outside the times teachers prefer.")
        opt_status = "✅ WORKING/SURVIVAL" 
        scheduled_classes = []
        outside = []  # the classes placed (partly) outside the time their teacher prefers
        for c in data.classes:
            blocks_needed = int(c.durationMinutes // 30)
            for r in data.rooms:
                if c.requiresRoomType == r.roomType and c.cohortHeadcount <= r.capacity:
                    if all(req in r.equipment for req in c.requiredEquipment):
                        for d in range(num_days):
                            for t in range(num_slots - blocks_needed + 1):
                                if (c.id, r.id, d, t) in x and best.Value(x[(c.id, r.id, d, t)]) == 1:
                                    start_time = get_time_string(t)
                                    end_time = get_time_string(t + blocks_needed) 
                                    scheduled_classes.append(ScheduledClass(
                                        classId=c.id, roomId=r.id, dayOfWeek=DAYS[d], 
                                        startTime=start_time, endTime=end_time
                                    ))
                                    if blocks_outside(c, t) > 0:
                                        outside.append(c.id)
                                    
        return ScheduleResponse(success=True, scheduledClasses=scheduled_classes, message=f"{opt_status} Schedule Generated! Placed {len(scheduled_classes)} dynamic classes.", outsidePreference=outside)
    
    elif status == cp_model.UNKNOWN:
        return ScheduleResponse(success=False, scheduledClasses=[], message="Scheduling Failed (Timeout). The AI got lost in the billions of combinations.")
    else:
        return ScheduleResponse(success=False, scheduledClasses=[], message="Scheduling Failed (Infeasible): A paradox has returned!")