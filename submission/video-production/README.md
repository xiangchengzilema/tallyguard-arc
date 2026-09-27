# TallyGuard product film

The current cut is a 2:59, 1920 × 1080, 30 fps product film. Its core is recorded web-product interaction, not a slideshow. The first complete workflow follows request `TG-7B7B38C1` from evidence confirmation through finance review, independent approval, finance settlement, and the requester-visible receipt. The second follows `TG-AUTO-40B`, a 40 USDC request automatically settled under a finance-enabled, versioned policy capped at 50 USDC per request and 500 USDC per day.

Both filmed workflows run in the **public simulation workspace**. Their receipt screens show simulated outcomes and did not move funds. The closing Arc Activity and Explorer screens are **separate historical Arc Testnet evidence** from the synthetic 50-workflow campaign, which confirmed 40 transfers of 0.01 test USDC. They are not the transactions caused by the filmed requests. The film does not present tests as customers or production adoption.

English narration is in `voiceover.json`; the full Chinese translation for owner review is in `CHINESE_SUBTITLES.md`. The output remains English on-screen. Recording scripts, screenshots, and QA sheets are in `../../artifacts/video-recording/`. Binary media and renders are local, ignored assets under `public/media/` and `out/`; review licenses, size, and account data before publishing them.

## Render

From this directory:

```powershell
npm install
npx remotion render src/index.ts TallyGuardFilm out/TallyGuard-3min-product-film-final-v9.mp4 --codec h264 --audio-codec aac --concurrency 4 --crf 20
```

The composition supports `{ "withBgm": false }` for a narration-only version. The music asset is `house-vibez.mp3` by Lily J, sourced from Mixkit. [Mixkit's music license](https://mixkit.co/license/modal/musicFree/) covers web/social video use; recheck if the distribution channel changes.
