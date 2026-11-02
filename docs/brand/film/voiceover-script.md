# OfinTech — 90-second film: voiceover script

American English. Calm and measured, the register of a colleague stating facts, not an advertisement. Roughly 95
words over 90 seconds, so there is room to breathe between lines — the gaps are deliberate; the picture carries
those moments on its own.

The film is finished and works silently. Record this only if you want a voice on it.

| In | Line |
|---|---|
| 0:02 | Four million, three hundred twelve thousand. Net profit for September. |
| 0:05 | Where did it come from? |
| 0:09 | Somewhere in a hundred and twenty-four journal entries. |
| 0:16 | The answer is in there. Your afternoon isn't. |
| 0:31 | So ask — the way you'd ask your controller. |
| 0:45 | Net profit. Against budget. And what moved it. |
| 0:54 | Each one with the account, the period, and the entries behind it. |
| 1:00 | Every figure carries its source. |
| 1:08 | It never moves money. |
| 1:12 | Nothing leaves without your approval. |
| 1:16 | Your books stay on your server. |
| 1:22 | OfinTech Desk. The figure you sign your name under — and where it came from. |

## Notes for the read

- **0:05** is the hook. Let it land as a real question, not a rhetorical one.
- **0:16** is the only line with any edge to it. Dry, not sarcastic.
- **1:08 – 1:16** are three flat statements of fact. Equal weight, no build, no emphasis on any single word. They
  are the reason a finance lead buys this, and they are more convincing stated plainly.
- Nothing in the script claims a certification, a compliance standard, or an accuracy figure. Keep it that way.

## Mixing a recorded voice onto the film

The bed already sits at about −23 dBFS RMS, which leaves room for a voice without re-mixing:

```
ffmpeg -i ofintech-90s.mp4 -i voice.wav \
  -filter_complex "[0:a]volume=0.55[bed];[1:a]highpass=f=90,acompressor=ratio=3:threshold=-20dB[vo];[bed][vo]amix=inputs=2:normalize=0[a]" \
  -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k ofintech-90s-vo.mp4
```

`volume=0.55` ducks the music by about 5 dB under the voice. Raise it toward 1.0 for the gaps.
