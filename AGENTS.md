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

- The historical candidate is one canonical source decode producing 96
  independent well AV1 files, per-well source-domain `mestimate` sidecars, and
  separate plate/reference-region QC. Whole-plate AV1 remains a comparison arm.
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
- Treat `docs/sidecar_schema.md`, `docs/mv_features_v1_schema.md`, and
  `docs/transcode_qc_feature_contract.md` as the schema/contract authorities.
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
python -m pytest -q
```

Use the repository Apptainer definition for production compatibility. Dry-run a
single manifest task and complete a bounded pilot before submitting an archival
array. Software tests validate plumbing; biological claims still require matched
source/AV1 comparisons and visual audit.
