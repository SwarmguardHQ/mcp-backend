"""
Scenario: Rescue Prioritisation (Multiple Survivors)
Objective: when resources are constrained, demonstrate intelligent ordering
of rescues — critical cases always before moderate/stable.
"""

MISSION_PROMPT = """\
SIREN, five survivors have been detected simultaneously after the earthquake.
You have only THREE drones available and limited supplies.

Detected survivors:
  S1 at (3,7) — CRITICAL  — needs medical_kit
  S2 at (8,2) — STABLE    — needs food
  S3 at (5,5) — CRITICAL  — needs medical_kit
  S4 at (1,9) — MODERATE  — needs water
  S5 at (7,8) — STABLE    — needs food

Available drones:
  DRONE_ALPHA   (90% battery, at (0,0))
  DRONE_BRAVO   (75% battery, at (9,0))
  DRONE_CHARLIE (50% battery, at (0,9))
  DRONE_DELTA   — OFFLINE

Supply depots:
  D1 at (0,0): medical_kit, water, food
  D2 at (9,9): rope, tarp, radio  ← no useful items for current survivors

Your task:
1. Call get_rescue_priority_list to confirm the ranked order.
2. Build an explicit assignment plan:
   - Which drone handles which survivor, in what order?
   - Show distance calculations for each leg.
   - Show battery sufficiency check for each assignment.
3. CRITICAL survivors MUST be served before MODERATE or STABLE.
4. When two survivors are equidistant, always choose the higher priority.
5. Execute the plan: collect → move → deliver → mark_rescued for each.
6. After all critical survivors are rescued, address moderate then stable.
7. If a drone runs low, it must charge before taking a new assignment.

Produce a written assignment plan (with reasoning) before making any tool calls.
"""