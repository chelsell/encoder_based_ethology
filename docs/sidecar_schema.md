# mestimate-sidecar v1 schema

The extractor writes one raw vector table, one frame summary table, and one JSON
metadata file per input video.

## Raw vectors

File: `<stem>.mestimate-v1.vectors.csv.gz`

Columns:

```text
frame_index,pts,time_seconds,vector_index,source,w,h,src_x,src_y,dst_x,dst_y,motion_x,motion_y,motion_scale,flags,dx_px,dy_px,magnitude_px
```

`dx_px` is `dst_x - src_x`, `dy_px` is `dst_y - src_y`, and
`magnitude_px` is `sqrt(dx_px^2 + dy_px^2)`.

## Frame summaries

File: `<stem>.mestimate-v1.frames.csv.gz`

Columns:

```text
frame_index,pts,time_seconds,n_vectors,mean_dx_px,mean_dy_px,mean_magnitude_px,median_magnitude_px,p90_magnitude_px,p95_magnitude_px,max_magnitude_px,sum_magnitude_px,resultant_magnitude_px,coherence
```

`coherence` is `resultant_magnitude_px / (sum_magnitude_px + 1e-12)`, and is
defined as `0` for frames with zero vectors or zero summed magnitude.

## Metadata

File: `<stem>.mestimate-v1.metadata.json`

The metadata records the input file hash and stream properties, effective
filtergraph, linked FFmpeg library versions, output row counts, and hashes of
the finalized compressed CSV files.
