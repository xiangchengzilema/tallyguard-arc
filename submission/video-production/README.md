# TallyGuard product film · v16

The current cut is a 3:15, 1920 × 1080, 30 fps product film. It opens with the business pain, gives the animated five-step homepage workflow 18 seconds, then uses actual recorded web-product interaction rather than static page images. The primary workflow follows request `TG-BE9B131D` from editable evidence through the new submit-result modal, finance review, independent approval, simulated finance settlement, and the requester-visible receipt. The second workflow follows `TG-AUTO-40B`, a 40 USDC request automatically settled under a finance-enabled, versioned policy capped at 50 USDC per request and 500 USDC per day.

Both filmed workflows run in the **public simulation workspace**. Their receipt screens show simulated outcomes; no funds moved from the filmed clicks. The closing Arc Activity and Explorer footage shows **separate historical Arc Testnet evidence**: 50 workflow test runs with 40 confirmed transfers of 0.01 test USDC. Those are not the transactions caused by the filmed requests. No tests are presented as customers or production adoption.

English narration is in `voiceover.json`; the Chinese owner-review copy is in `CHINESE_SUBTITLES.md` and `CHINESE_SUBTITLES.srt`. The final output remains English on-screen. The raw new manual-workflow recording and screenshots are in `../../artifacts/video-recording/`. Binary media and renders are local ignored assets under `public/media/` and `out/`.

## Render

From this directory:

```powershell
npm install
npx remotion render src/index.ts TallyGuardFilm out/TallyGuard-product-film-final-v16.mp4 --codec h264 --audio-codec aac --concurrency 4 --crf 20
npx remotion render src/index.ts TallyGuardFilm out/TallyGuard-product-film-final-v16-nobgm.mp4 --codec h264 --audio-codec aac --concurrency 4 --crf 20 --props nobgm-props.json
```

The narration-only version keeps the same visual timeline and voice track without background music. The music asset is `house-vibez.mp3` by Lily J, sourced from Mixkit. [Mixkit's music license](https://mixkit.co/license/modal/musicFree/) covers web/social video use; recheck if the distribution channel changes.
