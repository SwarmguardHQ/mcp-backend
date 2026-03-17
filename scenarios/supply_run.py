"""
Scenario: Supplies Collection
Objective: route drones to depots, collect the right supplies,
and deliver to every detected survivor.
"""

MISSION_PROMPT = """\
SIREN, a magnitude 7.2 earthquake has struck. All terrestrial comms are down.

MISSION — Supplies Collection:
Three survivors have been pre-located by a recon pass:
  S1 at (3,7) — CRITICAL — needs medical_kit immediately
  S2 at (8,2) — STABLE   — needs food
  S3 at (5,5) — MODERATE — needs water

Supply depots:
  D1 at (0,0) → medical_kit, water, food
  D2 at (9,9) → rope, tarp, radio

Your task:
1. Discover the fleet and assess battery levels.
2. Plan an optimal supply run that minimises total flight distance.
3. Show full battery-cost calculations for each leg.
4. Prioritise the CRITICAL survivor first.
5. Ensure no drone runs out of battery mid-mission.
6. Mark each survivor as rescued after delivery.

Constraints:
  - At least one drone starts with ≤ 30% battery.
  - Drones carry ONE item at a time.
  - Show chain-of-thought for every decision.
"""