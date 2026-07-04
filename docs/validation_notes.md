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

## Candidate known-effect phenotype: bromocriptine edge hugging

Matt reported that bromocriptine reliably causes fish to hug the edges of the
well. Treat this as a high-value candidate positive-control phenotype for
feature evaluation, not as a result established by this repository.

This effect is especially relevant for spatial MV and image-dynamics features:

- center / mid-zone / wall-zone motion fractions;
- center-to-wall activity ratio;
- motion centroid distance from well center;
- radial occupancy or radial activity summaries;
- wall-associated persistence after stimulus windows;
- disagreement between total motion and spatial redistribution.

A useful validation slice would compare bromocriptine wells against matched
controls within the same plate/run, stratified by stimulus window where
available. The expected qualitative pattern is increased wall-associated
activity or occupancy-like motion organization, not necessarily increased total
motion.
