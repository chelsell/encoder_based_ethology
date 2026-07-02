# Validation notes

Synthetic translation validation is performed by:

```bash
tests/test_synthetic_translation.sh
```

This generates an 83x83 fixture with static, horizontal, and vertical movement
episodes, runs `mestimate-sidecar`, and checks the schemas plus frame-level
motion contrast. The empirically observed sign convention should be updated
here after test runs against the local FFmpeg build.

On the local FFmpeg 6.1 library build, a 50-frame synthetic FFV1 fixture
produces 49 filtered output frames from `mestimate`. The sidecar therefore
records one row per filtered output frame, not one row per encoded input frame.

For the current synthetic fixture, the test observed:

```text
rightward translation episode: mean_dx_px > 0
upward translation episode:   mean_dy_px > 0
```

These are empirical extractor checks for this FFmpeg build and fixture. They
should not yet be treated as a final biological direction convention.
