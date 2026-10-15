# Level events and puzzle scripts

The extracted DRM/area-PCM scene contains more than geometry and object
intros. `Level.PuzzleInstances` points to a relocatable graph of events. Each
event has:

- an event number and five saved variables;
- a list of event objects (instances, signals, terrain groups, other events,
  areas, and VM objects);
- one or more action lines;
- an optional condition script and an optional action script per line.

The scripts are a small 16-bit stack VM implemented by `src/Game/EVENT.c`.
The extractor preserves the serialized words and labels the stable pieces:
object/number pushes, attribute references, arithmetic/comparison operators,
and camera-related attributes. It does not execute scripts or guess
attribute-specific operand widths, so raw JSON remains the source of truth.

## Extract a report

From the repository root:

```powershell
python tools/event_extractor.py `
  'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA\MRLOCK\BIN\MRLOCK6.PCM' `
  --output build\mrlock6-events.md
```

Use `--json` for a machine-readable report containing complete p-code word
arrays and all relocatable offsets.

## How to read the report

`EVENT_ProcessEvents` walks the puzzle array every frame. Events with a
non-negative number are ordinary event handlers. Negative-number entries are
puppet-show/definition sequences; they are advanced through
`processingPuppetShow` when another event invokes them. A condition script is
evaluated first. If it returns true, the corresponding action script runs.
Action scripts can yield a timer, which stores the current script position and
resumes later through the event timer queue.

`MRLOCK6.PCM` contains nine puzzle entries. Its entries `-2` and `-3` are
large camera/puppet-show sequences built from `campath` and `spldum` intros;
entry `-1` closes that sequence. Ordinary entries `1`, `2`, `3`, `4`, `6`, and
`7` include conditionals keyed from signals, variables, keypad values, and
terrain groups. Entry `1` contains camera distance/tilt changes, while entries
`-2`, `-3`, and `6` use camera spline, position, mode, and screen-wipe
attributes. This is enough to distinguish cinematic camera logic from ordinary
object/state logic without modifying the level.

The word `debug` is not stored as a textual event label in this asset. Debug
display is an engine-side feature (`DEBUG_Show Event Vars` in `src/Game/DEBUG.c`);
the report can still expose the event variables and raw scripts that the debug
overlay would inspect.
