# mestimate-sidecar v1 schema

The extractor writes one vector table, one frame summary table, and one JSON
metadata file per input video.

## Vector rows

Files:

```text
<stem>.mestimate-v1.vectors.csv.gz
<stem>.mestimate-v1.vectors.bin.gz
```

CSV is the default compatibility/debug representation. Binary is the preferred
representation when retaining many vector rows, selected with
`--vector-format bin`.

Columns:

```text
frame_index,pts,time_seconds,vector_index,source,w,h,src_x,src_y,dst_x,dst_y,motion_x,motion_y,motion_scale,flags,dx_px,dy_px,magnitude_px
```

`dx_px` is `dst_x - src_x`, `dy_px` is `dst_y - src_y`, and
`magnitude_px` is `sqrt(dx_px^2 + dy_px^2)`.

By default this table contains every vector emitted by FFmpeg `mestimate`.
For storage control, vector rows can be disabled or written as a deterministic
sampled audit subset. Output mode and sampling settings are recorded in metadata
under `vector_sampling`. Current modes and dials are:

```text
output: all | sampled | none
source: all | past | future
frame_stride: keep every Nth output frame
spatial_stride: keep blocks on a regular destination-block lattice
min_magnitude_px: keep vectors at or above this magnitude
```

When `output` is `none`, no vector row file is written. When `output` is
`sampled`, the vector table is an audit/debug stream, not the complete set of
vectors used for frame summaries.

Binary vector rows use a 32-byte header:

```text
magic: MSCVB1\0\0
version: uint32 = 1
endian_marker: uint32 = 0x01020304
header_size: uint32 = 32
record_size: uint32 = 76
field_count: uint32 = 18
reserved: uint32 = 0
```

Each record stores the same logical fields as the CSV schema in fixed order:
integer source fields are kept as integer fields, while `time_seconds` and
`magnitude_px` are stored as little-endian `float32`. Exact timing should use
`pts` plus the recorded timebase; `time_seconds` is a convenience field.

## Frame summaries

Files:

```text
<stem>.mestimate-v1.frames.csv.gz
<stem>.mestimate-v1.frames.bin.gz
```

CSV is the default and human-inspectable representation. Binary is an
experimental fixed-width representation selected with `--frame-output bin`;
benchmark before using it as an archival default.

Columns:

```text
frame_index,pts,time_seconds,n_vectors,mean_dx_px,mean_dy_px,mean_magnitude_px,median_magnitude_px,p90_magnitude_px,p95_magnitude_px,max_magnitude_px,sum_magnitude_px,resultant_magnitude_px,coherence,frame_diff_threshold,frame_diff_changed_pixels,frame_diff_changed_fraction,frame_diff_abs_sum,frame_diff_abs_mean
```

`coherence` is `resultant_magnitude_px / (sum_magnitude_px + 1e-12)`, and is
defined as `0` for frames with zero vectors or zero summed magnitude.

Frame summaries always use all vectors available on that filtered frame,
regardless of vector-row sampling. `n_vectors` is the raw count seen by the
summarizer, not the number of rows retained in a sampled vector row file.

The frame table also includes a cheap cd-style lag-1 grayscale image-difference
channel computed on the same `format=gray` filtered frames:

```text
frame_diff_changed_pixels = count(abs(current_gray - previous_gray) > threshold)
```

The default threshold is 10, matching the familiar cd(10)-style changed-pixel
count. The first output frame has no previous frame and is recorded as zero.
`frame_diff_abs_sum` and `frame_diff_abs_mean` preserve the total absolute
intensity change separately from the thresholded changed-pixel count.

## Metadata

File: `<stem>.mestimate-v1.metadata.json`

The metadata records the input file hash and stream properties, effective
filtergraph, linked FFmpeg library versions, output row counts, and hashes of
the finalized compressed output files.

For CSV, frame-summary floats are written with configurable significant-digit
precision. The default is 6 significant digits and is recorded in
`frame_summary_encoding.float_significant_digits`. Binary frame summaries store
the same fields as fixed-width little-endian records after a 32-byte header:

```text
magic: MSCFB1\0\0
version: uint32 = 1
endian_marker: uint32 = 0x01020304
header_size: uint32 = 32
record_size: uint32 = 92
field_count: uint32 = 19
reserved: uint32 = 0
```

Each record then stores the frame-summary fields in the same order as the CSV
schema. Frame and pixel counts use integer fields; summary-valued floating
fields use little-endian `float32`. Exact sizes and float type are recorded in
`frame_summary_encoding`.

Important output counts:

```text
raw_vector_row_count: vectors seen by frame summarization
vector_candidate_row_count: vectors passing sampling filters before writing
vector_row_count: rows actually written to the vector row file
vector_sampled_frame_count: frames eligible for sampled vector-row writing
frame_row_count: frame summary rows
```
