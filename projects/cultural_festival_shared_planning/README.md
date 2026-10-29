# Cultural festival: personal purposes and common planning

Run from the repository root:

```sh
python tools/run_planning_example.py \
  --project projects/cultural_festival_shared_planning \
  --choices projects/cultural_festival_shared_planning/choices.json \
  --out runs/festival-purpose-example --seed 0
```

Choose a fresh output directory. `choices.json` contains only actual initial
offers and both acceptances; all subsequent physical and social choices use the
common planner. Omitting it does not invent agreements. This sample does not
load `FestivalStatePolicy`, the old festival GA or the authored three cases.

This is a separate festival-day scenario (08:00–17:00), preserving the six source
people's traits, initial positions, knowledge, inventory and relations. It does
not replace the original three-day world or its school-attendance witnesses.

## Purpose mapping

| Person | Original purpose | Executable interpretation |
|---|---|---|
| 蒼 | 出演の約束を守る | Personally complete the prepared-presence task at 15:00–15:15 under an actually accepted promise. |
| 蒼 | 共同展示への責任を果たす | Personally hand the completed display photo to 灯 by 14:00, as agreed. |
| 蒼 | 不十分な出来を見せたくない | Required costume AND lead rehearsal preparation, also prerequisites of prepared presence. |
| 紗季 | 開演時刻を守る | Complete the sound check, then keep the accepted 15:00–15:15 stage duty. |
| 紗季 | 演劇準備を整える | Complete the sound-preparation task owned by this role. |
| 蓮 | 相手役として出演する | Complete partner rehearsal and the accepted 15:00–15:15 prepared presence. |
| 蓮 | 終盤の一台詞の短縮を相談する | Submit through the common proposal protocol and receive an actual reply; rejection or deferral also counts as a reply. |
| 灯 | 共同展示パネルを完成させる | Complete the display and its verification photo. |
| 灯 | 演劇を潰す意図はない | Retained intention, not converted into a sabotage objective or a fabricated measurable condition. |
| 悠 | 会場を調整する | Complete the venue-preparation task. |
| 実 | 案内放送を準備する | Complete broadcast preparation after actually learning that venue preparation is ready. |

Personal priority values and `required` flags are provisional inputs. They do
not add personality parameters. The practical preparation standard is costume
and rehearsal, not an automatic assessment of acting quality. An accepted line
change currently does not invalidate an earlier rehearsal.

Initial zero task progress is declared public to all six people. Later remote
progress is not shared automatically. In the reference run, 実 visits the venue
preparation site before planning the broadcast task. 蒼 observes the completed
display and hands its photograph to 灯 during the stage promise's waiting slack.

Presence does not imply acting, consent or successful performance. The opening
observer explicitly keeps `performance_started: false` and performance internals
unsupported. Task-based cooperation requests and joint work remain separate work.

See `docs/festival-purpose-migration.md` for the shared design and limitations.
