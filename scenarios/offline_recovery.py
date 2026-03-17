"""
Scenario: Offline Drone Recovery
Objective: handle a drone going offline mid-mission, attempt mesh recovery,
redistribute sectors, and continue the mission.
"""

MISSION_PROMPT = """\
SIREN, DRONE_ECHO has gone offline mid-mission.

Situation:
  DRONE_ECHO was scanning sector SE (x:5-9, y:0-4) when contact was lost.
  Its last known position was (7,3). Battery was at 22%.
  DRONE_ALPHA (85% battery, at (0,9)) has not yet been assigned.
  DRONE_BRAVO (60% battery, at (4,5)) is currently scanning sector CT.
  Survivor S2 at (8,2) — STABLE — is inside ECHO's abandoned sector.

Your task:
1. Confirm ECHO is offline via discover_drones or get_drone_status.
2. Attempt mesh recovery on ECHO — use attempt_drone_recovery.
3. If recovery SUCCEEDS:
   - Check ECHO's recovered battery.
   - If battery > 30%: reassign ECHO to finish sector SE.
   - If battery ≤ 30%: send ECHO to charge, assign ALPHA to SE instead.
4. If recovery FAILS:
   - Redistribute sector SE to ALPHA.
   - Broadcast the redistribution plan to all active drones.
5. Complete the scan of sector SE and detect S2.
6. Deliver water to S2 (STABLE condition).

Show your recovery decision tree explicitly before acting.
Explain what you would do differently if a second drone went offline.
"""