"""
Scenario: Battery Crisis
Objective: manage a swarm where multiple drones hit low battery mid-mission
while survivors are still unrescued.
"""

MISSION_PROMPT = """\
SIREN, BATTERY EMERGENCY — multiple drones are critically low.

Current state:
  DRONE_ALPHA   — 18% battery, position (7,8), en route to critical survivor S1
  DRONE_BRAVO   — 80% battery, position (2,2), idle
  DRONE_CHARLIE — 12% battery, position (5,5), just detected survivor S3
  DRONE_DELTA   — 65% battery, position (9,9), idle near depot D2

Known survivors (detected):
  S1 at (8,7) — CRITICAL — awaiting medical_kit
  S3 at (5,5) — CRITICAL — awaiting medical_kit

Your task:
1. Run get_battery_status on ALPHA and CHARLIE immediately.
2. Calculate whether each can safely reach their target or must abort.
   Show full distance × 3% cost math + 15% reserve check.
3. For any drone that CANNOT safely complete the delivery:
   a. Call return_to_charging_station
   b. Reassign its target to a healthy drone
   c. Broadcast the reassignment via mesh
4. Continue the supply run with healthy drones (BRAVO, DELTA).
5. Do NOT let any drone go offline — prevention is mandatory.
6. Deliver medical_kits to both critical survivors as fast as possible.

This scenarios tests your ability to balance urgency vs battery safety.
Show every trade-off explicitly in your reasoning.
"""