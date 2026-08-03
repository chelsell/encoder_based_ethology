# Encoder-Based Motion Sidecars

This repository contains a C17 extractor for FFmpeg `mestimate` motion-vector
side data plus Python utilities for sidecar inspection, feature experiments,
annotation import, and 96-well AV1 archival jobs.

The extractor is a standalone C17 command-line tool. It links against installed
FFmpeg development libraries and writes inspectable sidecars; it does not patch
FFmpeg, re-encode the source video, or parse diagnostic overlays.

## Implemented status

- `mestimate-sidecar` extracts frame summaries and optional vector rows from one
  input video. It includes lag-1 grayscale image change and basic MV magnitude,
  resultant, and coherence summaries.
- `scripts/run_archival_plate_task.py` creates 96 cropped AV1 files through one
  FFmpeg filter graph by default. Increasing `--encoder-processes` partitions
  wells across processes and decodes the source once per process, not once for
  the whole plate.
- The plate worker validates encoded outputs and can run `mestimate-sidecar`
  afterward on the materialized AV1 wells. That optional sidecar is
  archive-domain, not a source-domain branch from the source decode.
- The source-domain multi-lag image dynamics, static-reference channel, and
  plate common-mode companion specified in
  `docs/transcode_qc_feature_contract.md` are not implemented.
- Cluster wrappers use an Apptainer image when configured; otherwise they can
  invoke host tools. Source deletion is never part of the repository workflow.

The README below documents several independent utilities, not one end-to-end
command. Dated benchmark/status notes are snapshots and may describe runs that
do not exist on the current host.

## System build dependencies

On Ubuntu:

```bash
sudo apt install \
  build-essential \
  cmake \
  pkg-config \
  zlib1g-dev \
  libavformat-dev \
  libavcodec-dev \
  libavfilter-dev \
  libavutil-dev \
  libswscale-dev
```

## Optional Python inspection dependencies

The extractor and shell validation do not require pandas or matplotlib. The
plotting/report utility does:

```bash
python -m pip install -r requirements-inspect.txt
```

Current optional packages are:

```text
pandas
numpy
matplotlib
```

## Optional Python visualization dependencies

The sidecar-derived video overlay utility uses OpenCV:

```bash
python -m pip install -r requirements-visualize.txt
```

Current optional visualization packages are:

```text
pandas
numpy
opencv-python
```

## Optional Python test dependency

Python unit tests use `pytest`:

```bash
python -m pip install -r requirements-test.txt
```

## Build

```bash
./scripts/build.sh
```

## Build The Apptainer Image

The sidecar extractor can be baked into an Apptainer image so it links against
the same containerized FFmpeg libraries used at runtime:

```bash
scripts/build_sidecar_sif.sh mestimate_sidecar.sif
```

If the build compiles and tests successfully but fails while creating the final
SIF with a `mksquashfs` error, build a sandbox instead:

```bash
scripts/build_sidecar_sandbox.sh mestimate_sidecar.sandbox
IMAGE=mestimate_sidecar.sandbox scripts/test_sidecar_sif.sh
```

Apptainer can run a sandbox directory directly, so this is enough for local
development and smoke testing. Converting the sandbox to a `.sif` still depends
on the host `mksquashfs` path working.

The definition is [mestimate_sidecar.def](mestimate_sidecar.def). It installs
Ubuntu FFmpeg development libraries, builds `mestimate-sidecar`, and keeps
`/usr/bin/ffmpeg` available for synthetic fixture generation and diagnostic
`codecview` overlays.

Run the baked extractor against files bind-mounted from this repo:

```bash
scripts/run_sidecar_sif.sh \
  --input /work/data/A07.mp4 \
  --output-dir /work/outputs/A07_container \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --force
```

Run container smoke checks:

```bash
scripts/test_sidecar_sif.sh
```

This first runs the image `%test`, then runs the synthetic translation test
inside the image with `FFMPEG_BIN=/usr/bin/ffmpeg`.

## Run sidecar extraction as a Slurm array

For cluster execution, use a SIF or sandbox that contains both FFmpeg and the
`mestimate-sidecar` binary. A generic FFmpeg-only SIF is not enough unless the
sidecar binary is also present and linked against compatible FFmpeg libraries.
The repository image definition, [mestimate_sidecar.def](mestimate_sidecar.def),
builds the sidecar inside the image and is the intended production model.

Create a manifest from a directory of source videos:

```bash
python scripts/make_sidecar_slurm_manifest.py \
  --input-root /media/ssd1/sauronx_videos/video_selection_2025-09-18 \
  --pattern '*.mkv' \
  --output-root /scratch/$USER/mestimate_sidecars \
  --manifest manifests/mestimate_sidecar_jobs.csv \
  --vector-output none \
  --vector-format bin \
  --force
```

The manifest has one row per Slurm array task. Submit it with the task count
printed by the manifest command:

```bash
mkdir -p slurm_logs
MANIFEST=manifests/mestimate_sidecar_jobs.csv \
IMAGE=/path/to/mestimate_sidecar.sif \
sbatch --array=1-N scripts/sidecar_array.sbatch
```

Each task reads its row using `SLURM_ARRAY_TASK_ID`, creates the output
directory on the host, and runs:

```text
apptainer run --cleanenv --bind <input_parent>:<input_parent> --bind <output_dir>:<output_dir> <image> ...
```

If your cluster does not auto-bind a needed filesystem, pass extra bind paths:

```bash
SIDECAR_EXTRA_BIND_ARGS="--bind /media/ssd1 --bind /scratch" \
MANIFEST=manifests/mestimate_sidecar_jobs.csv \
IMAGE=/path/to/mestimate_sidecar.sif \
sbatch --array=1-N scripts/sidecar_array.sbatch
```

To debug a single task without Slurm:

```bash
python scripts/run_sidecar_manifest_task.py \
  --manifest manifests/mestimate_sidecar_jobs.csv \
  --task-index 1 \
  --image /path/to/mestimate_sidecar.sif \
  --dry-run
```

Each output directory receives `sidecar_slurm_task.json` with the manifest row,
Apptainer command, bind paths, Slurm IDs, elapsed time, and return code.

For archival runs that first split plate videos into well videos, see
[docs/well_first_archival_sidecar.md](docs/well_first_archival_sidecar.md).
The cluster scheduling unit should be the source plate video, not one full
source decode per well. Use `scripts/make_well_archival_manifest.py` to create a
plate-job manifest plus a per-well output/provenance manifest from the exported
independent ROI-record table. The ROI-solution table is per source video and is
recorded as provenance with `--roi-solution-table`. The sidecar extractor should
normally run on the well crop with
`--vector-output none`; keep plate-level common-mode/reference-region QC as a
separate companion product. See
[docs/cluster_reproducibility.md](docs/cluster_reproducibility.md) and
[docs/sge_archival_orchestration.md](docs/sge_archival_orchestration.md) before
production-scale cluster runs. Wynton-specific container rebuild and disk budget
notes are in
[docs/wynton_container_and_disk_budget.md](docs/wynton_container_and_disk_budget.md).
The cluster does not need the full local working tree; use
`scripts/make_wynton_deploy_bundle.sh` and the audit in
[docs/repo_cleaning_audit.md](docs/repo_cleaning_audit.md) to stage a minimal
source bundle.

The current archival candidate intentionally writes 96 independent well AV1
videos through one FFmpeg graph in its default one-process mode. Multiple
encoder processes repeat the source decode per group. A controlled 10-second comparison measured the
combined well outputs at 1.74 times the bytes of a whole-plate AV1, despite the
crops covering 71.7% of the source pixels. That storage premium is currently
accepted for further testing because independent well videos simplify the
downstream architecture and the source HEVC is expected to remain recoverable
from a checksum-verified cloud backup. This is a benchmarked working decision,
not yet authorization to remove source files.

Current Wynton behavior and measured pilot details are recorded in
[docs/wynton_benchmark_status_20260802.md](docs/wynton_benchmark_status_20260802.md).

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

If image building fails before reading the def file with an error about
`newuidmap` or `newgidmap`, the host Apptainer fakeroot installation needs
administrator attention. On a correctly configured host these helpers are owned
by `root:root` and have the setuid bit. This workspace currently observed:

```text
/usr/bin/newuidmap owned by nobody:nogroup
/usr/bin/newgidmap owned by nobody:nogroup
```

That prevents an unprivileged local build. The usual options are:

```bash
# Check the local Apptainer build prerequisites.
scripts/check_sidecar_sif_prereqs.sh

# Build on a host with working Apptainer fakeroot.
scripts/build_sidecar_sif.sh mestimate_sidecar.sif

# Or pass site-specific build flags, for example a remote builder if configured.
APPTAINER_BUILD_FLAGS="--remote" scripts/build_sidecar_sif.sh mestimate_sidecar.sif

# Or have an administrator repair the host Apptainer installation.
```

## Run an example

```bash
./build/mestimate-sidecar \
  --input data/A07.mp4 \
  --output-dir outputs/A07_mb16_epzs_sp12 \
  --method epzs \
  --mb-size 16 \
  --search-param 12
```

This writes:

```text
<stem>.mestimate-v1.vectors.csv.gz
<stem>.mestimate-v1.frames.csv.gz
<stem>.mestimate-v1.metadata.json
```

Existing sidecars are not replaced unless `--force` or `--overwrite` is passed.

## Sample vector rows at extraction time

Frame summaries are always computed from all vectors emitted by `mestimate`.
They also include a lag-1 cd-style grayscale frame-difference channel:
`frame_diff_changed_pixels` counts pixels with
`abs(current_gray - previous_gray) > frame_diff_threshold`, default threshold
10. Vector rows are optional. For a storage-saving first-pass extraction, keep
the complete frame summaries and skip vector rows:

```bash
./build/mestimate-sidecar \
  --input data/A07.mp4 \
  --output-dir outputs/A07_summary_only \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --frame-diff-threshold 10 \
  --frame-output csv \
  --summary-float-precision 6 \
  --vector-output none
```

For audit/debug runs, vector rows can be written as a deterministic sampled
subset. Use binary format when footprint matters:

```bash
./build/mestimate-sidecar \
  --input data/A07.mp4 \
  --output-dir outputs/A07_sampled_vectors \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --vector-output sampled \
  --vector-format bin \
  --vector-source past \
  --vector-frame-stride 5 \
  --vector-spatial-stride 2 \
  --vector-min-magnitude 0.25
```

The current dials are deliberately simple: temporal stride, regular spatial
lattice stride, source direction, and optional magnitude floor. They do not cap
the number of vectors in an active frame, because that would impose an artificial
ceiling on high-activity wells. Use `--vector-output all` to retain the complete
vector table for a smaller audit set, preferably with `--vector-format bin`.
Output mode, storage format, sampling settings, and raw versus retained vector
counts are recorded in
`<stem>.mestimate-v1.metadata.json`.

`--frame-output csv` with `--summary-float-precision 6` is currently the best
tested storage/speed compromise. `--frame-output bin` is available for
experiments, but on the current small benchmark it did not beat compact CSV
under gzip. For high-volume vector rows, `--vector-format bin` writes a
fixed-width `.vectors.bin.gz` stream with exact integer vector fields and
float32 convenience time/magnitude fields. CSV vectors remain useful for
inspection and small fixtures. The precision setting applies to CSV frame
summary output only; binary frame summaries store float32 summary values plus
exact integer counts.

FFmpeg also has filters such as `scdet`, `signalstats`, `entropy=mode=diff`,
`tblend`, `freezedetect`, and `blackframe` that can help select frames or windows
for denser vector retention. They provide frame-level image-dynamics metadata,
not per-vector match confidence, so they are best treated as later gating/QC
signals rather than a replacement for vector-level residuals.

To measure sidecar cost relative to AV1 transcode cost on a sample:

```bash
RUNS=3 scripts/benchmark.sh data/A07.mp4 outputs/benchmark_A07
```

## Validate

The synthetic validation script also needs an FFmpeg CLI on `PATH` to create the
small fixture video. Set `FFMPEG_BIN=/path/to/ffmpeg` to pin a specific binary.

```bash
tests/test_synthetic_translation.sh
python -m pytest -q tests
```

Scope pytest to `tests/`. A locally built `mestimate_sidecar.sandbox/` is a full
container filesystem, and a bare recursive pytest invocation can collect
incompatible system-Python files from it.

The synthetic test creates a small 83x83 video with known static, horizontal,
and vertical movement episodes, then checks schemas, row-count consistency, and
frame-level motion contrast. This is software validation of the extractor path,
not biological validation.

## Inspect a sidecar

```bash
python scripts/inspect_sidecar.py \
  --frames outputs/A07_mb16_epzs_sp12/A07.mestimate-v1.frames.csv.gz \
  --vectors outputs/A07_mb16_epzs_sp12/A07.mestimate-v1.vectors.csv.gz \
  --metadata outputs/A07_mb16_epzs_sp12/A07.mestimate-v1.metadata.json \
  --output-dir reports/A07_mb16_epzs_sp12
```

The report includes frame traces, histograms, top-motion frames, and a compact
JSON summary.

## Integrate vectors into compact MV features

The raw vector table is useful for audit and debugging, but routine analyses
should usually consume temporally integrated features. Generate frame-level and
fixed-bin summaries from an existing sidecar:

```bash
python scripts/integrate_mestimate_sidecar.py \
  --frames outputs/A07_mb16_epzs_sp12/A07.mestimate-v1.frames.csv.gz \
  --vectors outputs/A07_mb16_epzs_sp12/A07.mestimate-v1.vectors.csv.gz \
  --metadata outputs/A07_mb16_epzs_sp12/A07.mestimate-v1.metadata.json \
  --output-dir outputs/A07_mb16_epzs_sp12/derived_mv_features \
  --vector-source past \
  --bin-ms 50 100 250
```

This writes:

```text
<stem>.mv-features-v1.frames.csv.gz
<stem>.mv-features-v1.bin-50ms.csv.gz
<stem>.mv-features-v1.bin-100ms.csv.gz
<stem>.mv-features-v1.bin-250ms.csv.gz
<stem>.mv-features-v1.metadata.json
```

The compact feature schema is documented in
[docs/mv_features_v1_schema.md](docs/mv_features_v1_schema.md). The current
implementation is deliberately post-hoc: use it to stabilize feature definitions
before moving summary-only emission into the C extractor or containerized
transcode path.

To integrate every sidecar under a directory and get a size/runtime manifest:

```bash
python scripts/integrate_sidecar_tree.py \
  --input-root outputs/example_mv_bouts/sidecars \
  --output-root outputs/example_mv_bouts/derived_mv_features \
  --vector-source past \
  --bin-ms 50 100 250
```

This preserves the input directory layout under `--output-root` and writes
`mv_feature_batch_manifest.csv`, `mv_feature_batch_missing.csv`, and
`mv_feature_batch_summary.json`. The manifest includes source bytes, derived
bytes, elapsed seconds, skipped outputs, and missing vector tables.

## Import cliptriage annotation decisions

Human well-clip labels from the sibling `well_annotation` project can be
imported from its SQLite database without starting the annotation app:

```bash
python scripts/import_cliptriage_annotations.py \
  --database /home/cole/code/well_annotation/data/annotations/cliptriage.sqlite \
  --output-dir data/annotations/cliptriage_import
```

This writes append-only history, current latest labels, and per-clip label
summaries with source video, well, frame/time window, ROI, and sampling-stratum
provenance. See
[docs/cliptriage_annotation_import.md](docs/cliptriage_annotation_import.md).

## Render a sidecar-derived overlay

This draws the exact vectors stored in the sidecar over the original video. It
is different from FFmpeg `codecview`, which reruns `mestimate` and is best kept
as a concordance reference.

```bash
python scripts/render_sidecar_overlay.py \
  --input data/A07.mp4 \
  --vectors outputs/A07_mb16_epzs_sp12/A07.mestimate-v1.vectors.csv.gz \
  --frames outputs/A07_mb16_epzs_sp12/A07.mestimate-v1.frames.csv.gz \
  --output reports/A07_mb16_epzs_sp12/A07.sidecar_overlay.mp4 \
  --scale 6 \
  --force
```

The current renderer aligns sidecar rows to source frames by `frame_index`.
That is appropriate for the current constant-frame-rate samples, but timestamp
alignment should be added before relying on it for irregular or edited videos.

The renderer can also apply post-hoc filters while preserving the raw sidecar.
For example, this draws only vectors whose local destination block has at least
two pixels changing by more than 10 gray levels between adjacent frames:

```bash
python scripts/render_sidecar_overlay.py \
  --input outputs/20260402_105002_S24_compare/crops/20260402_105002_S24_A01.mkv \
  --vectors outputs/20260402_105002_S24_compare/sidecars/A01/20260402_105002_S24_A01.mestimate-v1.vectors.csv.gz \
  --frames outputs/20260402_105002_S24_compare/sidecars/A01/20260402_105002_S24_A01.mestimate-v1.frames.csv.gz \
  --output outputs/20260402_105002_S24_compare/A01_overlay_filtered_cdblock.mp4 \
  --scale 4 \
  --min-block-cd-pixels 2 \
  --cd-threshold 10 \
  --force
```

## Compare against local cd(10)

The comparison utility can analyze all `mestimate` vectors or restrict to one
temporal reference direction. FFmpeg's `mestimate` filter exposes only
`method`, `mb_size`, and `search_param`; it does not expose a filter option that
turns future-reference vectors off at extraction time. The sidecar stores the
raw `AVMotionVector.source` value, so the no-future-reference analogue is a
post-hoc source filter:

```bash
python scripts/compare_well_motion_cd10.py \
  --input /path/to/video.mkv \
  --output-dir outputs/example_past_only \
  --wells A01 A07 D06 H12 \
  --motion-metric active_fraction \
  --active-vector-threshold 0 \
  --active-min-block-cd-pixels 16 \
  --vector-source past
```

`--vector-source past` keeps `source < 0`, `future` keeps `source > 0`, and
`all` preserves the original behavior.

For a stricter cd(10)-like MV companion, `active_bout_fraction` adds spatial and
temporal support. It starts from supported active vectors, then zeroes any frame
that has too few active blocks or belongs to too short a consecutive run:

```bash
python scripts/compare_well_motion_cd10.py \
  --input /path/to/video.mkv \
  --output-dir outputs/example_mv_bouts \
  --wells A01 A07 D06 H12 \
  --motion-metric active_bout_fraction \
  --active-vector-threshold 0 \
  --active-min-block-cd-pixels 16 \
  --min-active-blocks-per-frame 2 \
  --min-active-run-frames 2 \
  --vector-source past
```

The output keeps both the original per-frame `mv_active_fraction` and the stricter
`mv_bout_fraction` columns so the filter effect remains inspectable.

For manual inspection, avoid interpreting simple high/low quantile disagreement
as strong discordance. The disagreement inspector can instead assign ratio-aware
categories using each well's cd(10) and MV thresholds:

```bash
python scripts/inspect_cd10_smvo_disagreements.py \
  --input-root outputs/example_mv_bouts \
  --output-dir outputs/example_mv_bouts/ratio_categories \
  --ratio-categories

python scripts/make_disagreement_clip_gallery.py \
  --input-root outputs/example_mv_bouts \
  --regime-table outputs/example_mv_bouts/ratio_categories/second_bin_regimes.csv \
  --output-dir outputs/example_mv_bouts/ratio_category_gallery \
  --force
```

This separates strong discordance (`cd10_only_strong`, `mv_only_strong`) from
threshold-boundary cases (`cd10_boundary_mv_high`, `mv_boundary_cd10_high`) and
consensus detections (`high_both_absolute`, `high_both_boundary`).

To screen alternative MV-derived traces without rerunning extraction, use:

```bash
python scripts/iterate_mv_feature_candidates.py \
  --input-root outputs/example_mv_bouts \
  --output-dir outputs/example_mv_feature_candidates
```

This compares saturation-gated, capped, log-scaled, and coherence-gated variants
against one-second cd(10). Treat the output as feature triage; it does not
replace manually curated positive/negative well labels.

For a stimulus-timing weak target when explicit stimulus metadata is unavailable,
screen features for cross-well synchronous peaks that recur at similar relative
seconds across videos:

```bash
python scripts/screen_mv_features_for_stimulus_timing.py \
  --input-root outputs/example_mv_bouts \
  --output-dir outputs/example_stimulus_feature_screen
```

This is not a stimulus decoder. It is an exploratory check for stimulus-like
timing structure: synchronized across selected wells and recurrent across
videos.

Other filter knobs include `--min-magnitude`, `--min-frame-energy`,
`--min-coherence`, and `--max-coherence`.

## Scan Post-Hoc Filters

Use the scan utility to compare candidate post-hoc filters against a local
`cd(10)`-style frame-difference baseline. In these scripts, primary `cd10` is
the count of pixels whose absolute frame-to-frame intensity delta is greater
than 10. The summed supra-threshold intensity delta is retained separately as
`cd10_sum_absdiff`.

For a motion-vector feature that is conceptually closer to cd(10), compare
against MV-active fraction rather than summed vector magnitude:

```bash
python scripts/compare_well_motion_cd10.py \
  --input /media/ssd1/sauronx_videos/video_selection_2025-09-18/20260402_105002_S24.mkv \
  --output-dir outputs/20260402_105002_S24_compare \
  --wells A01 A07 D06 H12 \
  --start-seconds 60 \
  --duration-seconds 120 \
  --layout auto \
  --motion-metric active_fraction \
  --active-vector-threshold 0 \
  --skip-existing
```

```bash
python scripts/scan_motion_filter_thresholds.py \
  --video outputs/20260402_105002_S24_compare/crops/20260402_105002_S24_A01.mkv \
  --vectors outputs/20260402_105002_S24_compare/sidecars/A01/20260402_105002_S24_A01.mestimate-v1.vectors.csv.gz \
  --frames outputs/20260402_105002_S24_compare/sidecars/A01/20260402_105002_S24_A01.mestimate-v1.frames.csv.gz \
  --output-dir outputs/20260402_105002_S24_compare/filter_scan_A01 \
  --cd-threshold 10
```

This writes `filter_scan.tsv`, a heatmap, and a vector table augmented with
local block frame-difference features. Treat these as threshold exploration
artifacts, not validated behavioral labels.

## Local data directories

If needed, create the ignored working directories with:

```bash
mkdir -p data/{catalog,manifests,rendered_clips,annotations,reports,metrics,cache,interim}
```

This repository does not track raw video, rendered clips, local annotation databases,
derived metric tables, or large generated outputs.

Tracked inputs should generally be limited to:

- source code
- tests and small synthetic fixtures
- configuration files
- taxonomy definitions
- documentation
- small hand-curated example assets, when explicitly intended

Generated or source data should be reproducible from documented commands and stored
outside Git or in an appropriate archival/object-storage location.
