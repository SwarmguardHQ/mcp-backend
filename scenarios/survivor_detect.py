"""
Scenario: Survivor Detection
Objective: systematically scan the full 10×10 grid and detect all survivors
using thermal and acoustic tools, then build the rescue priority list.
"""

MISSION_PROMPT = """\
SIREN, search-and-rescue sweep initiated after a major earthquake.
Survivor locations are UNKNOWN — you must find them via scanning.

MISSION — Survivor Detection:
1. Discover the active fleet.
2. Decompose the 10×10 grid into sectors (NW, NE, SW, SE, CT).
3. Assign one drone per sector based on battery and position.
4. For each sector: move drone through scan waypoints (every 2 cells),
   run thermal_scan at each waypoint.
5. If thermal returns weak results near rubble, follow up with acoustic_scan.
6. After all sectors are covered, call get_rescue_priority_list.
7. Broadcast the priority list to all drones via mesh radio.
8. Produce a final map using get_grid_map.

Rules:
  - Show sector assignment reasoning (battery math required).
  - Recall any drone reaching 25% battery before it finishes its sector.
  - Redistribute uncovered cells to a healthy drone.
  - All survivor detections auto-broadcast — confirm mesh receipt.
"""