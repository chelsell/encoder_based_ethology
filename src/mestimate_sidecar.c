#define _POSIX_C_SOURCE 200809L

#include "mestimate_sidecar.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
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
    int mb_size;
    int search_param;
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
    int64_t vector_rows;
    int64_t frame_rows;
    int64_t frame_index;
    char vectors_sha256[65];
    char frames_sha256[65];
} Context;

static void usage(FILE *out) {
    fprintf(out,
            "Usage: mestimate-sidecar --input PATH --output-dir DIR [options]\n\n"
            "Options:\n"
            "  --method METHOD          Default: epzs\n"
            "  --mb-size INTEGER        Default: 16\n"
            "  --search-param INTEGER   Default: 12\n"
            "  --force, --overwrite     Replace existing sidecar files\n"
            "  --dry-run                Validate setup without writing sidecars\n"
            "  --help                   Show this help\n"
            "  --version                Print version and FFmpeg library versions\n\n"
            "Outputs:\n"
            "  <stem>.mestimate-v1.vectors.csv.gz\n"
            "  <stem>.mestimate-v1.frames.csv.gz\n"
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

static int parse_args(int argc, char **argv, Options *opt) {
    *opt = (Options){
        .method = MESTIMATE_DEFAULT_METHOD,
        .mb_size = MESTIMATE_DEFAULT_MB_SIZE,
        .search_param = MESTIMATE_DEFAULT_SEARCH_PARAM,
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
    snprintf(ctx->paths.vectors, sizeof(ctx->paths.vectors), "%s/%s.mestimate-v1.vectors.csv.gz",
             ctx->opt.output_dir, ctx->paths.stem);
    snprintf(ctx->paths.frames, sizeof(ctx->paths.frames), "%s/%s.mestimate-v1.frames.csv.gz",
             ctx->opt.output_dir, ctx->paths.stem);
    snprintf(ctx->paths.metadata, sizeof(ctx->paths.metadata), "%s/%s.mestimate-v1.metadata.json",
             ctx->opt.output_dir, ctx->paths.stem);
}

static int ensure_outputs_ok(Context *ctx) {
    if (make_dirs(ctx->opt.output_dir) < 0) {
        return -1;
    }
    if (!ctx->opt.force &&
        (file_exists(ctx->paths.vectors) || file_exists(ctx->paths.frames) || file_exists(ctx->paths.metadata))) {
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
    ctx->vectors_gz = gzopen(ctx->paths.vectors, "wb");
    if (!ctx->vectors_gz) {
        perror(ctx->paths.vectors);
        return -1;
    }
    ctx->frames_gz = gzopen(ctx->paths.frames, "wb");
    if (!ctx->frames_gz) {
        perror(ctx->paths.frames);
        return -1;
    }
    gzputs(ctx->vectors_gz,
           "frame_index,pts,time_seconds,vector_index,source,w,h,src_x,src_y,dst_x,dst_y,motion_x,motion_y,motion_scale,flags,dx_px,dy_px,magnitude_px\n");
    gzputs(ctx->frames_gz,
           "frame_index,pts,time_seconds,n_vectors,mean_dx_px,mean_dy_px,mean_magnitude_px,median_magnitude_px,p90_magnitude_px,p95_magnitude_px,max_magnitude_px,sum_magnitude_px,resultant_magnitude_px,coherence\n");
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
    if (n > 0 && !mags) {
        return fail("failed to allocate magnitude buffer");
    }

    double sum_dx = 0.0;
    double sum_dy = 0.0;
    double sum_mag = 0.0;
    double max_mag = 0.0;

    for (int i = 0; i < n; i++) {
        AVMotionVector *mv = &mvs[i];
        int dx = mv->dst_x - mv->src_x;
        int dy = mv->dst_y - mv->src_y;
        double mag = hypot((double)dx, (double)dy);
        mags[i] = mag;
        sum_dx += dx;
        sum_dy += dy;
        sum_mag += mag;
        if (mag > max_mag) max_mag = mag;

        gzprintf(ctx->vectors_gz,
                 "%" PRId64 ",%" PRId64,
                 ctx->frame_index, frame->pts);
        write_time_field(ctx->vectors_gz, frame->pts, ctx->sink_time_base);
        gzprintf(ctx->vectors_gz,
                 ",%d,%" PRId32 ",%u,%u,%" PRId16 ",%" PRId16 ",%" PRId16 ",%" PRId16
                 ",%" PRId32 ",%" PRId32 ",%u,%" PRIu64 ",%d,%d,%.9f\n",
                 i, mv->source, mv->w, mv->h, mv->src_x, mv->src_y, mv->dst_x, mv->dst_y,
                 mv->motion_x, mv->motion_y, mv->motion_scale, mv->flags, dx, dy, mag);
        ctx->vector_rows++;
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

    gzprintf(ctx->frames_gz, "%" PRId64 ",%" PRId64, ctx->frame_index, frame->pts);
    write_time_field(ctx->frames_gz, frame->pts, ctx->sink_time_base);
    gzprintf(ctx->frames_gz,
             ",%d,%.9f,%.9f,%.9f,%.9f,%.9f,%.9f,%.9f,%.9f,%.9f,%.9f\n",
             n, mean_dx, mean_dy, mean_mag, median, p90, p95, max_mag, sum_mag, resultant, coherence);

    ctx->frame_rows++;
    ctx->frame_index++;
    av_free(mags);
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

static void utc_now(char out[32]) {
    time_t now = time(NULL);
    struct tm tm_utc;
    gmtime_r(&now, &tm_utc);
    strftime(out, 32, "%Y-%m-%dT%H:%M:%SZ", &tm_utc);
}

static int write_metadata(Context *ctx) {
    if (sha256_file(ctx->paths.vectors, ctx->vectors_sha256, NULL) < 0) return -1;
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
    snprintf(vectors_base, sizeof(vectors_base), "%s.mestimate-v1.vectors.csv.gz", ctx->paths.stem);
    snprintf(frames_base, sizeof(frames_base), "%s.mestimate-v1.frames.csv.gz", ctx->paths.stem);
    json_string(f, "vectors_file", vectors_base, ",");
    json_string(f, "frames_file", frames_base, ",");
    fprintf(f, "    \"vector_row_count\": %" PRId64 ",\n", ctx->vector_rows);
    fprintf(f, "    \"frame_row_count\": %" PRId64 ",\n", ctx->frame_rows);
    json_string(f, "vectors_sha256", ctx->vectors_sha256, ",");
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

    printf("wrote %" PRId64 " frames and %" PRId64 " vectors to %s\n",
           ctx.frame_rows, ctx.vector_rows, ctx.opt.output_dir);
    cleanup(&ctx);
    return 0;
}
