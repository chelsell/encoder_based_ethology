# Encoder-Based Motion Sidecars

This repository contains a minimal prototype extractor for FFmpeg `mestimate`
motion-vector side data.

The extractor is a standalone C17 command-line tool. It links against installed
FFmpeg development libraries and writes inspectable sidecars; it does not patch
FFmpeg, re-encode the source video, or parse diagnostic overlays.

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

## Validate

The synthetic validation script also needs an FFmpeg CLI on `PATH` to create the
small fixture video. Set `FFMPEG_BIN=/path/to/ffmpeg` to pin a specific binary.

```bash
tests/test_synthetic_translation.sh
```

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

mkdir -p data/{catalog,manifests,rendered_clips,annotations,reports,metrics,cache,interim}

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
