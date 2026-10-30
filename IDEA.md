A traffic control simulation of LLMs Talking to each other

- assigns an agent per intersection
- each agent observes the lanes going in and out of the intersection
- uses the idea of "Pressure" and data from neighboring agents to decide the next flow
- chooses between phases, and if to hold them for longer or change
- introduces an Orchestrator that overrides the subagents if it sees gridlock then proceeds to cotrol all affected intersections until resolved, then gives it back to the subagents control
