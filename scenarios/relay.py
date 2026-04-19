"""
Scenario: ACO Swarm Deployment
Objective: Demonstrate Ant Colony Optimization (pheromone-guided search) and 
dynamic mesh networking under heavy battery constraints across a wide grid.
"""

MISSION_PROMPT = """\
Save all survivors in the area. 

Note: This is a massive, multi-sector long-distance search.
You will need to heavily rely on your Pheromone Grid logic to guide the swarm efficiently.
Relays will be crucial for the distant infrastructure.
Watch the deadlock breaker if drones run low on fuel!
"""

INITIAL_FLEET = [
    {"id": "DRONE_ALPHA",   "x": 0, "y": 0, "battery": 90, "status": "idle"},
    {"id": "DRONE_BRAVO",   "x": 0, "y": 0, "battery": 75, "status": "idle"},
    {"id": "DRONE_CHARLIE", "x": 0, "y": 0, "battery": 60, "status": "idle"},
    {"id": "DRONE_DELTA",   "x": 0, "y": 0, "battery": 85, "status": "idle"},
    {"id": "DRONE_ECHO",    "x": 0, "y": 0, "battery": 100, "status": "idle"},
]

INITIAL_SURVIVORS = [
    # Far deep zones (Requires mesh relay and heavy battery burn)
    {"id": "S1", "x": 12, "y": 13, "condition": "critical"},
    {"id": "S2", "x": 13, "y": 12, "condition": "moderate"},
    {"id": "S3", "x": 2, "y": 16, "condition": "stable"},
    
    # Mid ranges
    {"id": "S4", "x": 5, "y": 1, "condition": "critical"},
    {"id": "S5", "x": 5, "y": 3, "condition": "moderate"},
    {"id": "S6", "x": 14, "y": 5, "condition": "stable"},
    
    # Close
    {"id": "S7", "x": 1, "y": 8, "condition": "stable"},
]
