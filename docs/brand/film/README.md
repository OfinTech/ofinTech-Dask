# The 90-second film

A brand piece for a finance lead: the figure, the question behind it, the answer with its source, and the three
limits that make the answer worth having. No screen recordings, so it does not go stale when the app changes, and
it works with the sound off.

| | |
|---|---|
| Output | 1920 × 1080, 30 fps, exactly 90 s, H.264 + AAC |
| Language | American English on screen |
| Sound | A quiet bed at about −21 LUFS. No voiceover; `voiceover-script.md` has one timed to the picture if you want to record it. |

## How it is made

`film.html` is the whole film: one page, one timeline, one function. `window.seek(t)` paints the frame at `t`
seconds and nothing else depends on time, so every render is identical and any second can be inspected on its own.

`frames.py` serves the repo, drives Chromium through the timeline and pipes JPEG frames straight into ffmpeg — no
thousands of PNGs on disk. It renders a chunk at a time to a `.part.mp4` and only renames it on a clean exit, so an
interrupted render never leaves a file that half plays.

```
python docs/brand/film/frames.py probe 2.4,26,53.5     # stills to look at before committing to a render
python docs/brand/film/frames.py 0 15 c0.mp4           # one chunk; repeat for 15-30, 30-45 … 75-90
printf "file '%s'\n" c0.mp4 c1.mp4 c2.mp4 c3.mp4 c4.mp4 c5.mp4 > list.txt
ffmpeg -f concat -safe 0 -i list.txt -c copy silent.mp4
python docs/brand/film/music.py                        # writes music.wav
ffmpeg -i silent.mp4 -i music.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -movflags +faststart ofintech-90s.mp4
```

Chunks all use the same size, frame rate and encoder settings, so they join with `-c copy` and nothing is encoded
twice. Transitions live inside the timeline, never across a chunk boundary.

## Changing it

- **A line of copy, or a timing:** edit `film.html`, probe the frames around it, then re-render only the chunks it
  falls in and concat again.
- **The music:** `music.py` is two slow chords, a little filtered air, and a soft tone at each turn in the story.
  It is written to sit about 23 dB under full scale so a voice can go over it without touching the mix.
- **The figures:** they are illustrative, and the site's footer says so. Keep them plausible for a Gulf logistics
  company; nothing in the film should look like a real client's book.

Do not add a compliance, certification or accuracy claim to the film. The product has none, and the three limits it
does state are the ones the product actually keeps.
