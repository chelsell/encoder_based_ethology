#define _POSIX_C_SOURCE 200809L

#include "mestimate_sidecar.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include <zlib.h>

#include <libavcodec/avcodec.h>
#include <libavfilter/avfilter.h>
#include <libavfilter/buffersink.h>
#include <libavfilter/buffersrc.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
#include <libavutil/imgutils.h>
#include <libavutil/motion_vector.h>
#include <libavutil/pixdesc.h>
#include <libavutil/sha.h>

#ifndef MESTIMATE_BUILD_TYPE
#define MESTIMATE_BUILD_TYPE "unknown"
#endif

typedef struct Options {
    const char *input_path;
    const char *output_dir;
    const char *method;
    const char *frame_output;
    const char *vector_output;
    const char *vector_format;
    const char *vector_source;
    int mb_size;
    int search_param;
    int frame_diff_threshold;
    int summary_float_precision;
    int vector_frame_stride;
    int vector_spatial_stride;
    double vector_min_magnitude;
    bool force;
    bool dry_run;
} Options;

typedef struct Paths {
    char stem[1024];
    char vectors[2048];
    char frames[2048];
    char metadata[2048];
} Paths;

typedef struct InputInfo {
    char basename[1024];
    char sha256[65];
    int64_t size_bytes;
    const char *container_format;
    const char *video_codec;
    const char *pixel_format;
    int width;
    int height;
    AVRational nominal_fps;
    AVRational time_base;
    double duration_seconds;
} InputInfo;

typedef struct Context {
    Options opt;
    Paths paths;
    InputInfo info;
    AVFormatContext *fmt;
    AVCodecContext *dec;
    AVFilterGraph *graph;
    AVFilterContext *buffersrc;
    AVFilterContext *buffersink;
    int video_stream_index;
    AVRational sink_time_base;
    gzFile vectors_gz;
    gzFile frames_gz;
    uint8_t *prev_gray;
    size_t prev_gray_size;
    bool has_prev_gray;
    int64_t raw_vector_rows;
    int64_t vector_candidate_rows;
    int64_t vector_rows;
    int64_t frame_rows;
    int64_t vector_sampled_frame_rows;
    int64_t frame_index;
    char vectors_sha256[65];
    char frames_sha256[65];
} Context;

typedef struct FrameSummaryRecord {
    int64_t frame_index;
    int64_t pts;
    double time_seconds;
    int32_t n_vectors;
    double mean_dx_px;
    double mean_dy_px;
    double mean_magnitude_px;
    double median_magnitude_px;
    double p90_magnitude_px;
    double p95_magnitude_px;
    double max_magnitude_px;
    double sum_magnitude_px;
    double resultant_magnitude_px;
    double coherence;
    int32_t frame_diff_threshold;
    int64_t frame_diff_changed_pixels;
    double frame_diff_changed_fraction;
    int64_t frame_diff_abs_sum;
    double frame_diff_abs_mean;
} FrameSummaryRecord;

typedef struct FrameSummaryBinaryHeader {
    char magic[8];
    uint32_t version;
    uint32_t endian_marker;
    uint32_t header_size;
    uint32_t record_size;
    uint32_t field_count;
    uint32_t reserved;
} FrameSummaryBinaryHeader;

#define FRAME_SUMMARY_BINARY_RECORD_SIZE 92u

typedef struct VectorBinaryHeader {
    char magic[8];
    uint32_t version;
    uint32_t endian_marker;
    uint32_t header_size;
    uint32_t record_size;
    uint32_t field_count;
    uint32_t reserved;
} VectorBinaryHeader;

#define VECTOR_BINARY_RECORD_SIZE 76u

static void usage(FILE *out) {
    fprintf(out,
            "Usage: mestimate-sidecar --input PATH --output-dir DIR [options]\n\n"
            "Options:\n"
            "  --method METHOD          Default: epzs\n"
            "  --mb-size INTEGER        Default: 16\n"
            "  --search-param INTEGER   Default: 12\n"
            "  --frame-diff-threshold N Lag-1 grayscale changed-pixel threshold. Default: 10\n"
            "  --frame-output FORMAT    Frame summary format: csv, bin. Default: csv\n"
            "  --summary-float-precision N Significant digits for frame-summary floats. Default: 6\n"
            "  --vector-output MODE     Vector row mode: all, sampled, none. Default: all\n"
            "  --vector-format FORMAT   Vector row format: csv, bin. Default: csv\n"
            "  --vector-source SOURCE   Vector rows to write: all, past, future. Default: all\n"
            "  --vector-frame-stride N  Write vector rows only for every Nth output frame. Default: 1\n"
            "  --vector-spatial-stride N Write vector rows only for every Nth block in x and y. Default: 1\n"
            "  --vector-min-magnitude X Write only vectors with magnitude >= X px. Default: 0\n"
            "  --force, --overwrite     Replace existing sidecar files\n"
            "  --dry-run                Validate setup without writing sidecars\n"
            "  --help                   Show this help\n"
            "  --version                Print version and FFmpeg library versions\n\n"
            "Outputs:\n"
            "  <stem>.mestimate-v1.vectors.csv.gz or <stem>.mestimate-v1.vectors.bin.gz (unless --vector-output none)\n"
            "  <stem>.mestimate-v1.frames.csv.gz or <stem>.mestimate-v1.frames.bin.gz\n"
            "  <stem>.mestimate-v1.metadata.json\n");
}

static void print_version(void) {
    printf("mestimate-sidecar %s\n", MESTIMATE_SIDECAR_VERSION);
    printf("libavformat %u.%u.%u\n", LIBAVFORMAT_VERSION_MAJOR, LIBAVFORMAT_VERSION_MINOR, LIBAVFORMAT_VERSION_MICRO);
    printf("libavcodec %u.%u.%u\n", LIBAVCODEC_VERSION_MAJOR, LIBAVCODEC_VERSION_MINOR, LIBAVCODEC_VERSION_MICRO);
    printf("libavfilter %u.%u.%u\n", LIBAVFILTER_VERSION_MAJOR, LIBAVFILTER_VERSION_MINOR, LIBAVFILTER_VERSION_MICRO);
    printf("libavutil %u.%u.%u\n", LIBAVUTIL_VERSION_MAJOR, LIBAVUTIL_VERSION_MINOR, LIBAVUTIL_VERSION_MICRO);
}

static void fferr(int err, char *buf, size_t size) {
    av_strerror(err, buf, size);
}

static int fail(const char *msg) {
    fprintf(stderr, "ERROR: %s\n", msg);
    return -1;
}

static const char *path_basename(const char *path) {
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static void strip_extension(char *dst, size_t dst_size, const char *basename) {
    snprintf(dst, dst_size, "%s", basename);
    char *dot = strrchr(dst, '.');
    if (dot && dot != dst) {
        *dot = '\0';
    }
}

static int file_exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0;
}

static int make_dir_if_needed(const char *path) {
    if (mkdir(path, 0775) == 0) {
        return 0;
    }
    if (errno == EEXIST) {
        struct stat st;
        if (stat(path, &st) == 0 && S_ISDIR(st.st_mode)) {
            return 0;
        }
    }
    perror(path);
    return -1;
}

static int make_dirs(const char *path) {
    char tmp[2048];
    snprintf(tmp, sizeof(tmp), "%s", path);
    size_t len = strlen(tmp);
    if (len == 0) {
        return fail("empty output directory");
    }
    if (tmp[len - 1] == '/') {
        tmp[len - 1] = '\0';
    }
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            if (make_dir_if_needed(tmp) < 0) {
                return -1;
            }
            *p = '/';
        }
    }
    return make_dir_if_needed(tmp);
}

static int sha256_file(const char *path, char out[65], int64_t *size_out) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        perror(path);
        return -1;
    }

    struct AVSHA *sha = av_sha_alloc();
    if (!sha) {
        fclose(f);
        return fail("failed to allocate SHA-256 context");
    }
    av_sha_init(sha, 256);

    unsigned char buf[65536];
    int64_t total = 0;
    for (;;) {
        size_t n = fread(buf, 1, sizeof(buf), f);
        if (n > 0) {
            av_sha_update(sha, buf, n);
            total += (int64_t)n;
        }
        if (n < sizeof(buf)) {
            if (ferror(f)) {
                perror(path);
                av_free(sha);
                fclose(f);
                return -1;
            }
            break;
        }
    }

    uint8_t digest[32];
    av_sha_final(sha, digest);
    for (int i = 0; i < 32; i++) {
        snprintf(out + i * 2, 3, "%02x", digest[i]);
    }
    out[64] = '\0';
    if (size_out) {
        *size_out = total;
    }
    av_free(sha);
    fclose(f);
    return 0;
}

static int parse_int_arg(const char *name, const char *value, int *out) {
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (!value[0] || *end != '\0' || parsed <= 0 || parsed > 4096) {
        fprintf(stderr, "ERROR: invalid %s: %s\n", name, value);
        return -1;
    }
    *out = (int)parsed;
    return 0;
}

static int parse_nonnegative_double_arg(const char *name, const char *value, double *out) {
    char *end = NULL;
    double parsed = strtod(value, &end);
    if (!value[0] || *end != '\0' || parsed < 0.0 || !isfinite(parsed)) {
        fprintf(stderr, "ERROR: invalid %s: %s\n", name, value);
        return -1;
    }
    *out = parsed;
    return 0;
}

static int parse_pixel_threshold_arg(const char *name, const char *value, int *out) {
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (!value[0] || *end != '\0' || parsed < 0 || parsed > 255) {
        fprintf(stderr, "ERROR: invalid %s: %s\n", name, value);
        return -1;
    }
    *out = (int)parsed;
    return 0;
}

static int parse_precision_arg(const char *name, const char *value, int *out) {
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (!value[0] || *end != '\0' || parsed < 1 || parsed > 17) {
        fprintf(stderr, "ERROR: invalid %s: %s\n", name, value);
        return -1;
    }
    *out = (int)parsed;
    return 0;
}

static bool vector_source_allowed(const char *source_filter, int32_t source) {
    if (strcmp(source_filter, "all") == 0) return true;
    if (strcmp(source_filter, "past") == 0) return source < 0;
    if (strcmp(source_filter, "future") == 0) return source > 0;
    return false;
}

static bool frame_output_is_binary(const Options *opt) {
    return strcmp(opt->frame_output, "bin") == 0;
}

static bool vector_format_is_binary(const Options *opt) {
    return strcmp(opt->vector_format, "bin") == 0;
}

static bool vector_spatial_allowed(const Options *opt, const AVMotionVector *mv) {
    if (opt->vector_spatial_stride <= 1) return true;
    if (opt->mb_size <= 0) return true;
    int bx = mv->dst_x / opt->mb_size;
    int by = mv->dst_y / opt->mb_size;
    return (bx % opt->vector_spatial_stride) == 0 && (by % opt->vector_spatial_stride) == 0;
}

static int parse_args(int argc, char **argv, Options *opt) {
    *opt = (Options){
        .method = MESTIMATE_DEFAULT_METHOD,
        .frame_output = "csv",
        .vector_output = "all",
        .vector_format = "csv",
        .vector_source = "all",
        .mb_size = MESTIMATE_DEFAULT_MB_SIZE,
        .search_param = MESTIMATE_DEFAULT_SEARCH_PARAM,
        .frame_diff_threshold = 10,
        .summary_float_precision = 6,
        .vector_frame_stride = 1,
        .vector_spatial_stride = 1,
        .vector_min_magnitude = 0.0,
    };

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            usage(stdout);
            exit(0);
        } else if (strcmp(argv[i], "--version") == 0) {
            print_version();
            exit(0);
        } else if (strcmp(argv[i], "--input") == 0 && i + 1 < argc) {
            opt->input_path = argv[++i];
        } else if (strcmp(argv[i], "--output-dir") == 0 && i + 1 < argc) {
            opt->output_dir = argv[++i];
        } else if (strcmp(argv[i], "--method") == 0 && i + 1 < argc) {
            opt->method = argv[++i];
        } else if (strcmp(argv[i], "--mb-size") == 0 && i + 1 < argc) {
            if (parse_int_arg("--mb-size", argv[++i], &opt->mb_size) < 0) return -1;
        } else if (strcmp(argv[i], "--search-param") == 0 && i + 1 < argc) {
            if (parse_int_arg("--search-param", argv[++i], &opt->search_param) < 0) return -1;
        } else if (strcmp(argv[i], "--frame-diff-threshold") == 0 && i + 1 < argc) {
            if (parse_pixel_threshold_arg("--frame-diff-threshold", argv[++i], &opt->frame_diff_threshold) < 0) return -1;
        } else if (strcmp(argv[i], "--frame-output") == 0 && i + 1 < argc) {
            opt->frame_output = argv[++i];
            if (strcmp(opt->frame_output, "csv") != 0 &&
                strcmp(opt->frame_output, "bin") != 0) {
                fprintf(stderr, "ERROR: invalid --frame-output: %s\n", opt->frame_output);
                return -1;
            }
        } else if (strcmp(argv[i], "--summary-float-precision") == 0 && i + 1 < argc) {
            if (parse_precision_arg("--summary-float-precision", argv[++i], &opt->summary_float_precision) < 0) return -1;
        } else if (strcmp(argv[i], "--vector-output") == 0 && i + 1 < argc) {
            opt->vector_output = argv[++i];
            if (strcmp(opt->vector_output, "all") != 0 &&
                strcmp(opt->vector_output, "sampled") != 0 &&
                strcmp(opt->vector_output, "none") != 0) {
                fprintf(stderr, "ERROR: invalid --vector-output: %s\n", opt->vector_output);
                return -1;
            }
        } else if (strcmp(argv[i], "--vector-format") == 0 && i + 1 < argc) {
            opt->vector_format = argv[++i];
            if (strcmp(opt->vector_format, "csv") != 0 &&
                strcmp(opt->vector_format, "bin") != 0) {
                fprintf(stderr, "ERROR: invalid --vector-format: %s\n", opt->vector_format);
                return -1;
            }
        } else if (strcmp(argv[i], "--vector-source") == 0 && i + 1 < argc) {
            opt->vector_source = argv[++i];
            if (strcmp(opt->vector_source, "all") != 0 &&
                strcmp(opt->vector_source, "past") != 0 &&
                strcmp(opt->vector_source, "future") != 0) {
                fprintf(stderr, "ERROR: invalid --vector-source: %s\n", opt->vector_source);
                return -1;
            }
        } else if (strcmp(argv[i], "--vector-frame-stride") == 0 && i + 1 < argc) {
            if (parse_int_arg("--vector-frame-stride", argv[++i], &opt->vector_frame_stride) < 0) return -1;
        } else if (strcmp(argv[i], "--vector-spatial-stride") == 0 && i + 1 < argc) {
            if (parse_int_arg("--vector-spatial-stride", argv[++i], &opt->vector_spatial_stride) < 0) return -1;
        } else if (strcmp(argv[i], "--vector-min-magnitude") == 0 && i + 1 < argc) {
            if (parse_nonnegative_double_arg("--vector-min-magnitude", argv[++i], &opt->vector_min_magnitude) < 0) return -1;
        } else if (strcmp(argv[i], "--force") == 0 || strcmp(argv[i], "--overwrite") == 0) {
            opt->force = true;
        } else if (strcmp(argv[i], "--dry-run") == 0) {
            opt->dry_run = true;
        } else {
            fprintf(stderr, "ERROR: unknown or incomplete argument: %s\n", argv[i]);
            usage(stderr);
            return -1;
        }
    }

    if (!opt->input_path || !opt->output_dir) {
        usage(stderr);
        return fail("--input and --output-dir are required");
    }
    return 0;
}

static void build_paths(Context *ctx) {
    const char *base = path_basename(ctx->opt.input_path);
    snprintf(ctx->info.basename, sizeof(ctx->info.basename), "%s", base);
    strip_extension(ctx->paths.stem, sizeof(ctx->paths.stem), base);
    snprintf(ctx->paths.vectors, sizeof(ctx->paths.vectors), "%s/%s.mestimate-v1.vectors.%s.gz",
             ctx->opt.output_dir, ctx->paths.stem, vector_format_is_binary(&ctx->opt) ? "bin" : "csv");
    snprintf(ctx->paths.frames, sizeof(ctx->paths.frames), "%s/%s.mestimate-v1.frames.%s.gz",
             ctx->opt.output_dir, ctx->paths.stem, frame_output_is_binary(&ctx->opt) ? "bin" : "csv");
    snprintf(ctx->paths.metadata, sizeof(ctx->paths.metadata), "%s/%s.mestimate-v1.metadata.json",
             ctx->opt.output_dir, ctx->paths.stem);
}

static int ensure_outputs_ok(Context *ctx) {
    if (make_dirs(ctx->opt.output_dir) < 0) {
        return -1;
    }
    bool vectors_conflict = strcmp(ctx->opt.vector_output, "none") != 0 && file_exists(ctx->paths.vectors);
    if (!ctx->opt.force &&
        (vectors_conflict || file_exists(ctx->paths.frames) || file_exists(ctx->paths.metadata))) {
        return fail("output sidecar files already exist; pass --force or --overwrite to replace them");
    }
    return 0;
}

static int open_input(Context *ctx) {
    int ret = avformat_open_input(&ctx->fmt, ctx->opt.input_path, NULL, NULL);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to open input: %s\n", buf);
        return -1;
    }
    ret = avformat_find_stream_info(ctx->fmt, NULL);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to read stream info: %s\n", buf);
        return -1;
    }

    ret = av_find_best_stream(ctx->fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (ret < 0) {
        return fail("no video stream found");
    }
    ctx->video_stream_index = ret;
    AVStream *st = ctx->fmt->streams[ctx->video_stream_index];
    const AVCodec *codec = avcodec_find_decoder(st->codecpar->codec_id);
    if (!codec) {
        return fail("failed to find decoder for selected video stream");
    }

    ctx->dec = avcodec_alloc_context3(codec);
    if (!ctx->dec) {
        return fail("failed to allocate decoder context");
    }
    ret = avcodec_parameters_to_context(ctx->dec, st->codecpar);
    if (ret < 0) {
        return fail("failed to copy codec parameters");
    }
    ret = avcodec_open2(ctx->dec, codec, NULL);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to open decoder: %s\n", buf);
        return -1;
    }

    if (sha256_file(ctx->opt.input_path, ctx->info.sha256, &ctx->info.size_bytes) < 0) {
        return -1;
    }
    ctx->info.container_format = ctx->fmt->iformat ? ctx->fmt->iformat->name : "";
    ctx->info.video_codec = codec->name ? codec->name : "";
    ctx->info.pixel_format = av_get_pix_fmt_name(ctx->dec->pix_fmt);
    if (!ctx->info.pixel_format) ctx->info.pixel_format = "unknown";
    ctx->info.width = ctx->dec->width;
    ctx->info.height = ctx->dec->height;
    ctx->info.nominal_fps = st->avg_frame_rate.num && st->avg_frame_rate.den ? st->avg_frame_rate : st->r_frame_rate;
    ctx->info.time_base = st->time_base;
    ctx->info.duration_seconds = st->duration != AV_NOPTS_VALUE
        ? st->duration * av_q2d(st->time_base)
        : (ctx->fmt->duration != AV_NOPTS_VALUE ? ctx->fmt->duration / (double)AV_TIME_BASE : 0.0);
    return 0;
}

static int init_filters(Context *ctx) {
    char args[1024];
    char filter_desc[512];
    int ret;
    const AVFilter *buffersrc = avfilter_get_by_name("buffer");
    const AVFilter *buffersink = avfilter_get_by_name("buffersink");
    AVStream *st = ctx->fmt->streams[ctx->video_stream_index];

    if (!buffersrc || !buffersink || !avfilter_get_by_name("mestimate")) {
        return fail("required FFmpeg filters are unavailable");
    }

    ctx->graph = avfilter_graph_alloc();
    if (!ctx->graph) {
        return fail("failed to allocate filter graph");
    }

    AVRational sar = ctx->dec->sample_aspect_ratio.num ? ctx->dec->sample_aspect_ratio : st->sample_aspect_ratio;
    if (!sar.num) sar = (AVRational){1, 1};
    snprintf(args, sizeof(args),
             "video_size=%dx%d:pix_fmt=%d:time_base=%d/%d:pixel_aspect=%d/%d",
             ctx->dec->width, ctx->dec->height, ctx->dec->pix_fmt,
             st->time_base.num, st->time_base.den, sar.num, sar.den);

    ret = avfilter_graph_create_filter(&ctx->buffersrc, buffersrc, "in", args, NULL, ctx->graph);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to create buffer source: %s\n", buf);
        return -1;
    }
    ret = avfilter_graph_create_filter(&ctx->buffersink, buffersink, "out", NULL, NULL, ctx->graph);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to create buffer sink: %s\n", buf);
        return -1;
    }

    snprintf(filter_desc, sizeof(filter_desc), "format=gray,mestimate=method=%s:mb_size=%d:search_param=%d",
             ctx->opt.method, ctx->opt.mb_size, ctx->opt.search_param);

    AVFilterInOut *outputs = avfilter_inout_alloc();
    AVFilterInOut *inputs = avfilter_inout_alloc();
    if (!outputs || !inputs) {
        avfilter_inout_free(&outputs);
        avfilter_inout_free(&inputs);
        return fail("failed to allocate filter graph endpoints");
    }
    outputs->name = av_strdup("in");
    outputs->filter_ctx = ctx->buffersrc;
    outputs->pad_idx = 0;
    outputs->next = NULL;
    inputs->name = av_strdup("out");
    inputs->filter_ctx = ctx->buffersink;
    inputs->pad_idx = 0;
    inputs->next = NULL;

    ret = avfilter_graph_parse_ptr(ctx->graph, filter_desc, &inputs, &outputs, NULL);
    avfilter_inout_free(&inputs);
    avfilter_inout_free(&outputs);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to parse filtergraph '%s': %s\n", filter_desc, buf);
        return -1;
    }

    ret = avfilter_graph_config(ctx->graph, NULL);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to configure filtergraph: %s\n", buf);
        return -1;
    }
    ctx->sink_time_base = av_buffersink_get_time_base(ctx->buffersink);
    return 0;
}

static int write_headers(Context *ctx) {
    if (strcmp(ctx->opt.vector_output, "none") != 0) {
        ctx->vectors_gz = gzopen(ctx->paths.vectors, "wb");
        if (!ctx->vectors_gz) {
            perror(ctx->paths.vectors);
            return -1;
        }
    }
    ctx->frames_gz = gzopen(ctx->paths.frames, "wb");
    if (!ctx->frames_gz) {
        perror(ctx->paths.frames);
        return -1;
    }
    if (ctx->vectors_gz) {
        if (vector_format_is_binary(&ctx->opt)) {
            VectorBinaryHeader header = {
                .magic = {'M', 'S', 'C', 'V', 'B', '1', '\0', '\0'},
                .version = 1,
                .endian_marker = 0x01020304u,
                .header_size = (uint32_t)sizeof(VectorBinaryHeader),
                .record_size = VECTOR_BINARY_RECORD_SIZE,
                .field_count = 18,
                .reserved = 0,
            };
            if (gzwrite(ctx->vectors_gz, &header, (unsigned int)sizeof(header)) != (int)sizeof(header)) {
                return fail("failed to write vector binary header");
            }
        } else {
            gzputs(ctx->vectors_gz,
                   "frame_index,pts,time_seconds,vector_index,source,w,h,src_x,src_y,dst_x,dst_y,motion_x,motion_y,motion_scale,flags,dx_px,dy_px,magnitude_px\n");
        }
    }
    if (frame_output_is_binary(&ctx->opt)) {
        FrameSummaryBinaryHeader header = {
            .magic = {'M', 'S', 'C', 'F', 'B', '1', '\0', '\0'},
            .version = 1,
            .endian_marker = 0x01020304u,
            .header_size = (uint32_t)sizeof(FrameSummaryBinaryHeader),
            .record_size = FRAME_SUMMARY_BINARY_RECORD_SIZE,
            .field_count = 19,
            .reserved = 0,
        };
        if (gzwrite(ctx->frames_gz, &header, (unsigned int)sizeof(header)) != (int)sizeof(header)) {
            return fail("failed to write frame-summary binary header");
        }
    } else {
        gzputs(ctx->frames_gz,
               "frame_index,pts,time_seconds,n_vectors,mean_dx_px,mean_dy_px,mean_magnitude_px,median_magnitude_px,p90_magnitude_px,p95_magnitude_px,max_magnitude_px,sum_magnitude_px,resultant_magnitude_px,coherence,frame_diff_threshold,frame_diff_changed_pixels,frame_diff_changed_fraction,frame_diff_abs_sum,frame_diff_abs_mean\n");
    }
    return 0;
}

static int compare_double(const void *a, const void *b) {
    double da = *(const double *)a;
    double db = *(const double *)b;
    return (da > db) - (da < db);
}

static double percentile_sorted(const double *values, int n, double p) {
    if (n <= 0) return 0.0;
    double pos = p * (double)(n - 1);
    int lo = (int)floor(pos);
    int hi = (int)ceil(pos);
    double frac = pos - lo;
    return values[lo] * (1.0 - frac) + values[hi] * frac;
}

static void write_time_field(gzFile f, int64_t pts, AVRational tb) {
    if (pts == AV_NOPTS_VALUE || tb.num == 0 || tb.den == 0) {
        gzputs(f, ",");
    } else {
        gzprintf(f, ",%.9f", pts * av_q2d(tb));
    }
}

static void write_time_field_precise(gzFile f, int64_t pts, AVRational tb, int precision) {
    if (pts == AV_NOPTS_VALUE || tb.num == 0 || tb.den == 0) {
        gzputs(f, ",");
    } else {
        gzprintf(f, ",%.*g", precision, pts * av_q2d(tb));
    }
}

static int gzwrite_exact(gzFile f, const void *data, size_t size, const char *label) {
    if (gzwrite(f, data, (unsigned int)size) != (int)size) {
        fprintf(stderr, "ERROR: failed to write %s\n", label);
        return -1;
    }
    return 0;
}

static void append_bytes(uint8_t **p, const void *value, size_t size) {
    memcpy(*p, value, size);
    *p += size;
}

static float frame_time_seconds(int64_t pts, AVRational tb) {
    if (pts == AV_NOPTS_VALUE || tb.num == 0 || tb.den == 0) {
        return NAN;
    }
    return (float)(pts * av_q2d(tb));
}

static int write_vector_row(Context *ctx, AVFrame *frame, int vector_index, const AVMotionVector *mv, int dx, int dy, double mag) {
    if (vector_format_is_binary(&ctx->opt)) {
        uint8_t record[VECTOR_BINARY_RECORD_SIZE];
        uint8_t *p = record;
        int64_t frame_index = ctx->frame_index;
        int64_t pts = frame->pts;
        float time_seconds = frame_time_seconds(frame->pts, ctx->sink_time_base);
        int32_t vector_index_i32 = vector_index;
        int32_t source = mv->source;
        uint32_t w = mv->w;
        uint32_t h = mv->h;
        int16_t src_x = mv->src_x;
        int16_t src_y = mv->src_y;
        int16_t dst_x = mv->dst_x;
        int16_t dst_y = mv->dst_y;
        int32_t motion_x = mv->motion_x;
        int32_t motion_y = mv->motion_y;
        uint32_t motion_scale = mv->motion_scale;
        uint64_t flags = mv->flags;
        int32_t dx_i32 = dx;
        int32_t dy_i32 = dy;
        float magnitude = (float)mag;
        append_bytes(&p, &frame_index, sizeof(frame_index));
        append_bytes(&p, &pts, sizeof(pts));
        append_bytes(&p, &time_seconds, sizeof(time_seconds));
        append_bytes(&p, &vector_index_i32, sizeof(vector_index_i32));
        append_bytes(&p, &source, sizeof(source));
        append_bytes(&p, &w, sizeof(w));
        append_bytes(&p, &h, sizeof(h));
        append_bytes(&p, &src_x, sizeof(src_x));
        append_bytes(&p, &src_y, sizeof(src_y));
        append_bytes(&p, &dst_x, sizeof(dst_x));
        append_bytes(&p, &dst_y, sizeof(dst_y));
        append_bytes(&p, &motion_x, sizeof(motion_x));
        append_bytes(&p, &motion_y, sizeof(motion_y));
        append_bytes(&p, &motion_scale, sizeof(motion_scale));
        append_bytes(&p, &flags, sizeof(flags));
        append_bytes(&p, &dx_i32, sizeof(dx_i32));
        append_bytes(&p, &dy_i32, sizeof(dy_i32));
        append_bytes(&p, &magnitude, sizeof(magnitude));
        if ((size_t)(p - record) != VECTOR_BINARY_RECORD_SIZE) {
            return fail("internal vector binary record size mismatch");
        }
        if (gzwrite_exact(ctx->vectors_gz, record, sizeof(record), "vector binary record") < 0) {
            return -1;
        }
    } else {
        gzprintf(ctx->vectors_gz,
                 "%" PRId64 ",%" PRId64,
                 ctx->frame_index, frame->pts);
        write_time_field(ctx->vectors_gz, frame->pts, ctx->sink_time_base);
        gzprintf(ctx->vectors_gz,
                 ",%d,%" PRId32 ",%u,%u,%" PRId16 ",%" PRId16 ",%" PRId16 ",%" PRId16
                 ",%" PRId32 ",%" PRId32 ",%u,%" PRIu64 ",%d,%d,%.9f\n",
                 vector_index, mv->source, mv->w, mv->h, mv->src_x, mv->src_y, mv->dst_x, mv->dst_y,
                 mv->motion_x, mv->motion_y, mv->motion_scale, mv->flags, dx, dy, mag);
    }
    ctx->vector_rows++;
    return 0;
}

static int compute_frame_difference(Context *ctx, AVFrame *frame, int64_t *changed_pixels, double *changed_fraction, int64_t *abs_sum, double *abs_mean) {
    int width = frame->width;
    int height = frame->height;
    if (width <= 0 || height <= 0) {
        *changed_pixels = 0;
        *changed_fraction = 0.0;
        *abs_sum = 0;
        *abs_mean = 0.0;
        return 0;
    }

    size_t needed = (size_t)width * (size_t)height;
    if (needed != ctx->prev_gray_size) {
        uint8_t *next = av_realloc(ctx->prev_gray, needed);
        if (!next) {
            return fail("failed to allocate previous-frame buffer");
        }
        ctx->prev_gray = next;
        ctx->prev_gray_size = needed;
        ctx->has_prev_gray = false;
    }

    int64_t changed = 0;
    int64_t sum_abs = 0;
    size_t offset = 0;
    for (int y = 0; y < height; y++) {
        const uint8_t *row = frame->data[0] + (ptrdiff_t)y * frame->linesize[0];
        for (int x = 0; x < width; x++) {
            uint8_t current = row[x];
            if (ctx->has_prev_gray) {
                int diff = abs((int)current - (int)ctx->prev_gray[offset]);
                if (diff > ctx->opt.frame_diff_threshold) {
                    changed++;
                }
                sum_abs += diff;
            }
            ctx->prev_gray[offset] = current;
            offset++;
        }
    }
    ctx->has_prev_gray = true;

    *changed_pixels = changed;
    *changed_fraction = needed ? (double)changed / (double)needed : 0.0;
    *abs_sum = sum_abs;
    *abs_mean = needed ? (double)sum_abs / (double)needed : 0.0;
    return 0;
}

static int write_frame_summary(Context *ctx, AVFrame *frame, int n,
                               double mean_dx, double mean_dy, double mean_mag,
                               double median, double p90, double p95, double max_mag,
                               double sum_mag, double resultant, double coherence,
                               int64_t diff_changed_pixels, double diff_changed_fraction,
                               int64_t diff_abs_sum, double diff_abs_mean) {
    double time_seconds = (frame->pts == AV_NOPTS_VALUE || ctx->sink_time_base.num == 0 || ctx->sink_time_base.den == 0)
        ? 0.0
        : frame->pts * av_q2d(ctx->sink_time_base);

    if (frame_output_is_binary(&ctx->opt)) {
        int64_t frame_index = ctx->frame_index;
        int64_t pts = frame->pts;
        float time_seconds_f = (float)time_seconds;
        int32_t n_vectors = n;
        float mean_dx_f = (float)mean_dx;
        float mean_dy_f = (float)mean_dy;
        float mean_mag_f = (float)mean_mag;
        float median_f = (float)median;
        float p90_f = (float)p90;
        float p95_f = (float)p95;
        float max_mag_f = (float)max_mag;
        float sum_mag_f = (float)sum_mag;
        float resultant_f = (float)resultant;
        float coherence_f = (float)coherence;
        int32_t threshold = ctx->opt.frame_diff_threshold;
        float diff_changed_fraction_f = (float)diff_changed_fraction;
        float diff_abs_mean_f = (float)diff_abs_mean;
        if (gzwrite_exact(ctx->frames_gz, &frame_index, sizeof(frame_index), "frame-summary frame_index") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &pts, sizeof(pts), "frame-summary pts") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &time_seconds_f, sizeof(time_seconds_f), "frame-summary time_seconds") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &n_vectors, sizeof(n_vectors), "frame-summary n_vectors") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &mean_dx_f, sizeof(mean_dx_f), "frame-summary mean_dx_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &mean_dy_f, sizeof(mean_dy_f), "frame-summary mean_dy_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &mean_mag_f, sizeof(mean_mag_f), "frame-summary mean_magnitude_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &median_f, sizeof(median_f), "frame-summary median_magnitude_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &p90_f, sizeof(p90_f), "frame-summary p90_magnitude_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &p95_f, sizeof(p95_f), "frame-summary p95_magnitude_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &max_mag_f, sizeof(max_mag_f), "frame-summary max_magnitude_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &sum_mag_f, sizeof(sum_mag_f), "frame-summary sum_magnitude_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &resultant_f, sizeof(resultant_f), "frame-summary resultant_magnitude_px") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &coherence_f, sizeof(coherence_f), "frame-summary coherence") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &threshold, sizeof(threshold), "frame-summary frame_diff_threshold") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &diff_changed_pixels, sizeof(diff_changed_pixels), "frame-summary frame_diff_changed_pixels") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &diff_changed_fraction_f, sizeof(diff_changed_fraction_f), "frame-summary frame_diff_changed_fraction") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &diff_abs_sum, sizeof(diff_abs_sum), "frame-summary frame_diff_abs_sum") < 0) return -1;
        if (gzwrite_exact(ctx->frames_gz, &diff_abs_mean_f, sizeof(diff_abs_mean_f), "frame-summary frame_diff_abs_mean") < 0) return -1;
        return 0;
    }

    int p = ctx->opt.summary_float_precision;
    gzprintf(ctx->frames_gz, "%" PRId64 ",%" PRId64, ctx->frame_index, frame->pts);
    write_time_field_precise(ctx->frames_gz, frame->pts, ctx->sink_time_base, p);
    gzprintf(ctx->frames_gz,
             ",%d,%.*g,%.*g,%.*g,%.*g,%.*g,%.*g,%.*g,%.*g,%.*g,%.*g,%d,%" PRId64 ",%.*g,%" PRId64 ",%.*g\n",
             n,
             p, mean_dx,
             p, mean_dy,
             p, mean_mag,
             p, median,
             p, p90,
             p, p95,
             p, max_mag,
             p, sum_mag,
             p, resultant,
             p, coherence,
             ctx->opt.frame_diff_threshold, diff_changed_pixels,
             p, diff_changed_fraction,
             diff_abs_sum,
             p, diff_abs_mean);
    return 0;
}

static int process_filtered_frame(Context *ctx, AVFrame *frame) {
    AVFrameSideData *sd = av_frame_get_side_data(frame, AV_FRAME_DATA_MOTION_VECTORS);
    int n = 0;
    AVMotionVector *mvs = NULL;

    if (sd) {
        if (sd->size % sizeof(AVMotionVector) != 0) {
            return fail("motion-vector side data has malformed size");
        }
        n = (int)(sd->size / sizeof(AVMotionVector));
        mvs = (AVMotionVector *)sd->data;
    }

    double *mags = n > 0 ? av_malloc_array((size_t)n, sizeof(double)) : NULL;
    bool *candidates = n > 0 ? av_malloc_array((size_t)n, sizeof(bool)) : NULL;
    if (n > 0 && !mags) {
        return fail("failed to allocate magnitude buffer");
    }
    if (n > 0 && !candidates) {
        av_free(mags);
        av_free(candidates);
        return fail("failed to allocate vector sampling buffers");
    }

    double sum_dx = 0.0;
    double sum_dy = 0.0;
    double sum_mag = 0.0;
    double max_mag = 0.0;
    bool write_all_vectors = strcmp(ctx->opt.vector_output, "all") == 0;
    bool write_sampled_vectors = strcmp(ctx->opt.vector_output, "sampled") == 0;
    bool frame_sampled = write_sampled_vectors && (ctx->frame_index % ctx->opt.vector_frame_stride) == 0;
    int candidate_count = 0;

    for (int i = 0; i < n; i++) {
        AVMotionVector *mv = &mvs[i];
        int dx = mv->dst_x - mv->src_x;
        int dy = mv->dst_y - mv->src_y;
        double mag = hypot((double)dx, (double)dy);
        mags[i] = mag;
        candidates[i] = false;
        sum_dx += dx;
        sum_dy += dy;
        sum_mag += mag;
        if (mag > max_mag) max_mag = mag;

        bool write_vector = write_all_vectors ||
            (frame_sampled &&
             mag >= ctx->opt.vector_min_magnitude &&
             vector_spatial_allowed(&ctx->opt, mv) &&
             vector_source_allowed(ctx->opt.vector_source, mv->source));
        if (write_vector) {
            candidates[i] = true;
            candidate_count++;
        }
    }
    ctx->raw_vector_rows += n;
    ctx->vector_candidate_rows += candidate_count;

    if (write_all_vectors || frame_sampled) {
        if (frame_sampled) {
            ctx->vector_sampled_frame_rows++;
        }
        for (int i = 0; i < n; i++) {
            if (!candidates[i]) continue;
            AVMotionVector *mv = &mvs[i];
            int dx = mv->dst_x - mv->src_x;
            int dy = mv->dst_y - mv->src_y;
            if (write_vector_row(ctx, frame, i, mv, dx, dy, mags[i]) < 0) {
                av_free(mags);
                av_free(candidates);
                return -1;
            }
        }
    }

    double mean_dx = n ? sum_dx / n : 0.0;
    double mean_dy = n ? sum_dy / n : 0.0;
    double mean_mag = n ? sum_mag / n : 0.0;
    double median = 0.0;
    double p90 = 0.0;
    double p95 = 0.0;
    if (n > 0) {
        qsort(mags, (size_t)n, sizeof(double), compare_double);
        median = percentile_sorted(mags, n, 0.50);
        p90 = percentile_sorted(mags, n, 0.90);
        p95 = percentile_sorted(mags, n, 0.95);
    }
    double resultant = hypot(sum_dx, sum_dy);
    double coherence = (n == 0 || sum_mag == 0.0) ? 0.0 : resultant / (sum_mag + MESTIMATE_COHERENCE_EPSILON);
    int64_t diff_changed_pixels = 0;
    int64_t diff_abs_sum = 0;
    double diff_changed_fraction = 0.0;
    double diff_abs_mean = 0.0;
    if (compute_frame_difference(ctx, frame, &diff_changed_pixels, &diff_changed_fraction, &diff_abs_sum, &diff_abs_mean) < 0) {
        av_free(mags);
        av_free(candidates);
        return -1;
    }

    if (write_frame_summary(ctx, frame, n, mean_dx, mean_dy, mean_mag, median, p90, p95,
                            max_mag, sum_mag, resultant, coherence, diff_changed_pixels,
                            diff_changed_fraction, diff_abs_sum, diff_abs_mean) < 0) {
        av_free(mags);
        av_free(candidates);
        return -1;
    }

    ctx->frame_rows++;
    ctx->frame_index++;
    av_free(mags);
    av_free(candidates);
    return 0;
}

static int drain_filter(Context *ctx) {
    int ret = 0;
    AVFrame *filt = av_frame_alloc();
    if (!filt) {
        return fail("failed to allocate filtered frame");
    }
    for (;;) {
        ret = av_buffersink_get_frame(ctx->buffersink, filt);
        if (ret == AVERROR(EAGAIN) || ret == AVERROR_EOF) {
            av_frame_free(&filt);
            return 0;
        }
        if (ret < 0) {
            char buf[AV_ERROR_MAX_STRING_SIZE];
            fferr(ret, buf, sizeof(buf));
            fprintf(stderr, "ERROR: failed to pull filtered frame: %s\n", buf);
            av_frame_free(&filt);
            return -1;
        }
        if (process_filtered_frame(ctx, filt) < 0) {
            av_frame_free(&filt);
            return -1;
        }
        av_frame_unref(filt);
    }
}

static int push_decoded_frame(Context *ctx, AVFrame *frame) {
    int ret = av_buffersrc_add_frame_flags(ctx->buffersrc, frame, AV_BUFFERSRC_FLAG_KEEP_REF);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to push decoded frame into filtergraph: %s\n", buf);
        return -1;
    }
    return drain_filter(ctx);
}

static int receive_decoder(Context *ctx) {
    AVFrame *frame = av_frame_alloc();
    if (!frame) {
        return fail("failed to allocate decoded frame");
    }
    for (;;) {
        int ret = avcodec_receive_frame(ctx->dec, frame);
        if (ret == AVERROR(EAGAIN) || ret == AVERROR_EOF) {
            av_frame_free(&frame);
            return 0;
        }
        if (ret < 0) {
            char buf[AV_ERROR_MAX_STRING_SIZE];
            fferr(ret, buf, sizeof(buf));
            fprintf(stderr, "ERROR: failed to decode frame: %s\n", buf);
            av_frame_free(&frame);
            return -1;
        }
        if (push_decoded_frame(ctx, frame) < 0) {
            av_frame_free(&frame);
            return -1;
        }
        av_frame_unref(frame);
    }
}

static int run_decode_filter(Context *ctx) {
    AVPacket *pkt = av_packet_alloc();
    if (!pkt) {
        return fail("failed to allocate packet");
    }

    for (;;) {
        int ret = av_read_frame(ctx->fmt, pkt);
        if (ret == AVERROR_EOF) break;
        if (ret < 0) {
            char buf[AV_ERROR_MAX_STRING_SIZE];
            fferr(ret, buf, sizeof(buf));
            fprintf(stderr, "ERROR: failed to read packet: %s\n", buf);
            av_packet_free(&pkt);
            return -1;
        }

        if (pkt->stream_index == ctx->video_stream_index) {
            ret = avcodec_send_packet(ctx->dec, pkt);
            if (ret < 0) {
                char buf[AV_ERROR_MAX_STRING_SIZE];
                fferr(ret, buf, sizeof(buf));
                fprintf(stderr, "ERROR: failed to send packet to decoder: %s\n", buf);
                av_packet_free(&pkt);
                return -1;
            }
            if (receive_decoder(ctx) < 0) {
                av_packet_free(&pkt);
                return -1;
            }
        }
        av_packet_unref(pkt);
    }
    av_packet_free(&pkt);

    int ret = avcodec_send_packet(ctx->dec, NULL);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to flush decoder: %s\n", buf);
        return -1;
    }
    if (receive_decoder(ctx) < 0) {
        return -1;
    }

    ret = av_buffersrc_add_frame_flags(ctx->buffersrc, NULL, 0);
    if (ret < 0) {
        char buf[AV_ERROR_MAX_STRING_SIZE];
        fferr(ret, buf, sizeof(buf));
        fprintf(stderr, "ERROR: failed to flush filtergraph: %s\n", buf);
        return -1;
    }
    return drain_filter(ctx);
}

static int close_outputs(Context *ctx) {
    int ok = 0;
    if (ctx->vectors_gz) {
        int ret = gzclose(ctx->vectors_gz);
        ctx->vectors_gz = NULL;
        if (ret != Z_OK) {
            fprintf(stderr, "ERROR: failed to close %s\n", ctx->paths.vectors);
            ok = -1;
        }
    }
    if (ctx->frames_gz) {
        int ret = gzclose(ctx->frames_gz);
        ctx->frames_gz = NULL;
        if (ret != Z_OK) {
            fprintf(stderr, "ERROR: failed to close %s\n", ctx->paths.frames);
            ok = -1;
        }
    }
    return ok;
}

static void json_escape(FILE *f, const char *s) {
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '\\' || *p == '"') {
            fputc('\\', f);
            fputc(*p, f);
        } else if (*p == '\n') {
            fputs("\\n", f);
        } else if (*p >= 0x20) {
            fputc(*p, f);
        }
    }
}

static void json_string(FILE *f, const char *key, const char *value, const char *suffix) {
    fprintf(f, "    \"%s\": \"", key);
    json_escape(f, value ? value : "");
    fprintf(f, "\"%s\n", suffix);
}

static void json_null(FILE *f, const char *key, const char *suffix) {
    fprintf(f, "    \"%s\": null%s\n", key, suffix);
}

static void utc_now(char out[32]) {
    time_t now = time(NULL);
    struct tm tm_utc;
    gmtime_r(&now, &tm_utc);
    strftime(out, 32, "%Y-%m-%dT%H:%M:%SZ", &tm_utc);
}

static int write_metadata(Context *ctx) {
    if (strcmp(ctx->opt.vector_output, "none") != 0) {
        if (sha256_file(ctx->paths.vectors, ctx->vectors_sha256, NULL) < 0) return -1;
    }
    if (sha256_file(ctx->paths.frames, ctx->frames_sha256, NULL) < 0) return -1;

    FILE *f = fopen(ctx->paths.metadata, "wb");
    if (!f) {
        perror(ctx->paths.metadata);
        return -1;
    }
    char created[32];
    utc_now(created);
    char filtergraph[512];
    snprintf(filtergraph, sizeof(filtergraph), "format=gray,mestimate=method=%s:mb_size=%d:search_param=%d",
             ctx->opt.method, ctx->opt.mb_size, ctx->opt.search_param);

    fprintf(f, "{\n");
    fprintf(f, "  \"schema_name\": \"%s\",\n", MESTIMATE_SCHEMA_NAME);
    fprintf(f, "  \"schema_version\": \"%s\",\n", MESTIMATE_SCHEMA_VERSION);
    fprintf(f, "  \"created_utc\": \"%s\",\n", created);
    fprintf(f, "  \"input\": {\n");
    json_string(f, "path", ctx->opt.input_path, ",");
    json_string(f, "basename", ctx->info.basename, ",");
    json_string(f, "sha256", ctx->info.sha256, ",");
    fprintf(f, "    \"size_bytes\": %" PRId64 ",\n", ctx->info.size_bytes);
    json_string(f, "container_format", ctx->info.container_format, ",");
    json_string(f, "video_codec", ctx->info.video_codec, ",");
    json_string(f, "pixel_format", ctx->info.pixel_format, ",");
    fprintf(f, "    \"width\": %d,\n", ctx->info.width);
    fprintf(f, "    \"height\": %d,\n", ctx->info.height);
    fprintf(f, "    \"nominal_fps\": \"%d/%d\",\n", ctx->info.nominal_fps.num, ctx->info.nominal_fps.den);
    fprintf(f, "    \"time_base\": \"%d/%d\",\n", ctx->info.time_base.num, ctx->info.time_base.den);
    fprintf(f, "    \"duration_seconds\": %.9f\n", ctx->info.duration_seconds);
    fprintf(f, "  },\n");
    fprintf(f, "  \"filtergraph\": {\n");
    json_string(f, "effective_filtergraph", filtergraph, ",");
    json_string(f, "method", ctx->opt.method, ",");
    fprintf(f, "    \"mb_size\": %d,\n", ctx->opt.mb_size);
    fprintf(f, "    \"search_param\": %d\n", ctx->opt.search_param);
    fprintf(f, "  },\n");
    fprintf(f, "  \"image_dynamics\": {\n");
    fprintf(f, "    \"frame_difference_lag_frames\": 1,\n");
    fprintf(f, "    \"frame_difference_threshold\": %d,\n", ctx->opt.frame_diff_threshold);
    fprintf(f, "    \"frame_difference_operator\": \"count pixels with abs(current_gray - previous_gray) > threshold; first frame is zero\"\n");
    fprintf(f, "  },\n");
    fprintf(f, "  \"frame_summary_encoding\": {\n");
    json_string(f, "format", frame_output_is_binary(&ctx->opt) ? "bin.gz" : "csv.gz", ",");
    fprintf(f, "    \"float_significant_digits\": %d,\n", ctx->opt.summary_float_precision);
    fprintf(f, "    \"binary_header_size\": %zu,\n", frame_output_is_binary(&ctx->opt) ? sizeof(FrameSummaryBinaryHeader) : (size_t)0);
    fprintf(f, "    \"binary_record_size\": %u,\n", frame_output_is_binary(&ctx->opt) ? FRAME_SUMMARY_BINARY_RECORD_SIZE : 0u);
    fprintf(f, "    \"binary_float_type\": \"%s\",\n", frame_output_is_binary(&ctx->opt) ? "float32" : "");
    fprintf(f, "    \"binary_endian_marker\": \"%s\"\n", frame_output_is_binary(&ctx->opt) ? "0x01020304" : "");
    fprintf(f, "  },\n");
    fprintf(f, "  \"vector_sampling\": {\n");
    json_string(f, "output", ctx->opt.vector_output, ",");
    json_string(f, "source", ctx->opt.vector_source, ",");
    fprintf(f, "    \"frame_stride\": %d,\n", ctx->opt.vector_frame_stride);
    fprintf(f, "    \"spatial_stride\": %d,\n", ctx->opt.vector_spatial_stride);
    fprintf(f, "    \"min_magnitude_px\": %.9f,\n", ctx->opt.vector_min_magnitude);
    fprintf(f, "    \"spatial_stride_definition\": \"keep vectors whose floor(dst_x / mb_size) and floor(dst_y / mb_size) are divisible by spatial_stride\",\n");
    fprintf(f, "    \"frame_summaries_use_all_vectors\": true\n");
    fprintf(f, "  },\n");
    fprintf(f, "  \"vector_encoding\": {\n");
    json_string(f, "format", vector_format_is_binary(&ctx->opt) ? "bin.gz" : "csv.gz", ",");
    fprintf(f, "    \"binary_header_size\": %zu,\n", vector_format_is_binary(&ctx->opt) ? sizeof(VectorBinaryHeader) : (size_t)0);
    fprintf(f, "    \"binary_record_size\": %u,\n", vector_format_is_binary(&ctx->opt) ? VECTOR_BINARY_RECORD_SIZE : 0u);
    fprintf(f, "    \"binary_float_type\": \"%s\",\n", vector_format_is_binary(&ctx->opt) ? "float32" : "");
    fprintf(f, "    \"binary_endian_marker\": \"%s\",\n", vector_format_is_binary(&ctx->opt) ? "0x01020304" : "");
    fprintf(f, "    \"binary_time_definition\": \"%s\"\n", vector_format_is_binary(&ctx->opt) ? "time_seconds is float32 for convenience; pts plus time_base remains the exact timing source" : "");
    fprintf(f, "  },\n");
    fprintf(f, "  \"software\": {\n");
    json_string(f, "tool_name", "mestimate-sidecar", ",");
    json_string(f, "tool_version", MESTIMATE_SIDECAR_VERSION, ",");
    json_string(f, "build_type", MESTIMATE_BUILD_TYPE, ",");
    json_string(f, "compiler", MESTIMATE_COMPILER, ",");
    fprintf(f, "    \"libavformat_version\": \"%u.%u.%u\",\n", LIBAVFORMAT_VERSION_MAJOR, LIBAVFORMAT_VERSION_MINOR, LIBAVFORMAT_VERSION_MICRO);
    fprintf(f, "    \"libavcodec_version\": \"%u.%u.%u\",\n", LIBAVCODEC_VERSION_MAJOR, LIBAVCODEC_VERSION_MINOR, LIBAVCODEC_VERSION_MICRO);
    fprintf(f, "    \"libavfilter_version\": \"%u.%u.%u\",\n", LIBAVFILTER_VERSION_MAJOR, LIBAVFILTER_VERSION_MINOR, LIBAVFILTER_VERSION_MICRO);
    fprintf(f, "    \"libavutil_version\": \"%u.%u.%u\",\n", LIBAVUTIL_VERSION_MAJOR, LIBAVUTIL_VERSION_MINOR, LIBAVUTIL_VERSION_MICRO);
    fprintf(f, "    \"ffmpeg_cli_version_if_available\": null\n");
    fprintf(f, "  },\n");
    fprintf(f, "  \"outputs\": {\n");
    char vectors_base[1200];
    char frames_base[1200];
    snprintf(vectors_base, sizeof(vectors_base), "%s.mestimate-v1.vectors.%s.gz",
             ctx->paths.stem, vector_format_is_binary(&ctx->opt) ? "bin" : "csv");
    snprintf(frames_base, sizeof(frames_base), "%s.mestimate-v1.frames.%s.gz",
             ctx->paths.stem, frame_output_is_binary(&ctx->opt) ? "bin" : "csv");
    if (strcmp(ctx->opt.vector_output, "none") == 0) {
        json_null(f, "vectors_file", ",");
    } else {
        json_string(f, "vectors_file", vectors_base, ",");
    }
    json_string(f, "frames_file", frames_base, ",");
    fprintf(f, "    \"vector_row_count\": %" PRId64 ",\n", ctx->vector_rows);
    fprintf(f, "    \"raw_vector_row_count\": %" PRId64 ",\n", ctx->raw_vector_rows);
    fprintf(f, "    \"vector_candidate_row_count\": %" PRId64 ",\n", ctx->vector_candidate_rows);
    fprintf(f, "    \"vector_sampled_frame_count\": %" PRId64 ",\n", ctx->vector_sampled_frame_rows);
    fprintf(f, "    \"frame_row_count\": %" PRId64 ",\n", ctx->frame_rows);
    if (strcmp(ctx->opt.vector_output, "none") == 0) {
        json_null(f, "vectors_sha256", ",");
    } else {
        json_string(f, "vectors_sha256", ctx->vectors_sha256, ",");
    }
    json_string(f, "frames_sha256", ctx->frames_sha256, "");
    fprintf(f, "  }\n");
    fprintf(f, "}\n");

    if (fclose(f) != 0) {
        perror(ctx->paths.metadata);
        return -1;
    }
    return 0;
}

static void cleanup(Context *ctx) {
    close_outputs(ctx);
    avfilter_graph_free(&ctx->graph);
    avcodec_free_context(&ctx->dec);
    avformat_close_input(&ctx->fmt);
    av_free(ctx->prev_gray);
    ctx->prev_gray = NULL;
    ctx->prev_gray_size = 0;
    ctx->has_prev_gray = false;
}

int main(int argc, char **argv) {
    Context ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.video_stream_index = -1;

    if (parse_args(argc, argv, &ctx.opt) < 0) {
        return 2;
    }
    build_paths(&ctx);
    if (!ctx.opt.dry_run && ensure_outputs_ok(&ctx) < 0) {
        return 2;
    }
    if (open_input(&ctx) < 0) {
        cleanup(&ctx);
        return 1;
    }
    if (init_filters(&ctx) < 0) {
        cleanup(&ctx);
        return 1;
    }
    if (ctx.opt.dry_run) {
        printf("dry-run ok: %s -> %s\n", ctx.opt.input_path, ctx.opt.output_dir);
        cleanup(&ctx);
        return 0;
    }
    if (write_headers(&ctx) < 0) {
        cleanup(&ctx);
        return 1;
    }
    if (run_decode_filter(&ctx) < 0) {
        cleanup(&ctx);
        return 1;
    }
    if (close_outputs(&ctx) < 0) {
        cleanup(&ctx);
        return 1;
    }
    if (write_metadata(&ctx) < 0) {
        cleanup(&ctx);
        return 1;
    }

    printf("wrote %" PRId64 " frames and %" PRId64 " vector rows to %s\n",
           ctx.frame_rows, ctx.vector_rows, ctx.opt.output_dir);
    cleanup(&ctx);
    return 0;
}
