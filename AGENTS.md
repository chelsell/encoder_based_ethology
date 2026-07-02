# AGENTS.md — Encoder-Aware Video Analysis and Motion Sidecar

## Project purpose

This repository develops a reproducible, non-tracking video-analysis framework for high-throughput larval zebrafish recordings.

The data are fixed-camera, grayscale, high-frame-rate recordings of multi-larva wells in 96-well plates. The scientific goal is to preserve and exploit motion information that is richer than a scalar motion index while remaining appropriate for crowded wells where persistent individual identity and pose are neither reliable nor necessary.

The project has two linked goals:

1. **Historical preservation and source-domain measurement**
   - Transcode historical video to validated AV1 archival files.
   - During the historical source decode, create a compact, versioned motion sidecar and associated image-dynamics measurements.
   - Preserve enough information to support future feature engineering after source files are no longer online.

2. **Prospective, reproducible video phenotyping**
   - Develop well-level spatiotemporal features that make behavioral review, QC, and phenotype discovery more tractable.
   - Treat the representation as a population-level assay of motion organization rather than failed individual tracking.
   - Build a validation framework that distinguishes useful biological signal, image-supported motion, and instrument/optical effects.

The project is not trying to replace legacy cd(10). cd(10) remains useful as a familiar, interpretable motion-energy reference. The purpose of the sidecar is to preserve additional dimensions of behavior: spatial organization, displacement-like motion, directional coherence, temporal persistence, central versus peripheral activity, and common-mode image motion.

---

## Current scientific position

### Data characteristics

Assume the following unless a dataset-specific manifest says otherwise:

- 8-bit grayscale source video.
- Usually 100 fps.
- Long stimulus batteries, commonly around 20 minutes.
- Fixed camera and stable well geometry.
- Multiple larvae per well.
- Grainy imagery, visible shot noise, occasional reflections, well-boundary texture, and possible common-mode plate/camera movement.
- Known stimulus timing from run metadata.
- Existing cd(10) traces and legacy behavioral interpretations that remain useful as references.

### Current motion-sidecar result

A first-generation FFmpeg `mestimate` motion sidecar has already shown meaningful signal:

- Derived motion-vector features can track cd(10) well across representative wells.
- Candidate local-pixel-supported MV-active-fraction traces often achieve approximately 0.8–0.94 Pearson correlation with cd(10), with meaningful disagreement cases.
- Several derived MV features can identify stimulus timing.
- Overlay review shows that many high-MV intervals correspond to visible multi-animal movement, while some vector placement remains imperfect or ambiguous at block scale.
- Temporal aggregation and local image support appear to improve the usefulness of aggregate features.

This is sufficient justification to preserve a **source-domain v1 sidecar** during the historical transcode. The sidecar is a durable first-generation measurement, not a final behavioral ontology.

### Architectural decision

Use a two-layer archival design:

```text
historical source video
    ├── AV1 archival video
    ├── source-domain motion sidecar v1
    ├── source-domain image-dynamics / frame-difference measurements
    └── manifest, checksums, stimulus metadata, geometry, provenance
```

For future acquisition, keep storage-clearing transcode reliability and rich scientific analysis as separable operational concerns:

```text
capture → verified AV1 archive + manifest + minimum QC
archive → versioned feature extraction / review / validation jobs
```

Historical transcode is the special opportunity to compute the v1 sidecar while the original source stream is being decoded. Future feature versions may derive from AV1 after source-versus-archive validation establishes feature compatibility.

---

## Scientific framing

### What the representation measures

The primary representation is a **well-level spatiotemporal aggregate-motion field**.

It may describe:

- local image-supported displacement;
- distributed versus focal movement;
- motion-vector magnitude and active area;
- coherence or cancellation of motion directions;
- radial, tangential, inward, and outward components;
- center, mid-zone, and wall-associated activity;
- event onset, peak, duration, persistence, and recovery;
- trial-to-trial response organization;
- plate-wide or reference-region common-mode motion.

It does not require persistent identity, exact animal counts, body pose, or literal animal velocity. Language in reports and code should reflect that distinction.

### Primary analytical questions

The feature system should help answer questions such as:

- Did a well visibly respond during a known stimulus window?
- Was the response sharp, prolonged, repeated, or delayed?
- Was activity spatially focal, distributed, central, or peripheral?
- Was movement directionally coherent or composed of independent local motions?
- Does a high cd(10) event correspond to displacement-like motion, local deformation, common-mode image motion, or some combination?
- Are repeated stimulus responses declining, increasing, reorganizing spatially, or becoming less reliable?
- Do candidate drug phenotypes differ in motion organization even when total cd(10) is similar?
- Can the video representation distinguish obvious quality issues from credible biological events?

---

## Guiding principles

1. **Preserve rich source-derived measurements during historical transcode.**
   Historical source decode is an opportunity that will not recur after raw storage is reclaimed.

2. **Keep raw-ish representation and derived features distinct.**
   The sidecar should retain sufficient vector-level or grid-level information for later feature development. Derived tables should remain versioned products, never the only representation.

3. **Use multiple complementary motion channels.**
   Block displacement and pixel-change activity answer different questions. Preserve both.

4. **Use known stimulus timing as structure.**
   Stimulus-aligned windows are high-value analysis intervals. Retain full temporal provenance.

5. **Evaluate aggregate behavior, not literal vector placement alone.**
   A motion vector is a local correspondence estimate over a block. Features should be assessed by timing, spatial organization, persistence, and visual support.

6. **Build an auditable chain from video to feature.**
   Every output must record source identity, geometry version, extractor version, parameters, software environment, and timebase.

7. **Favor small, inspectable feature families over an indiscriminate feature explosion.**
   Add features because they answer a concrete visual, QC, or pharmacological question.

8. **Use disagreement as data.**
   cd(10), raw MV, local-support-weighted MV, and image-difference features will sometimes disagree. Those residual cases are a high-value route to understanding what each channel measures.

---

## Core data products

Each processed run should be represented by a stable run directory or equivalent object-store prefix.

```text
runs/<run_id>/
  manifest/
    run_manifest.json
    encode_manifest.json
    stimulus_manifest.json
    geometry_manifest.json
    checksums.json
  video/
    <run_id>.av1.mkv
  sidecar/
    mv_v1/
      metadata.json
      vectors/                 # optional compact vector-level partitions
      grid_timeseries.zarr
      well_timeseries.zarr
      common_mode_timeseries.zarr
    image_dynamics_v1/
      metadata.json
      well_timeseries.zarr
      grid_timeseries.zarr
  derived/
    feature_sets/
      mv_features_v1/
      mv_features_v1_1/
    review_panels/
    audit/
  logs/
```

Exact filenames may differ, but the conceptual separation must remain.

### Required manifest fields

Every run manifest should include:

```yaml
run_id: ...
instrument_id: ...
acquisition:
  source_path: ...
  source_sha256: ...
  width: ...
  height: ...
  pixel_format: ...
  nominal_fps: ...
  observed_frame_count: ...
  timebase: ...
video_archive:
  archive_path: ...
  archive_sha256: ...
  codec: av1
  encoder: ...
  encoder_parameters: ...
  ffmpeg_version: ...
  container_digest: ...
  verification_status: ...
stimuli:
  schedule_path: ...
  timestamp_reference: ...
geometry:
  plate_type: ...
  well_geometry_version: ...
  well_masks_path: ...
sidecar:
  sidecar_version: mv_v1
  extractor_commit: ...
  extractor_environment: ...
  motion_estimation_method: ...
  motion_estimation_parameters: ...
  local_support_definition: ...
  temporal_aggregation_definition: ...
```

Use stable run identifiers and explicit file hashes. Avoid relying on filesystem location as identity.

---

## Motion sidecar v1

### Purpose

The v1 sidecar is a source-domain record of coarse local motion estimates and closely related image-dynamics measurements. It should support current feature extraction and later re-aggregation without requiring source video access.

### Canonical motion estimator

Use FFmpeg `mestimate` as the canonical v1 extractor unless a benchmarked change is explicitly approved.

Record all relevant parameters, including:

- motion-estimation method;
- block size;
- search parameter;
- direction / reference-frame convention;
- scaling or prefiltering;
- frame indexing and timestamps;
- image crop and geometry version;
- FFmpeg build and container digest.

The v1 sidecar should preserve direction convention unambiguously. A future reader must be able to determine whether `(dx, dy)` is defined from current to reference frame or the reverse.

### Recommended vector-level fields

Where vector-level storage is practical, retain compact binary/columnar records with at least:

```text
run_id
frame_index
pts / timestamp
reference_direction
block_x
block_y
block_width
block_height
src_x
src_y
dst_x
dst_y
dx_px
dy_px
magnitude_px
well_id
spatial_bin_id
```

Add the following source-derived support fields when available:

```text
local_frame_difference
local_pixel_support
local_texture_measure
reference_region_flag
```

Do not use CSV or JSON rows for high-volume vector storage. Prefer compact partitioned Parquet, Arrow/IPC, Zarr, or another binary format with explicit schema and compression.

### Required preaggregated outputs

The sidecar must also include durable per-well and grid-level time series. These are the routine analysis interface.

At each frame or minimal temporal bin, calculate and store:

- active-block fraction;
- raw total vector magnitude;
- support-weighted total vector magnitude;
- sum of `dx` and sum of `dy`;
- vector resultant magnitude;
- directional coherence;
- median, p90, and p95 vector magnitude;
- local-pixel-supported MV-active fraction;
- active spatial-bin fraction;
- motion centroid;
- motion-centroid distance from well center;
- spatial entropy;
- maximum-bin fraction;
- center / mid-zone / wall-zone motion fractions;
- radial inward and outward components;
- tangential component;
- local texture-weighted and low-texture-associated motion fractions when available;
- static-reference and plate-wide common-mode motion measures.

Store both a fine temporal representation and a binned representation:

```text
fine time series: source frame rate or minimally reduced cadence
coarse spatial representation: 50–100 ms bins by default
stimulus-window representation: preserve finer time resolution around event windows
```

### Spatial representations

Support both of the following where convenient:

1. **Cartesian grid**
   - Suggested initial layout: 4×4 or 6×6 bins within the usable well mask.
   - Useful for persistent local artifact detection, left/right asymmetry, and spatial localization.

2. **Polar grid**
   - Suggested initial layout: 3 radial annuli × 8 angular sectors.
   - Useful for center-versus-wall activity and radial/tangential motion.

The spatial partition is a feature-engineering choice, not a fixed truth. Version it explicitly.

---

## Image-dynamics companion stream

### Rationale

Motion vectors capture translation-like correspondence. Frame-difference activity captures local deformation, blur, tail/body movement, intensity changes, and other image dynamics that may not form a clean block translation.

The companion stream should remain separate from MV features even when it is used as local support.

### Required measurements

Compute robust frame-difference activity at several temporal lags, initially:

```text
lag 1 frame   ≈ 10 ms at 100 fps
lag 3 frames  ≈ 30 ms
lag 10 frames ≈ 100 ms
```

Within each well and spatial bin, retain:

- changed-pixel fraction;
- clipped or robust total intensity-change energy;
- center / wall fractions;
- spatial entropy;
- maximum-bin fraction;
- activity centroid;
- temporal persistence.

Use robust clipping, percentile scaling, or other explicitly documented transformations appropriate to grainy imagery. Preserve enough metadata to reproduce the thresholding and scaling.

### Local pixel support for MV features

The candidate local-pixel-supported MV-active fraction is currently promising.

Treat local pixel support as a **weighting or stratification variable**, not as a claim that a vector represents a fish trajectory.

For each vector block or local cell, estimate support from image change in the source frame pair or relevant temporal neighborhood. Keep the raw ingredients and the resulting supported aggregate.

Example conceptual form:

\[
M_{\mathrm{supported}}(t)
=
\sum_i
w_{\mathrm{local}}(i,t)
\cdot
\lVert \mathbf{v}_{i,t} \rVert
\]

where `w_local` is a smooth, documented function of local image activity or support.

The derived feature set should include, separately:

- raw MV-active fraction;
- local-pixel-supported MV-active fraction;
- raw magnitude;
- support-weighted magnitude;
- image-difference activity without MV weighting.

This lets later work identify whether a phenotype is dominated by image change, displacement-like motion, or their relationship.

---

## Temporal aggregation and persistence

Temporal treatment is likely central to producing stable, interpretable well-level features.

### Required representations

Preserve raw/fine measurements, then derive robust temporal summaries at multiple scales:

```text
10–20 ms: onset-sensitive trace
40–100 ms: short response / spatial map bin
100–250 ms: robust event morphology and coherence summaries
stimulus-aligned windows: flexible analysis scale based on stimulus class
```

Recommended summary operations:

- sums for total activity;
- medians or trimmed means for noisy local features;
- max / p90 for sharp transients;
- fraction active across frames for persistence;
- time-above-threshold after a documented normalization;
- short-window autocorrelation or continuity measures.

### Interpretive use

Temporal aggregation should improve stability of aggregate behavioral features. Visual overlays should retain frame-level information separately, because temporally pooled vector displays can appear spatially displaced from the animal’s current position.

---

## Common-mode motion and reference regions

### Goal

Characterize movement that is shared across static plate regions or wells, including global translation, vibration, illumination-linked texture change, and other instrument-level effects.

### Required implementation

Define a versioned static-reference mask where possible:

- inter-well plate material;
- well rims or stable structural regions;
- fixed image features outside valid larval regions;
- other deliberately designated non-larval regions.

For each frame or bin, calculate:

- reference-region frame difference;
- reference-region motion-vector magnitude;
- estimated global translation / registration shift;
- directionality of common-mode motion;
- fraction of wells showing aligned motion;
- plate-wide median and dispersion of motion features.

Store these traces as QC covariates. They should be available in every review panel and every downstream feature table.

### Global-motion correction experiments

Support analysis variants with:

```text
raw well motion
global-motion-adjusted well motion
reference-region covariate model
```

Use these variants to characterize signal, not to silently alter historical feature definitions. Any correction must be explicit in feature-set versioning.

---

## Artifact characterization and audit workflow

### Purpose

The motion sidecar should be accompanied by a compact, curated audit set that links high-level measurements to visible image causes.

The objective is to determine when a feature is supported by visible larval movement, local deformation, common-mode motion, well-rim/reflection structure, intensity effects, or mixed causes.

### Curated audit dataset

Maintain a small but deliberately stratified panel of short clips and event windows.

Include:

- quiet baseline intervals;
- clear visible larval movement;
- strong stimulus-evoked responses;
- high raw-MV / modest image-difference episodes;
- high image-difference / modest-MV episodes;
- rim- or edge-associated activity;
- plate-wide motion candidates;
- known historical flicker or optical issue examples;
- representative normal DMSO wells;
- strong and nontrivial treatment phenotypes;
- source-versus-AV1 matched examples.

For each clip or event window, attach concise labels such as:

```text
fish-supported displacement
local deformation / tail-body activity
common-mode plate or camera movement
well-rim / reflection-associated motion
intensity / flicker-like event
mixed
indeterminate
```

Labels should be framed as visual causes of apparent motion, not final biological judgments.

### Audit outputs

Generate a standardized panel for each selected event:

1. source frame sequence or short video;
2. raw MV overlay;
3. local-support-weighted MV overlay;
4. optional temporally pooled grid representation;
5. raw and supported MV traces;
6. multi-lag frame-difference traces;
7. vector coherence;
8. spatial entropy / center-wall fraction;
9. reference-region and global-motion traces;
10. stimulus timing;
11. annotation label and analyst notes.

### Independent comparator methods

Use independent methods on the audit corpus to test whether suspicious events are method-specific or broadly supported by image correspondence.

Preferred comparison methods:

- OpenCV DIS optical flow;
- PIV-style cross-correlation using OpenPIV or PIVlab;
- simple phase-correlation or masked registration for global translation;
- selected OpenCV/scikit-image optical-flow variants where they resolve a disagreement.

Compare **well-level aggregate behavior**, not literal vector-by-vector equality:

- event timing;
- response magnitude ranking;
- spatial centroid;
- focal versus distributed motion;
- coherence;
- center/wall organization;
- persistence;
- behavior after global-motion adjustment.

The audit corpus is a validation instrument for the representation. It should remain small, curated, and easy to inspect.

---

## Validation framework

### Principle

cd(10) is a reference and continuity measure, not the optimization target.

A feature can be useful when it differs from cd(10) in a visually interpretable and reproducible way.

### Required comparison axes

For every candidate feature family, evaluate:

1. **Temporal validity**
   - Does it align with known stimulus timing where expected?
   - Does it preserve onset and response structure at the required temporal scale?

2. **Visual validity**
   - Do high and low values map to identifiable video differences in curated clips?
   - Are residual cases understandable?

3. **Technical robustness**
   - Is the result stable across nearby motion-estimation settings?
   - Does it remain interpretable across instruments, runs, plate positions, and lighting states?

4. **Common-mode sensitivity**
   - How does the feature relate to reference-region motion and estimated global shift?
   - Does it identify plate-wide events as a separate component?

5. **Source-versus-archive compatibility**
   - Does the feature preserve relevant timing, ranking, spatial organization, and treatment separation when computed from decoded AV1?

6. **Biological utility**
   - Does it improve human review, replicate consistency, phenotype separation, or future hit prioritization beyond cd(10) alone?

### Core metrics

Use a combination of:

- Pearson and Spearman correlation with cd(10), reported as continuity checks;
- stimulus-onset classification / timing error;
- within-condition and across-condition similarity;
- held-out plate/run replicate consistency;
- source-versus-AV1 concordance;
- residual-case annotation summaries;
- spatial-map similarity;
- feature distribution versus plate position, row, column, instrument, and batch.

### Source-versus-AV1 sentinel validation

For a rolling subset of matched source and AV1 videos:

1. compute the approved feature set from source;
2. compute the same feature set from decoded AV1;
3. compare temporal, spatial, and event-level outputs;
4. save a machine-readable report;
5. flag deviations beyond predeclared thresholds.

This provides continuity as codec settings, software environments, or instrument conditions evolve.

---

## Feature-set development

### Initial feature families

Build and maintain a small, interpretable first-generation feature bank.

#### Motion magnitude and support

- raw MV-active fraction;
- local-pixel-supported MV-active fraction;
- raw total vector magnitude;
- support-weighted vector magnitude;
- p90/p95 vector magnitude;
- active spatial-bin fraction;
- low-texture-associated motion fraction where available.

#### Direction and organization

- vector coherence;
- resultant direction and magnitude;
- radial inward / outward motion;
- tangential motion;
- angular dispersion;
- spatial entropy;
- maximum-bin fraction;
- motion centroid and centroid displacement.

#### Zone occupancy of activity

- center / mid-zone / wall-zone motion fractions;
- center-to-wall ratio;
- radial movement distribution;
- sector asymmetry.

#### Event morphology

For each known stimulus presentation:

- pre-stimulus baseline;
- early peak;
- early AUC;
- late AUC;
- time to peak;
- rise slope;
- response duration;
- recovery time;
- local peak count;
- response persistence;
- coherence during response;
- spatial entropy during response;
- center/wall ratio during response;
- raw-versus-supported MV relationship;
- image-difference-versus-MV relationship.

#### Repeated-stimulus behavior

- response probability;
- amplitude slope across trials;
- latency slope;
- response-area slope;
- within-well trial-to-trial similarity;
- spatial-map similarity across trials;
- change in coherence or spatial concentration across repetitions.

### Feature names and units

Every feature must have:

- a stable machine-readable name;
- clear units or normalization;
- a human-readable definition;
- time aggregation definition;
- mask and geometry version;
- extraction version.

Use names that state what is measured rather than what is presumed biologically. For example:

```text
mv_active_fraction_local_support
mv_magnitude_sum_raw
motion_spatial_entropy
motion_wall_fraction
reference_region_motion_fraction
```

rather than labels that assume “startle,” “fish count,” or “velocity” unless directly justified.

---

## Review and annotation workflow

### Purpose

The first high-value use of the feature bank is to make manual review tractable and principled.

### Review selection

Construct panels and queues from:

- feature residuals between cd(10), raw MV, supported MV, and frame-difference activity;
- high common-mode/reference-region motion;
- unusual spatial concentration;
- unusual center/wall behavior;
- high temporal instability;
- treatment and DMSO exemplars;
- repeated-stimulus response outliers;
- low source-versus-AV1 agreement;
- selected random controls.

### Annotation strategy

Start with short stimulus-aligned clips and concise visual labels. Avoid forcing detailed behavioral taxonomy before the representation has demonstrated stable discriminative dimensions.

Prioritize labels that support method validation and later phenotype work:

```text
quiet
distributed movement
focal movement
synchronized-looking response
prolonged diffuse activity
edge-associated activity
common-mode image event
visible local deformation
uncertain / mixed
```

Maintain clear separation between:

- visual description;
- QC interpretation;
- inferred pharmacological phenotype.

### Human-review outputs

Produce compact HTML or notebook reports containing:

- synchronized video snippets;
- well-level time series;
- stimulus event markers;
- grid/polar motion maps;
- frame-difference and MV channels;
- common-mode traces;
- feature values;
- annotation controls or tables.

The review interface should make it easy to compare wells within a plate, matched wells across runs, and repeated stimuli within a well.

---

## Historical transcode integration

### Required outcome

Historical transcoding should produce:

1. a verified AV1 archival video;
2. source-domain motion sidecar v1;
3. source-domain image-dynamics measurements;
4. complete manifest and checksums;
5. structured logs and a failure/retry record.

### Pipeline structure

Preferred conceptual flow:

```text
source video
    ↓
single canonical decode
    ├── AV1 encode branch
    ├── mestimate / vector extraction branch
    ├── frame-difference / local-support branch
    └── metadata / manifest writer
```

Implementation may use a streaming pipeline, temporary intermediate, or coordinated subprocesses. The required properties are reproducibility, source identity, timing alignment, and fault isolation.

### Throughput profiling

For every pipeline version, benchmark on representative videos:

- quiet DMSO;
- ordinary mixed-stimulus video;
- high-motion or unusually difficult video;
- at least one known artifact-prone or visually unusual video.

Record:

- source fps processed;
- wall time;
- CPU and RAM use;
- disk read/write throughput;
- sidecar size;
- output AV1 size;
- extraction failures or dropped frames;
- AV1 feature preservation;
- sidecar feature stability.

Historical work may use cluster-scale jobs. Prospective acquisition should promote only verified, bounded, throughput-safe steps into the instrument path.

### Restartability

Jobs must be restartable and idempotent.

Each run should have explicit states such as:

```text
discovered
validated_source
encoding
sidecar_extracting
encoded
sidecar_complete
verified
failed
quarantined
```

A partially completed run must not be treated as archival-complete. Write outputs atomically where possible, verify hashes, and maintain a resumable ledger.

---

## Prospective acquisition integration

### Default operational split

For future instrument workflows:

```text
capture
  → verified AV1 archive
  → metadata + stimulus provenance
  → minimal QC trace
  → downstream versioned analysis
```

This keeps storage reclamation dependable while allowing the scientific feature stack to evolve.

### Promotion pathway

A sidecar feature may be promoted to routine online capture only after it has:

- demonstrated useful scientific or QC value;
- passed source-versus-AV1 and cross-run validation;
- been profiled at representative acquisition load;
- produced bounded storage and compute use;
- been specified with stable versioned semantics.

---

## Software and repository standards

### Preferred stack

Use practical, inspectable tools:

- FFmpeg / Apptainer for canonical decode and encoding;
- Python for orchestration, aggregation, analysis, and reports;
- NumPy, Polars/Pandas, PyArrow, Zarr/HDF5, OpenCV, scikit-image as appropriate;
- Parquet for tabular derived features;
- Zarr or similarly chunked arrays for dense time × well × spatial-bin data;
- Matplotlib for static review figures;
- HTML reports or notebooks for interactive review.

Use GPU methods only when they provide a measured operational advantage and retain reproducible software/environment definition.

### Reproducibility requirements

Every derived artifact must include:

- code commit;
- feature-set version;
- environment/container digest;
- parameter file hash;
- source/archive identity;
- geometry/mask version;
- date of extraction;
- schema version.

### Configuration

All feature settings should be declared in versioned YAML/TOML/JSON configuration files.

Example:

```yaml
feature_set: mv_features_v1
motion_estimation:
  method: epzs
  block_size: 16
  search_param: 7
image_dynamics:
  lags_frames: [1, 3, 10]
  support_transform: robust_quantile
temporal_bins_ms: [10, 50, 100, 250]
spatial:
  cartesian_grid: [6, 6]
  polar_annuli: 3
  polar_sectors: 8
geometry_version: plate96_v1
```

### Tests

Implement tests for:

- coordinate and direction conventions;
- frame/time alignment;
- well assignment;
- spatial-bin assignment;
- aggregation invariance under row order;
- empty/quiet frames;
- known synthetic translations;
- global translation/reference-region behavior;
- manifest completeness;
- source-versus-AV1 comparison plumbing;
- restart and partial-output handling.

Synthetic test movies should include:

- a translating dark shape;
- local deformation without net translation;
- whole-frame translation;
- intensity flicker;
- stationary textured rim;
- mixed fish-like/local and global motion.

---

## Suggested repository layout

```text
.
├── AGENTS.md
├── README.md
├── pyproject.toml
├── configs/
│   ├── encode/
│   ├── sidecar/
│   ├── features/
│   └── geometry/
├── src/
│   ├── ingest/
│   ├── encode/
│   ├── sidecar/
│   ├── image_dynamics/
│   ├── geometry/
│   ├── features/
│   ├── qc/
│   ├── audit/
│   ├── reports/
│   └── utils/
├── scripts/
│   ├── benchmark_pipeline.py
│   ├── transcode_historical.py
│   ├── extract_sidecar.py
│   ├── derive_features.py
│   ├── build_review_panels.py
│   ├── run_source_archive_validation.py
│   └── audit_motion_events.py
├── tests/
├── notebooks/
│   ├── exploratory/
│   └── validated/
├── reports/
│   ├── benchmarks/
│   ├── source_archive_validation/
│   ├── audit/
│   └── review_panels/
├── schemas/
└── docs/
```

Exploration belongs in notebooks or explicitly named experimental modules. Production extraction paths should have tested, configuration-driven entry points.

---

## Immediate work plan

### Phase 1 — Formalize and preserve v1

1. Freeze the current sidecar schema and parameter set for historical v1.
2. Ensure manifests capture exact extraction provenance.
3. Store raw/vector-level information where storage permits, plus mandatory preaggregated well/grid time series.
4. Add source-domain multi-lag frame-difference outputs.
5. Add local-pixel-support fields and supported aggregate traces.
6. Add common-mode/reference-region traces.
7. Build a small source-versus-AV1 validation batch.

### Phase 2 — Understand residuals and artifacts

1. Build a residual sampler from:
   - high supported MV / low cd(10);
   - high cd(10) / low supported MV;
   - high both;
   - low both;
   - high reference-region/common-mode motion;
   - strong spatially focal motion.
2. Generate standardized visual audit panels.
3. Curate concise source-of-motion annotations.
4. Run DIS and PIV-style comparisons on the audit corpus.
5. Quantify the effect of:
   - local pixel support;
   - temporal aggregation;
   - global-motion adjustment;
   - texture-aware stratification;
   - motion-estimation parameter changes.

### Phase 3 — Build feature v1

1. Implement a compact, interpretable feature set.
2. Calculate stimulus-aligned event morphology.
3. Calculate repeated-stimulus response organization.
4. Build per-well review reports.
5. Evaluate replicate stability and cross-run robustness.
6. Add feature tables to the existing behavioral/QC ecosystem.

### Phase 4 — Deploy selectively

1. Use the sidecar to support video review and QC on known-good videos.
2. Evaluate whether features improve interpretation of DMSO variation, phenotype outliers, and stimulus-specific responses.
3. Promote stable feature families into routine prospective analysis.
4. Retain the ability to rederive higher-level feature sets as methods improve.

---

## Definition of success

The project is succeeding when it provides a durable, reproducible answer to questions that cd(10) alone cannot answer, while remaining inspectable against the underlying video.

Concrete success criteria include:

- historical AV1 archive and source-domain v1 sidecar complete with manifests and checksums;
- robust source-versus-AV1 feature compatibility for approved downstream feature sets;
- review panels that make it easy to distinguish distributed, focal, coherent, persistent, and common-mode motion patterns;
- a curated audit corpus linking feature behavior to visible image causes;
- well-level features that improve reproducible phenotype review or QC beyond scalar motion alone;
- clear provenance for every number used in downstream biology;
- a feature pipeline that can evolve without making earlier video data uninterpretable.

---

## Communication style for agents

When working in this repository:

- State assumptions explicitly.
- Preserve existing validated results; extend them with versioned additions.
- Present quantitative evidence and representative review panels before recommending a pipeline change.
- Treat low-correlation or disagreement cases as informative examples to inspect.
- Prefer implementation plans that are testable on a small representative panel before scaling to the historical corpus.
- Write concise technical notes explaining what each measurement captures, what supports it in the video, and where interpretation remains provisional.
- Keep recommendations tightly connected to the data and the stated scientific questions.
