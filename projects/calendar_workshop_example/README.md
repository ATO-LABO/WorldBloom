# Night workshop example

Ada makes three pieces at Bench and hands them to Bo. Both explicitly accept
the quantity and recipient before work starts. This is a small mechanism
acceptance input, with no action search or generated story.

The UTC calendar covers November 1–2, 2026. Observation starts at 21:00 and
ends at 01:00 the next day. Work starts at 23:45, takes thirty elapsed minutes,
and finishes at 00:15. Handoff takes two minutes and finishes at the 00:22
promise deadline. The registered new-shift notice at 00:01 occurs while Ada
is working. All these actions occur outside the daytime display band;
display interval size does not scale their durations.

From the repository root, use an output directory outside the checkout:

```sh
python scripts/calendar_sim.py --world projects/calendar_workshop_example/world.yaml --choices projects/calendar_workshop_example/choices.json --out /tmp/workshop-run
```

To stop during work at 00:02 and resume the same run:

```sh
python scripts/calendar_sim.py --world projects/calendar_workshop_example/world.yaml --choices projects/calendar_workshop_example/choices.json --out /tmp/workshop-split --until-minute 1442
python scripts/calendar_sim.py --world projects/calendar_workshop_example/world.yaml --choices projects/calendar_workshop_example/choices.json --out /tmp/workshop-split --resume
```

Saving a separate executable version requires a new destination. Saving does
not start a simulation:

```sh
python scripts/calendar_sim.py --world projects/calendar_workshop_example/world.yaml --freeze-to /tmp/workshop-fixed
python scripts/calendar_sim.py --world /tmp/workshop-fixed/world.yaml --choices projects/calendar_workshop_example/choices.json --out /tmp/workshop-fixed-run
```

The fixed version records the world, contract and both declared person files.
Choice commands remain a separate explicit run input. This example omits
observers; it requires no event-specific observation configuration.
