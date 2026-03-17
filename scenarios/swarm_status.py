"""
Scenario: Swarm Status Dashboard
Objective: demonstrate the agent's ability to inspect the full fleet,
identify problems, and proactively issue corrective actions.
"""

MISSION_PROMPT = """\
SIREN, command has requested a full swarm status report mid-mission.

Your task:
1. Call get_swarm_summary for a high-level overview.
2. Call get_all_drone_statuses for full fleet detail.
3. For any drone with battery ≤ 25%: call get_battery_status and recommend action.
4. For any OFFLINE drone: call attempt_drone_recovery.
5. For any drone in CHARGING state: call get_battery_status to check if done.
6. Display the current get_grid_map.
7. Call get_rescue_priority_list to show outstanding survivors.
8. Get the mesh log via get_mesh_log to review recent broadcasts.
9. Produce a structured situation report:

   SWARM STATUS REPORT
   ════════════════════════════════════
   Active drones      : X / Y
   Offline drones     : X
   Low battery alerts : [drone IDs]
   Survivors rescued  : X / Y
   Critical unrescued : X
   Mission complete   : Yes / No
   Recommended action : <your analysis>
   ════════════════════════════════════

Show chain-of-thought for each observation and recommendation.
"""