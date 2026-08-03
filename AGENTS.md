# Encoder-Based Ethology agent notes

## Purpose and interpretation

This repository extracts and validates reproducible motion sidecars and
well-level AV1 archives for fixed-camera, multi-larva zebrafish video. The
representation is aggregate image-supported motion; do not describe it as
individual tracking, pose, fish count, or literal animal velocity.

`cd(10)` is a continuity/reference measure, not the optimization target. Treat
disagreement among `cd(10)`, motion vectors, and frame differences as something
to inspect, not automatically as failure.

## Current architecture

- The implemented plate worker produces 96 independent well AV1 files through
  one FFmpeg filter graph by default. With multiple encoder processes, the
  source is decoded once per process group.
- Its optional post-encode `mestimate` step reads the completed well AV1 files.
  This is archive-domain compatibility/debug output, not source-domain QC.
- One canonical source decode feeding per-well source-domain multi-lag features
  plus plate/reference-region QC is the target contract, not current behavior.
- Keep source HEVC recoverable from a checksum-verified backup while the archive
  and source-domain QC path are under validation. Nothing here authorizes source
  deletion.
- FFmpeg `mestimate` is the canonical v1 extractor. Preserve its coordinate,
  reference-direction, frame/timebase, and schema conventions. Any incompatible
  semantic change requires a new version rather than rewriting v1.
- Routine archival jobs normally retain complete frame summaries and omit raw
  vector rows; use sampled/all vectors for bounded audit sets when justified.

## Working rules

- Start with `README.md` for supported commands. For production cluster work,
  also read `docs/sge_archival_orchestration.md`,
  `docs/cluster_reproducibility.md`, and the latest dated Wynton status note.
- Treat `docs/sidecar_schema.md` and `docs/mv_features_v1_schema.md` as current
  schemas. `docs/transcode_qc_feature_contract.md` is an unimplemented target
  contract; do not report it as an emitted product.
- Preserve raw/source-derived measurements separately from derived feature
  sets. Version derived features instead of changing earlier meanings in place.
- Every output must identify the input hash, ROI/geometry, source frame/timebase,
  extractor and feature versions, parameters, code revision, and
  environment/container. Prefer stable IDs and hashes over paths as identity.
- Keep jobs restartable and idempotent. Do not treat partial output as complete;
  write atomically where practical and verify expected files, frame counts, and
  hashes before marking archival success.
- Make pipeline or feature changes on a small representative/audit set first.
  Report quantitative comparisons and inspectable overlays before recommending
  scale-up.
- Do not commit videos, sidecars, build trees, local databases, or generated
  reports unless they are deliberately small fixtures or curated documentation.
- Preserve user outputs by default. Commands that replace existing products
  must require an explicit `--force`/`--overwrite` path.

## Build and verification

```bash
./scripts/build.sh
tests/test_synthetic_translation.sh
python -m pytest -q tests
```

Scope pytest to `tests/`: a local `mestimate_sidecar.sandbox/` contains a full
container filesystem that pytest would otherwise try to collect recursively.

Use the repository Apptainer definition for production compatibility when an
image is configured; some wrappers otherwise use host tools. Dry-run a
single manifest task and complete a bounded pilot before submitting an archival
array. Software tests validate plumbing; biological claims still require matched
source/AV1 comparisons and visual audit.
