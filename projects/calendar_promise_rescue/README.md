# Shared calendar planning example

Run from the repository root (output must be outside the checkout):

```
python scripts/calendar_sim.py --world projects/calendar_promise_rescue/world.yaml --choices projects/calendar_promise_rescue/choices.json --out ../example-calendar-run
```

The three explicit commands establish an offered agreement and both parties'
acceptances. All subsequent travel, work, rest and handoff choices are generated
by the shared goal planner at runtime. Input choices do not prescribe that path.

World conditions and calendar periods define how success/failure is judged;
subjects own their priorities and required conditions. Native task/energy rules
supply action preconditions, duration, resource costs and recovery. This example
adds no scenario-specific Python and makes no external API/model calls.
