#!/usr/bin/env python3
"""
HGSS Multi EXP patcher
======================

Makes the whole party gain experience after every battle, as if every Pokemon
were holding the Exp. Share ("Multi Exp" in French), without needing the item.

Behaviour after patching
------------------------
* Pokemon that took part in the battle: normal share of the EXP
  (total / number of participants), exactly like the vanilla game *without*
  an Exp. Share.  Holding the Exp. Share no longer halves their EXP.
* Every other living, non-egg Pokemon of the party: half of a participant's
  share (Gen 6+ behaviour).  Change NON_PARTICIPANT_SHIFT to alter this:
      0 -> same as participants (Gen 8 style)
      1 -> half (default)
      2 -> a quarter, ...
* Fainted Pokemon, eggs and level 100 Pokemon never gain EXP (vanilla rules).

How it works (all changes are in overlay 12, the battle engine)
---------------------------------------------------------------
1. EXP split function (BtlCmd "calc exp split"): the "number of Exp. Share
   holders" branch is bypassed; ctx->expParticipant = max(1, EXP / participants)
   and ctx->expShare = max(1, expParticipant >> NON_PARTICIPANT_SHIFT).
2. EXP award loop: the "is holding Exp. Share" test (hold effect 0x33) is
   replaced by an "is not an egg" test (MON_DATA_IS_EGG), so every non-egg
   party member is visited.
3. EXP award: participants receive expParticipant only, everybody else
   receives expShare only (vanilla added both for participants holding the item).

Only overlay 12 is modified.  The ROM is rebuilt with ndspy.

Usage
-----
    pip install ndspy
    python hgss_multi_exp.py "HeartGold.nds"            # -> "HeartGold (Multi EXP).nds"
    python hgss_multi_exp.py in.nds -o out.nds
    python hgss_multi_exp.py in.nds --dry-run           # only verify that it is patchable
    python hgss_multi_exp.py in.nds --ips               # also build an IPS patch (output/<code>.ips)
    python hgss_multi_exp.py in.nds --ips-only          # build only the IPS patch, no patched ROM

Supported (checked on real dumps): French, USA, Europe, Australia, German and
Italian HeartGold / SoulSilver.  Other releases (Spanish, Japanese, Korean...)
are attempted automatically; the patcher refuses them if the code differs.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import ndspy.code
    import ndspy.rom
except ImportError:  # pragma: no cover
    sys.exit("This tool needs ndspy:  pip install ndspy")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How much EXP a Pokemon that did NOT fight gets, as a right shift of a
# participant's share: 0 = 100 %, 1 = 50 %, 2 = 25 %.
NON_PARTICIPANT_SHIFT = 1

THUMB_NOP = bytes.fromhex("c046")  # mov r8, r8 (ARMv5 has no 'nop' encoding in Thumb)


def thumb_nops(count: int) -> bytes:
    return THUMB_NOP * count


# Patch addresses are given as RAM addresses of the French release, where
# overlay 12 is loaded at 0x22378e0.  The overlay contents are laid out
# identically in all the HGSS releases (only the load address and `bl`
# operands change), so what matters is the offset inside the overlay.
OVERLAY_BASE = 0x22378E0

# ---------------------------------------------------------------------------
# Patch description
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Patch:
    address: int  # RAM address inside the overlay
    old: bytes  # expected original bytes (safety check)
    new: bytes  # replacement, same length as `old`
    note: str
    # (start, end) ranges of `old` ignored by the safety check: Thumb `bl`
    # operands differ between releases because they point into the ARM9.
    wild: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if len(self.old) != len(self.new):
            raise ValueError(f"patch {self.address:#x}: size mismatch")


@dataclass(frozen=True)
class Version:
    name: str
    overlay_id: int
    patches: tuple[Patch, ...]


def _share_block() -> bytes:
    """Thumb code that replaces the tail of the EXP split function.

    Entry: r0 = EXP / participants, r5 = battle context.  Exits to the
    function epilogue (0x223e82e).  Equivalent to:

        if (r0 == 0) r0 = 1;
        ctx->expParticipant = r0;
        r0 >>= NON_PARTICIPANT_SHIFT;
        if (r0 == 0) r0 = 1;
        ctx->expShare = r0;
        return 0;
    """
    code = bytes.fromhex(
        "0028"  # cmp  r0, #0
        "00d1"  # bne  +0          (skip the next instruction)
        "0120"  # movs r0, #1
        "291c"  # adds r1, r5, #0
        "9c31"  # adds r1, #0x9c   (r1 = &ctx->expParticipant)
        "0860"  # str  r0, [r1]
    )
    if NON_PARTICIPANT_SHIFT > 0:
        code += (0x0800 | (NON_PARTICIPANT_SHIFT << 6)).to_bytes(2, "little")  # lsrs r0, r0, #n
        code += bytes.fromhex("00d1")  # bne +0
        code += bytes.fromhex("0120")  # movs r0, #1
    code += bytes.fromhex(
        "4860"  # str  r0, [r1, #4]  (ctx->expShare)
        "07e0"  # b    0x223e82e     (epilogue, only valid at 0x223e808 + size)
    )
    return code


def _french_hgss_patches() -> tuple[Patch, ...]:
    share_old = bytes.fromhex(
        "291c9c310860281c9c300068002803d1281c01219c3001600020a035286002e0"
    )
    share_new = _share_block()
    # The final 'b' must land on 0x223e82e from wherever it ends up.
    b_addr = 0x223E808 + len(share_new) - 2
    disp = (0x223E82E - (b_addr + 4)) // 2
    share_new = share_new[:-2] + (0xE000 | (disp & 0x7FF)).to_bytes(2, "little")
    share_new += thumb_nops((len(share_old) - len(share_new)) // 2)

    return (
        Patch(
            0x223E7BE,
            bytes.fromhex("20d0"),  # beq 0x223e802 (only when nobody holds the item)
            bytes.fromhex("20e0"),  # b   0x223e802 (always take that branch)
            "EXP split: always use the 'no Exp. Share holder' path",
        ),
        Patch(
            0x223E808,
            share_old,
            share_new,
            "EXP split: participants get EXP/n, everyone else gets that >> shift",
        ),
        Patch(
            0x22458FC,
            bytes.fromhex("0621"),  # movs r1, #6   (MON_DATA_HELD_ITEM)
            bytes.fromhex("4c21"),  # movs r1, #0x4c (MON_DATA_IS_EGG)
            "EXP award loop: read the egg flag instead of the held item",
        ),
        Patch(
            0x2245906,
            bytes.fromhex("0004000c0121052232f63bfa332811d0"),
            bytes.fromhex("0028" "17d0") + thumb_nops(6),
            "EXP award loop: every non-egg Pokemon is eligible (was: hold effect == Exp. Share)",
            wild=((8, 12),),
        ),
        Patch(
            0x2245A6A,
            bytes.fromhex("03d0"),  # beq 0x2245a74
            bytes.fromhex("06d0"),  # beq 0x2245a7a
            "EXP award: non-participants jump straight to the 'share' bonus",
        ),
        Patch(
            0x2245A74,
            bytes.fromhex("0698"),  # ldr r0, [sp, #0x18]  (held effect)
            bytes.fromhex("08e0"),  # b 0x2245a88          (participants skip the bonus)
            "EXP award: participants no longer receive the Exp. Share bonus",
        ),
    )


_PATCHES = _french_hgss_patches()

# Releases checked against real dumps.  Any other HGSS code (IPK?/IPG?) is
# still attempted: the byte-for-byte safety check refuses what does not match.
VERSIONS: dict[str, Version] = {
    "IPKF": Version("HeartGold (French)", 12, _PATCHES),
    "IPGF": Version("SoulSilver (French)", 12, _PATCHES),
    "IPKE": Version("HeartGold (USA/Europe)", 12, _PATCHES),
    "IPGE": Version("SoulSilver (USA/Europe/Australia)", 12, _PATCHES),
    "IPGD": Version("SoulSilver (German)", 12, _PATCHES),
    "IPGI": Version("SoulSilver (Italian)", 12, _PATCHES),
}

# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------


def apply_patches(rom: ndspy.rom.NintendoDSRom, version: Version) -> None:
    overlays = rom.loadArm9Overlays()
    overlay = overlays[version.overlay_id]
    data = overlay.data

    for patch in version.patches:
        start = patch.address - OVERLAY_BASE
        end = start + len(patch.old)
        if not 0 <= start < end <= len(data):
            raise SystemExit(f"{patch.address:#x}: outside overlay {version.overlay_id}")
        current = bytearray(data[start:end])
        expected = bytearray(patch.old)
        for lo, hi in patch.wild:
            current[lo:hi] = expected[lo:hi] = bytes(hi - lo)
        if current == bytearray(patch.new):
            raise SystemExit("This ROM already contains the Multi EXP patch.")
        if current != expected:
            raise SystemExit(
                f"{patch.address:#x}: unexpected code ({current.hex()}), "
                "this ROM is not the version the patch was written for."
            )

    for patch in version.patches:
        start = patch.address - OVERLAY_BASE
        new = bytearray(patch.new)
        for lo, hi in patch.wild:  # keep the release's own bytes there
            new[lo:hi] = data[start + lo : start + hi]
        data[start : start + len(new)] = new

    # Store the overlay back (recompressed, as in the original ROM).  When it
    # still fits in its original slot it is zero-padded to the original size,
    # so that no other file moves: the patched ROM then differs from the
    # original in a few places only, which is what makes a small IPS possible.
    original_size = len(rom.files[overlay.fileID])
    new_file = overlay.save(compress=True)
    if len(new_file) < original_size:
        new_file += bytes(original_size - len(new_file))
    rom.files[overlay.fileID] = new_file
    rom.arm9OverlayTable = ndspy.code.saveOverlayTable(overlays)


# ---------------------------------------------------------------------------
# IPS output
# ---------------------------------------------------------------------------

IPS_MAX_OFFSET = 0xFFFFFF  # IPS offsets are 24-bit
IPS_EOF_OFFSET = 0x454F46  # "EOF": a record starting here would end the file
IPS_MAX_RECORD = 0xFFFF
IPS_MERGE_GAP = 6  # merge two changes closer than this (a record costs 5 bytes)


def make_ips(original: bytes, patched: bytes) -> bytes:
    """Build an IPS patch turning `original` into `patched` (same size)."""
    if len(original) != len(patched):
        raise ValueError("ROM size changed, cannot express it as an IPS patch")

    # 1. Collect the changed ranges [start, end).
    ranges: list[list[int]] = []
    size = len(original)
    i = 0
    while i < size:
        if original[i] == patched[i]:
            i += 1
            continue
        start = i
        while i < size and original[i] != patched[i]:
            i += 1
        if ranges and start - ranges[-1][1] < IPS_MERGE_GAP:
            ranges[-1][1] = i
        else:
            ranges.append([start, i])

    # 2. Emit records, split at 64 KiB, dodging the "EOF" offset.
    out = bytearray(b"PATCH")
    for start, end in ranges:
        if end - 1 > IPS_MAX_OFFSET:
            raise SystemExit(
                f"Change at {start:#x} is beyond the 16 MiB an IPS patch can address; "
                "use the patched .nds instead."
            )
        while start < end:
            if start == IPS_EOF_OFFSET:
                start -= 1  # re-send the previous (unchanged) byte
            chunk = min(end - start, IPS_MAX_RECORD)
            out += start.to_bytes(3, "big") + chunk.to_bytes(2, "big") + patched[start : start + chunk]
            start += chunk
    out += b"EOF"
    return bytes(out)


def apply_ips(rom: bytes, ips: bytes) -> bytes:
    """Minimal IPS reader, used to verify the generated patch."""
    if ips[:5] != b"PATCH":
        raise ValueError("not an IPS file")
    data = bytearray(rom)
    pos = 5
    while ips[pos : pos + 3] != b"EOF":
        offset = int.from_bytes(ips[pos : pos + 3], "big")
        length = int.from_bytes(ips[pos + 3 : pos + 5], "big")
        pos += 5
        if length:
            payload = ips[pos : pos + length]
            pos += length
        else:  # RLE record
            length = int.from_bytes(ips[pos : pos + 2], "big")
            payload = ips[pos + 2 : pos + 3] * length
            pos += 3
        if offset + length > len(data):
            data.extend(bytes(offset + length - len(data)))
        data[offset : offset + length] = payload
    return bytes(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the Multi EXP patch to a HGSS ROM.")
    parser.add_argument("rom", type=Path, help="input .nds file")
    parser.add_argument("-o", "--output", type=Path, help="output .nds (default: '<name> (Multi EXP).nds')")
    parser.add_argument("--ips", action="store_true", help="also build an IPS patch")
    parser.add_argument("--ips-only", action="store_true", help="build only the IPS patch (no patched ROM)")
    parser.add_argument("--ips-output", type=Path, help="IPS path (default: output/<game code>.ips)")
    parser.add_argument("--dry-run", action="store_true", help="check the ROM, write nothing")
    args = parser.parse_args(argv)

    rom = ndspy.rom.NintendoDSRom.fromFile(str(args.rom))
    code = bytes(rom.idCode).decode("ascii", "replace")
    version = VERSIONS.get(code)
    if version is None and code[:3] in ("IPK", "IPG"):
        version = Version(f"HGSS release {code} (untested)", 12, _PATCHES)
    if version is None:
        print(f"'{code}' is not a HeartGold/SoulSilver ROM.", file=sys.stderr)
        return 1

    print(f"Detected: {version.name} [{code}]")
    apply_patches(rom, version)
    for patch in version.patches:
        print(f"  {patch.address:#010x}  {patch.note}")

    if args.dry_run:
        print("Dry run: nothing written.")
        return 0

    if args.ips or args.ips_only:
        original = args.rom.read_bytes()
        patched = bytes(rom.save())
        if len(patched) != len(original):
            raise SystemExit("The patched ROM has a different size, no IPS patch can be built.")
        ips = make_ips(original, patched)
        if apply_ips(original, ips) != patched:
            raise SystemExit("Internal error: the IPS patch does not reproduce the patched ROM.")
        ips_path = args.ips_output or Path("output") / f"{code}.ips"
        ips_path.parent.mkdir(parents=True, exist_ok=True)
        ips_path.write_bytes(ips)
        print(f"IPS written: {ips_path} ({len(ips)} bytes)")
        if args.ips_only:
            return 0

    output = args.output or args.rom.with_name(f"{args.rom.stem} (Multi EXP){args.rom.suffix}")
    rom.saveToFile(str(output))
    print(f"Written: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
