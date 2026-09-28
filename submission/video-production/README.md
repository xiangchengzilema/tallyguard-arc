# TallyGuard product film · v19

The current cut is a 3:12, 1920 × 1080, 30 fps product film. It opens with six business pains, holds on the homepage's animated five-step workflow, then uses continuous recordings of real web interactions. The manual-request footage shows user submission and result modal, finance selection/review, independent approval, finance-triggered simulated settlement, and the user's receipt under request `TG-33628CF4`. The low-value footage shows finance explicitly enabling a versioned no-touch policy (50 USDC per request, 450 USDC per day), then a 40 USDC request automatically completing in the simulation workspace under request `TG-3CA63499`. A third recording shows approver rejection with a written reason, the user seeing it, clicking **Correct and resubmit**, and opening a corrected new request with the same finance reason under `TG-151377B2`.

All filmed payment outcomes are **public simulation workspace** outcomes; filmed clicks do not move funds. The Arc Activity and Explorer footage is **separate historical Arc Testnet evidence**: 50 workflow runs and 40 confirmed transfers of 0.01 test USDC. Do not claim those are transfers from the filmed requests or real customers.

English narration is in `voiceover.json`; the complete Chinese owner-review copy is in `CHINESE_SUBTITLES.md` and `CHINESE_SUBTITLES.srt`. The raw dynamic browser recordings and frame-QA images are in `../../artifacts/video-recording/`. Binary media and renders are local ignored assets under `public/media/` and `out/`.

## Render

From this directory:

```powershell
npm install
npx remotion render src/index.ts TallyGuardFilm out/TallyGuard-product-film-final-v19.mp4 --codec h264 --audio-codec aac --concurrency 4 --timeout 120000 --crf 20
npx remotion render src/index.ts TallyGuardFilm out/TallyGuard-product-film-final-v19-nobgm.mp4 --codec h264 --audio-codec aac --concurrency 4 --timeout 120000 --crf 20 --props nobgm-props.json
```

The music asset is `house-vibez.mp3` by Lily J, sourced from Mixkit. [Mixkit's music license](https://mixkit.co/license/modal/musicFree/) covers web/social video use; recheck if the distribution channel changes.
