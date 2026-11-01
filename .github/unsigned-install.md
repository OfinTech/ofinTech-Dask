
---

### These installers are not code-signed

**macOS** will refuse the app outright — *“OfinTech Desk” is damaged and can’t be opened. You should move it to
the Trash.* It is not damaged: Apple silicon will not run an application that carries no signature at all. Move
it to Applications, then clear the quarantine flag macOS puts on anything downloaded:

```
xattr -dr com.apple.quarantine "/Applications/OfinTech Desk.app"
```

**Windows** SmartScreen warns instead of refusing: *More info → Run anyway*.

Only Apple silicon is built for macOS; this release has no Intel build. What removes all of this is a signed and
notarised build — see `docs/RELEASING.md`.
