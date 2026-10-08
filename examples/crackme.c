/* A tiny crackme to try Kintsugi with:
 *
 *   gcc -O2 -s -o crackme examples/crackme.c
 *
 * then drop the `crackme` binary on the Analyze page. */
#include <stdio.h>
#include <string.h>

static const unsigned char secret[] = {0x37, 0x3c, 0x31, 0x3a, 0x2b, 0x3b, 0x27, 0x30, 0x2d, 0x00};

static int check(const char *s) {
    size_t n = strlen(s);
    if (n != 9) return 0;
    for (size_t i = 0; i < n; i++)
        if ((unsigned char)(s[i] ^ 0x5a) != secret[i]) return 0;
    return 1;
}

int main(int argc, char **argv) {
    if (argc < 2) { puts("usage: check <password>"); return 1; }
    puts(check(argv[1]) ? "Access granted" : "Access denied");
    return 0;
}
