"""
Default Scenario
Objective: acts as a generic fallback if no specific scenario is provided.
"""

MISSION_PROMPT = """\
SIREN, generic search and rescue mission initiated.
Proceed with standard operating procedures.
"""

INITIAL_FLEET = [
    {"id": "DRONE_ALPHA",   "x": 0, "y": 0, "battery": 95},
    {"id": "DRONE_BRAVO",   "x": 9, "y": 0, "battery": 80},
    {"id": "DRONE_CHARLIE", "x": 0, "y": 9, "battery": 70},
    {"id": "DRONE_DELTA",   "x": 9, "y": 9, "battery": 60},
    {"id": "DRONE_ECHO",    "x": 5, "y": 0, "battery": 100},
]

INITIAL_SURVIVORS = [
    {"id": "S1", "x": 3, "y": 7, "condition": "critical"},
    {"id": "S2", "x": 8, "y": 2, "condition": "stable"},
    {"id": "S3", "x": 5, "y": 5, "condition": "critical"},
    {"id": "S4", "x": 1, "y": 9, "condition": "moderate"},
    {"id": "S5", "x": 7, "y": 8, "condition": "stable"},
]
