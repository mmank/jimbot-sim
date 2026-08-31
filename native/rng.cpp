// The game's randomness, in C++, bit-for-bit with src/balatro/rng.py.
//
// This is the first slice of a possible port, and deliberately the first: if
// the RNG cannot be reproduced exactly then nothing downstream can be, and a
// day spent finding that out beats three weeks. Everything the simulator does
// hangs off these three functions -- which joker the shop offers, which card
// The Hook discards, whether a Glass card shatters.
//
// Two things here are genuinely fragile, and both are compiler business
// rather than logic:
//
//   pseudohash folds `(a / num) * byte * pi + pi * i` per character. A
//   compiler is free to contract the multiply-add into an FMA, which rounds
//   once instead of twice and changes the last bits. Built with
//   -ffp-contract=off, and the differential test below is what proves it.
//
//   round13 is Lua's string.format("%.13f"), so it is printf's rounding, not
//   ours. glibc rounds correctly; older MinGW routed %f through MSVCRT, which
//   did not. __USE_MINGW_ANSI_STDIO asks for the conforming one.
//
// Plain C ABI on purpose: ctypes calls this with no build system, no
// pybind11, and no wheel to keep current on two platforms.

#ifdef __MINGW32__
#define __USE_MINGW_ANSI_STDIO 1
#endif

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#if defined(_WIN32)
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {

const double kPi = 3.14159265358979323846;

inline uint64_t bits_of(double d) {
    uint64_t u;
    std::memcpy(&u, &d, sizeof u);
    return u;
}

inline double double_of(uint64_t u) {
    double d;
    std::memcpy(&d, &u, sizeof d);
    return d;
}

// Python's float % returns a result with the sign of the divisor; for a
// positive divisor and any finite left operand that is fmod adjusted upward
// once. The game only ever takes % 1 of positives, but the adjustment is kept
// so this matches Python rather than matching it by luck.
inline double py_mod1(double x) {
    double r = std::fmod(x, 1.0);
    if (r != 0.0 && r < 0.0) r += 1.0;
    return r;
}

struct TW223 {
    uint64_t gen[4];
};

// L'Ecuyer table 3, first entry: L=64, J=4, k=223, N1=49.
const int kK[4] = {63, 58, 55, 47};
const int kQ[4] = {31, 19, 24, 21};
const int kS[4] = {18, 28, 7, 8};

void tw223_seed(TW223 *t, double seed) {
    uint32_t r = 0x11090601u;               // 64-k[i], four 8-bit constants
    for (int i = 0; i < 4; ++i) {
        uint64_t m = 1ull << (r & 255u);
        r >>= 8;
        seed = seed * kPi + 2.7182818284590452354;
        uint64_t u = bits_of(seed);
        if (u < m) u += m;                  // keep the top k[i] bits non-zero
        t->gen[i] = u;
    }
    for (int i = 0; i < 10; ++i) {
        uint64_t acc = 0;
        for (int j = 0; j < 4; ++j) {
            uint64_t z = t->gen[j];
            z = (((z << kQ[j]) ^ z) >> (kK[j] - kS[j])) ^
                ((z & (~0ull << (64 - kK[j]))) << kS[j]);
            acc ^= z;
            t->gen[j] = z;
        }
    }
}

double tw223_step(TW223 *t) {
    uint64_t acc = 0;
    for (int j = 0; j < 4; ++j) {
        uint64_t z = t->gen[j];
        z = (((z << kQ[j]) ^ z) >> (kK[j] - kS[j])) ^
            ((z & (~0ull << (64 - kK[j]))) << kS[j]);
        acc ^= z;
        t->gen[j] = z;
    }
    return double_of((acc & 0x000FFFFFFFFFFFFFull) | 0x3FF0000000000000ull);
}

}  // namespace

// The string hash, folded in reverse over latin-1 bytes.
EXPORT double balatro_pseudohash(const unsigned char *data, int length) {
    double num = 1.0;
    for (int i = length; i > 0; --i) {
        num = py_mod1((1.1239285023 / num) * (double)data[i - 1] * kPi +
                      kPi * (double)i);
    }
    return num;
}

// Lua's string.format("%.13f"), parsed back.
EXPORT double balatro_round13(double value) {
    char buffer[64];
    std::snprintf(buffer, sizeof buffer, "%.13f", value);
    return std::strtod(buffer, nullptr);
}

// One pool step: the new state, given the old one.
EXPORT double balatro_pool_step(double state) {
    return std::fabs(balatro_round13(py_mod1(2.134453429141 + state * 1.72431234)));
}

EXPORT void *balatro_tw223_new(double seed) {
    TW223 *t = new TW223();
    tw223_seed(t, seed);
    return t;
}

EXPORT void balatro_tw223_free(void *handle) {
    delete static_cast<TW223 *>(handle);
}

EXPORT double balatro_tw223_step(void *handle) {
    return tw223_step(static_cast<TW223 *>(handle));
}

// The whole draw in one call, so a benchmark measures the generator rather
// than the cost of crossing into Python four times.
EXPORT double balatro_tw223_draw(void *handle, double low, double high,
                                 int mode) {
    double d = tw223_step(static_cast<TW223 *>(handle)) - 1.0;
    if (mode == 0) return d;
    if (mode == 1) return std::floor(d * low) + 1.0;
    return std::floor(d * (high - low + 1.0)) + low;
}

// Loops inside the library, so a benchmark measures the generator instead of
// the cost of crossing into Python. The gap between this and the per-call
// number is the whole architectural lesson of the port: cross the boundary
// once per environment step, never once per draw.
EXPORT double balatro_bench_tw223(void *handle, int n) {
    TW223 *t = static_cast<TW223 *>(handle);
    double acc = 0.0;
    for (int i = 0; i < n; ++i) acc += tw223_step(t);
    return acc;
}

EXPORT double balatro_bench_pseudohash(const unsigned char *data, int length,
                                       int n) {
    double acc = 0.0;
    for (int i = 0; i < n; ++i) acc += balatro_pseudohash(data, length);
    return acc;
}
