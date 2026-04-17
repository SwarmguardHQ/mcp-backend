"""
Scenario: Relay Mission
Objective: demonstrate the automated deployment of a mesh relay drone 
when the command agent attempts a long-distance move.
"""

MISSION_PROMPT = """\
Save all survivors in the area. 

Note: This is a long-distance mission. You will need to utilize the automated relay system (as defined in your core Operational Rules) to maintain connectivity while searching distant sectors.
"""

INITIAL_FLEET = [
    {"id": "DRONE_ALPHA",   "x": 0, "y": 0, "battery": 65, "status": "idle"},
    {"id": "DRONE_BRAVO",   "x": 0, "y": 0, "battery": 100, "status": "idle"},
    {"id": "DRONE_CHARLIE", "x": 0, "y": 0, "battery": 80, "status": "idle"},
    {"id": "DRONE_DELTA",   "x": 0, "y": 0, "battery": 60, "status": "idle"},
    {"id": "DRONE_ECHO",   "x": 0, "y": 0, "battery": 100, "status": "idle"},
]

INITIAL_SURVIVORS = [
    {"id": "S1", "x": 1, "y": 9, "condition": "critical"},
    {"id": "S2", "x": 1, "y": 8, "condition": "stable"},
    {"id": "S3", "x": 5, "y": 1, "condition": "critical"},
    {"id": "S4", "x": 5, "y": 3, "condition": "moderate"},
]
