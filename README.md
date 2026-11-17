# HGSS Multi EXP

Patch for Pokémon HeartGold / SoulSilver that makes the whole party gain
experience after every battle, without having to hold the Exp. Share
(*Multi Exp* in the French version).

| Pokémon | EXP gained |
|---|---|
| Fought in the battle | normal share (total ÷ number of participants) |
| Did not fight (alive, not an egg, below level 100) | half of a participant's share |

Change `NON_PARTICIPANT_SHIFT` in `hgss_multi_exp.py` to tune the second row
(`0` = same as participants, `1` = half, `2` = a quarter…).

## Usage

```bash
pip install ndspy
python hgss_multi_exp.py "Pokemon HeartGold.nds"
python hgss_multi_exp.py "Pokemon SoulSilver.nds" -o patched.nds
python hgss_multi_exp.py rom.nds --dry-run   # only check that the ROM is supported
python hgss_multi_exp.py rom.nds --bps       # also build output/<rom name>.bps
python hgss_multi_exp.py rom.nds --bps-only  # build only output/<rom name>.bps
```

BPS patches (apply with Flips or MultiPatch) are built from *your* ROM (diff
original → patched, verified by re-applying it), so you get one per ROM.

Supported (checked): FR, USA, EU, AU, DE, IT HeartGold and SoulSilver. Other releases are attempted and refused if the code differs.

## What is patched

Only overlay 12 (battle engine) is touched, six small Thumb patches in the two
EXP routines (split calculation and per-Pokémon award). The Exp. Share item
itself is left as is; it just no longer changes anything.
