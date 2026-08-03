# Wynton archival benchmark status — 2026-08-02

This is a dated snapshot, not a live status page. It records the exact state
that motivated the current documentation and defaults.

## Corpus inventory

The current manifest contains 6,481 source plate HEVCs and 622,176 expected well
outputs. Source HEVC bytes total 5,977,201,148,957 bytes (5.98 TB decimal).

Compressed source-size landmarks are:

| Percentile | Bytes | Interpretation |
| --- | ---: | --- |
| median | 184,689,479 | compressed HEVC file size |
| p90 | 4,168,994,737 | compressed HEVC file size |
| p99 | 8,349,672,495 | compressed HEVC file size |
| maximum | 48,529,958,839 | compressed HEVC file size |

These are byte-size percentiles, not duration percentiles. The selected p90 and
p99 stress sources are approximately 13.7 and 25.7 minutes at 100 fps,
respectively. They both satisfy the current 3,600-second test-wave cap.

## Code and image provenance

- canonical local code for subsequent waves: commit
  `15ef14bbc0df24ed4a3979374c26d8d35c8601b8`;
- tested Wynton SIF:
  `/wynton/scratch/chelsell/encoder_based_ethology/containers/mestimate_sidecar_2f51ba952c1d.sif`;
- SIF SHA-256:
  `df9628122e2c1bc00a3cb5c606b0fdda45d6a618d829d08ab41847a7380cf1fa`.

The image contains FFmpeg/ffprobe, Python, `mestimate-sidecar`, and rsync. The
orchestration scripts run from a clean, committed repository checkout bound into
the container.

## Active benchmark arrays at snapshot

| Job | Sources | Validation | Resources per task | Repository |
| --- | ---: | --- | --- | --- |
| `4361886` | 5 ordinary/mixed | full decode of all wells | 1 core, 4G RAM, 20G scratch | `2f51ba9` |
| `4362331` | same 5 | packet count plus 5 decoded sentinels | 1 core, 4G RAM, 20G scratch | `25a1801` |
| `4362745` | compressed-size p90/p99 | packet count plus 5 decoded sentinels | 1 core, 4G RAM, 50G scratch | `25a1801` |

All source transfers used resumable rsync directly to `dt2.wynton.ucsf.edu`.
Local and staged SHA-256 values matched. Measured transfer throughput for the
4.17 GB and 8.35 GB stress sources was approximately 111 MB/s.

No task had completed at 23:25 PDT. The first array had exceeded one hour per
task, demonstrating that one hour is a runtime lower bound for planning, not an
upper bound. Observed maximum virtual memory was approximately 2.3G.

Lightweight control operations use `log2.wynton.ucsf.edu`; bulk data does not
pass through the login node. No SSH tunnel is required for job execution or
transfer. A persistent SSH ControlMaster is only a monitoring convenience.

At this snapshot, Wynton's SGE configuration assigns user `chelsell` to project
`grabelab`, and submitted jobs run in `member.q`. The user is not authorized for
the `sellolab` SGE project despite having Unix primary group `sello`. Production
use should retain the scheduler's configured project only with the allocation
owner's agreement; the repository does not hard-code a `-P` project override.

## Current operational behavior

1. Stage a bounded source wave under `/wynton/scratch/$USER` through `dt2`.
2. Submit one array task per plate and one CPU slot per task.
3. Copy the active source to job-local `$TMPDIR`.
4. Decode the source once and fan out 96 deterministic well crops to independent
   grayscale AV1 files using libaom, CRF 35, CPU-used 8.
5. Validate all outputs with packet counts/codec/geometry consistency and fully
   decode five deterministic sentinel wells.
6. Rsync validated results to bounded shared Wynton scratch for prompt
   collection to durable `/shire/store` storage.
7. Keep primary HEVC files until a cloud backup identity/checksum and all
   required source-domain QC products have been verified.

Test waves reject sources longer than 3,600 seconds before node-local copying.
Setting the limit to zero is an explicit override for separately budgeted longer
sources.

## Important incompleteness

Active jobs use `RUN_SIDECAR=0`. They benchmark well-video encoding and output
validation only. They do not yet calculate the source-domain multi-lag image
dynamics, compact MV/spatial summaries, or plate/reference common-mode traces
required by `configs/qc/clipsift_qc_bridge_v0_1.json`.

The exact durable `/shire/store` layout is not frozen. The current preference is
a versioned product alongside each submission's existing `camera` directory,
without overwriting the HEVC source.
