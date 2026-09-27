"""Generate reviewable English narration for the film from voiceover.json.

The copy contains only public demonstration facts. No application credentials or
transaction signing material are read by this script.
"""

import asyncio
import argparse
import json
from pathlib import Path

import edge_tts


ROOT = Path(__file__).resolve().parent
SEGMENTS = json.loads((ROOT / "voiceover.json").read_text(encoding="utf-8"))
OUT = ROOT / "audio"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="Regenerate only one numbered segment, for example 11")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    for segment in SEGMENTS:
        if args.id and segment["id"] != args.id:
            continue
        output = OUT / f"vo-{segment['id']}.mp3"
        speech = edge_tts.Communicate(
            segment["text"],
            voice="en-US-GuyNeural",
            rate="-8%",
            volume="+0%",
        )
        await speech.save(str(output))
        print(output.name)


if __name__ == "__main__":
    asyncio.run(main())
