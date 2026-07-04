# MV features v1 schema

`mv_features_v1` is a derived product built from a `mestimate-sidecar` raw
sidecar. It is intended to be the routine analysis interface when recording
every vector row is too expensive.

The initial implementation is:

```bash
python scripts/integrate_mestimate_sidecar.py \
  --frames <stem>.mestimate-v1.frames.csv.gz \
  --vectors <stem>.mestimate-v1.vectors.csv.gz \
  --metadata <stem>.mestimate-v1.metadata.json \
  --output-dir <derived_dir>
```

`--vectors` may also point to `<stem>.mestimate-v1.vectors.bin.gz`.

## Outputs

For input stem `<stem>`:

```text
<stem>.mv-features-v1.frames.csv.gz
<stem>.mv-features-v1.bin-50ms.csv.gz
<stem>.mv-features-v1.bin-100ms.csv.gz
<stem>.mv-features-v1.bin-250ms.csv.gz
<stem>.mv-features-v1.metadata.json
```

The frame table has one row per source sidecar frame. The bin tables have one
row per fixed-width time bin derived from `time_seconds`.

## Core frame fields

```text
frame_index
pts
time_seconds
analysis_n_vectors
analysis_motion_energy
analysis_mean_magnitude_px
analysis_p95_magnitude_px
analysis_sum_dx_px
analysis_sum_dy_px
mv_active_vectors
mv_active_motion_energy
mv_active_mean_magnitude_px
mvaf
mv_active_frame
mv_active_run_frames
mv_bout_frame
mv_bout_active_vectors
mv_bout_motion_energy
mv_bout_fraction
mv_capped_active_fraction
mv_log_active_fraction
mv_inverse_saturation_fraction
active_spatial_bin_fraction
spatial_entropy
max_bin_fraction
motion_centroid_x_norm
motion_centroid_y_norm
vector_source_filter
```

`mvaf` means motion-vector active fraction: the fraction of selected-source
blocks whose `magnitude_px` is greater than the configured active-vector
threshold.

`mv_bout_fraction` is `mvaf` after zeroing frames that do not meet the configured
minimum active block count or are not part of a sufficiently persistent active
run.

Spatial fields are computed from active vectors on a versioned Cartesian grid.
The centroid is normalized to `[0, 1]` within the inferred vector destination
extent, not a physical well coordinate system.

## Core bin fields

Each bin table includes:

```text
bin_index
time_start_seconds
time_end_seconds
frame_index_start
frame_index_end
n_frames
analysis_n_vectors_median
analysis_motion_energy_sum
analysis_motion_energy_mean
mv_active_vectors_sum
mv_active_vectors_max
mvaf_mean
mvaf_peak
mv_active_frame_fraction
mv_active_frames
mv_longest_active_run_frames
mv_bout_fraction_mean
mv_bout_fraction_peak
mv_bout_active_vectors_sum
mv_bout_frame_fraction
mv_capped_active_fraction_mean
mv_log_active_fraction_mean
mv_inverse_saturation_fraction_mean
coherence_mean
coherence_active_mean
resultant_magnitude_px_mean
p95_magnitude_px_peak
active_spatial_bin_fraction_mean
active_spatial_bin_fraction_peak
spatial_entropy_mean
max_bin_fraction_mean
motion_centroid_x_norm_mean
motion_centroid_y_norm_mean
motion_centroid_x_norm_sd
motion_centroid_y_norm_sd
```

These are engineering summaries for review and modeling. They are not biological
labels and should be compared against stimulus timing, cd(10), image dynamics,
and visual audit clips.

## Candidate spatial positive control

Bromocriptine is reported by Matt to reliably make fish hug the well edge. This
is a useful candidate known-effect phenotype for judging whether MV-derived
features capture spatial organization beyond total activity.

Feature versions should therefore prioritize explicit center-versus-wall
summaries, including wall-zone motion fraction, center-to-wall ratio, motion
centroid distance from the well center, and radial activity persistence. A
successful feature need not show higher total motion for bromocriptine; the
expected signal may be spatial redistribution toward the well boundary.
