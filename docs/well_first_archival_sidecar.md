# Well-first archival sidecars

The preferred archival order is:

```text
source plate video
  -> one canonical plate decode
      -> deterministic well crops from versioned ROI table
      -> per-well AV1 archive
      -> per-well source-domain image-dynamics summary
      -> optional per-well MV sidecar summary
  -> lightweight plate-level QC/common-mode product
```

The per-well sidecar is in the coordinate domain of the well crop. It should be
treated as a well-domain product, not a plate-domain vector field. The split
manifest is therefore part of the scientific provenance.

The required transcode-time QC traces and the boundary between source-decode
measurements and downstream window summaries are specified in
[`transcode_qc_feature_contract.md`](transcode_qc_feature_contract.md) and
`configs/qc/clipsift_qc_bridge_v0_1.json`.

The source plate video should not be decoded independently once per well. A
historical archival job should be scheduled at the plate-video level, decode the
HEVC stream once, and fan out crop, AV1 encode, image-dynamics, and optional MV
measurements from that decoded frame stream. Per-well manifests are still useful
as expected-output inventories, but the scheduler unit should be the plate job.

## Build archival manifests

Use the independent ROI-record table exported from `ROI_improvement` and the
source catalog to create both a plate-level job manifest and a well-level output
manifest. The ROI-solution table is per source video; record it with
`--roi-solution-table`, but use the ROI-record table for per-well crop boxes.

```bash
python scripts/make_well_archival_manifest.py \
  --source-catalog /home/cole/code/ROI_improvement/data/catalog/valar_96_well_source_catalog.csv \
  --roi-table /home/cole/code/ROI_improvement/data/roi_records/valar_96_well_affine_analysis100_roi_records.csv \
  --roi-solution-table /home/cole/code/ROI_improvement/data/roi_solutions/valar_96_well_analysis100/valar_96_well_analysis100_roi_solutions.csv \
  --roi-repo /home/cole/code/ROI_improvement \
  --fail-dirty-roi-repo \
  --skip-missing-source-path \
  --image /path/to/archival_pipeline.sif \
  --output-root /wynton/scratch/$USER/encoder_based_ethology/valar_96_well_analysis100/runs \
  --well-manifest /media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100/well_archival_outputs.csv
```

This writes `well_archival_outputs.csv`, `plate_archival_jobs.csv`, and a JSON
summary in `/media/ssd1/tmp/encoder_based_ethology_manifests/valar_96_well_analysis100`.
The plate manifest has one row per source video and should drive cluster array
jobs. The well manifest has one row per well and records expected AV1, sidecar,
ROI, source, and container provenance.

## Current per-well sidecar command

The current worker can run `mestimate-sidecar` after well AV1 files have been
materialized. This is an archive-domain compatibility/debug path, not the
required historical source-domain extraction. When that path is explicitly
enabled, use summary-only sidecars:

```bash
mestimate-sidecar \
  --input <well_crop_video> \
  --output-dir <well_sidecar_dir> \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --frame-diff-threshold 10 \
  --frame-output csv \
  --summary-float-precision 6 \
  --vector-output none
```

This archive-domain command preserves:

- all-frame MV summary statistics;
- lag-1 cd-style grayscale image dynamics;
- exact extractor/filter/software provenance;
- no high-volume vector row file.

For the production historical pipeline, source-domain lagged image dynamics are
the minimum required per-well motion product and must branch from the canonical
source decode. Compact source-domain MV/spatial summaries are required by the QC
feature contract once their throughput is benchmarked. Running the current SGE
worker with `RUN_SIDECAR=0` benchmarks only video encoding and validation.

Use `--vector-output sampled` or `--vector-output all` only for audit subsets,
debugging, and method development. When retaining vector rows beyond tiny
fixtures, prefer `--vector-format bin`; CSV is mainly for inspection.

## Required split provenance

The crop/split manifest should include at least:

```yaml
parent_video:
  path: ...
  sha256: ...
  source_fingerprint: ...
  frame_count: ...
  fps: ...
  timebase: ...
  decode_count: 1
well_video:
  well_id: A01
  path: ...
  sha256: ...
  crop_x: ...
  crop_y: ...
  crop_width: ...
  crop_height: ...
  crop_padding: ...
  frame_count: ...
  fps: ...
  timebase: ...
  source_frame_start: ...
  source_frame_end: ...
geometry:
  version: ...
  plate_type: ...
  coordinate_domain: well_crop
  parent_coordinate_origin: [crop_x, crop_y]
roi_provenance:
  roi_table_path: ...
  roi_table_sha256: ...
  roi_repo_commit: ...
  roi_repo_dirty: 0
container:
  image_path: ...
  image_sha256: ...
  ffmpeg_version: ...
  pipeline_commit: ...
```

The well AV1 archive and the well sidecar should be derived from the same crop
definition and frame sequence. Frame counts and timebase should be verified
across all wells from the same parent plate video.

## Plate-level companion

Well-first extraction removes direct access to plate-wide motion from the
well-domain sidecar. Keep a separate lightweight plate-level QC product for:

- reference-region or inter-well image dynamics;
- common-mode camera/plate translation candidates;
- global flicker or illumination shifts;
- dropped-frame or timing anomalies;
- split/geometry verification.

This companion is a QC covariate, not a replacement for per-well behavior
features.

For QC, the production companion must preserve static-reference image dynamics,
signed intensity change, whole-plate registration shift plus response, the
fraction of wells active concurrently, and robust plate activity location and
dispersion. These are higher-priority artifact covariates than additional raw
MV peak-magnitude transforms.
