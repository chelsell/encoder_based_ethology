# Transcode QC feature contract

The historical transcode QC product is a two-axis, source-domain representation:

1. visible event activity in each well;
2. technical/common-mode evidence from the well and the full plate.

It ranks intervals for review. It is not an automatic artifact verdict and must
not be used to delete or reject source data. Quiet, artifact, ambiguous, and
plausibly behavioral labels remain separate during evaluation.

The machine-readable contract is
`configs/qc/clipsift_qc_bridge_v0_1.json`. It incorporates the ClipSift QC
handoff dated 2026-08-03.

## Calculate during the canonical source decode

Retain source-cadence traces, aligned by source frame index and PTS, for:

- per-well changed-pixel fraction and robust absolute image-change energy at
  lags 1, 3, and 10 frames;
- per-well absolute and signed frame-mean intensity change;
- per-well MV magnitude sum, p95 magnitude, active fraction, coherence, and
  resultant magnitude;
- compact per-well 4x4 spatial MV summaries: occupied-bin fraction, normalized
  entropy, maximum-bin fraction, centroid, and wall fraction;
- static-reference image change and signed/absolute intensity change;
- whole-plate phase-correlation x/y shift, magnitude, and response;
- concurrent active-well fraction, plate directional resultant, robust median
  activity, and robust activity dispersion.

The static-reference mask and well geometry are versioned inputs. Registration
response is retained so an uncertain shift is not mistaken for measured motion.
Full vector rows are not a production default; use them for curated audit
subsets. Coarse-grid summaries prevent spatial occupancy information from being
lost when vector rows are omitted.

## Derive after the transcode

The durable fine traces are the primary measurement. Derive 50, 100, and 250 ms
bins and exact stimulus/clip-window summaries later. These include medians,
p95, maxima, AUC, active-frame fraction, bout persistence, duplicate/corrupt-like
fractions, centroid displacement, and cross-channel ratios. Every ratio uses a
versioned robust denominator floor.

This division keeps the storage-clearing path bounded while preserving the
measurements that cannot be reconstructed after historical source removal.

## Promotion gate

The current C sidecar supplies lag-1 image change and basic MV magnitude,
resultant, and coherence. It does not yet satisfy this contract: multi-lag and
signed intensity traces, summary-only active/spatial MV fields, and the
plate/reference companion remain implementation requirements. `RUN_SIDECAR=0`
encoder-only benchmarks therefore measure encoding throughput, not completion
of the archival QC product.
