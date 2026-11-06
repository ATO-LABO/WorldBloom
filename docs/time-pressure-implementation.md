# Opt-in deadline pressure

World-local `time-pressure.yaml` v1 stores authored evidence. Each source links
the registered protagonist and a v1 response goal to that goal's period end or
an existing scheduled event. Public awareness declares knowledge of the deadline
and current logical clock; fact-based awareness requires that actor to know the
registered fact. Optional duration estimates (1..10000 logical steps, including
the current step) require a separate known registered fact. Missing evidence is
neutral. Hidden planner ETA and route cost are never actor estimates.

The per-run switch defaults off. Off/absent normalize identically and add no RNG
draw, sidecar capture or pressure record. Changed runtime source has truthful
changed provenance; tests compare all other events and final RNG state against
the exact pre-change develop Simulation fixture. On captures/hashes evidence
with frozen inputs and connects Simulation, evolve CLI, execution worker and
GA/resume identity. Unsupported explicit/calendar/expansion modes are rejected.

Only an unconditional matching goal-progress candidate receives a multiplier
between 1 and 4. Rest and unrelated/conditional candidates remain neutral; legal
late choices are not removed. The strongest matching source applies. Dividing
by the largest weight before multiplying avoids overflow; an all-one correction
returns the original list unchanged. Selection uses the existing random draw.
Scheduled forced selection and existing priority rules retain their position;
contest sampling stays separate. No model API is added or called.

Actor explanations contain only actor evidence. Author review happens after
selection and labels the planner-duration forecast separately. Actual future
completion/slack remain unknown rather than being represented by the forecast.

World editing uses GET `/api/worlds/{id}/time-pressure`, POST there to save, and
POST `/validate`. Saves require both world and sidecar revisions. Failed or
uncertain writes preserve drafts and block blind retry. Safely shaped missing
references remain readable for repair. Cloning preserves evidence and validates
references before publication. Legacy ZIP import rejects the new sidecar rather
than silently dropping it; older ZIP behavior is unchanged.

This PR is based on develop a573f4b0973fa154ee59aa625a8a1e1ac85f91c1.
That branch does not contain the separate unmerged six-tab world-settings
redesign or help component. This change therefore retains its existing calendar
editor and adds only two persistent panels: time design and deadline/pressure.
The pressure editor can be opened at `#screen=time&tab=pressure`. Calendar and
evidence drafts/save boundaries remain separate. The existing shared UI classes
are reused, with no common CSS or top-bar changes. Supplementary text has a
plain fallback when the optional help component is absent. Other local UI and
economy/stamina changes are excluded.

This develop-based PR tree passed 85 Python tests and all 25 JavaScript scripts.
Those results are separate from the earlier development integration (39 Python
tests and four JavaScript suites). Browser presentation, real HTTP and reloading an already-running server
remain unverified. A previous broad GA crash/recovery regression lost its exec
session after 84 successful rows; its final result is unknown and it was not
restarted. No Studio/EXE deployment, delivery mechanism, new workflow, new
permission, new authentication, approval or merge is included.
