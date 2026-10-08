"""Which binaries each decompiler can handle.

Decompilers that cannot handle a binary are skipped instead of being run only
to fail. Only what is known for sure is listed in ``SUPPORT``: a decompiler
that is not in it, or a binary whose format is not recognised, is always tried.
"""
import struct

# What each decompiler handles. A missing key means "any".
SUPPORT = {
    # 0.5.2 only ships 32-bit loaders (ELF32, Win32 PE, Mach-O, DOS) and the
    # x86 (32-bit), PPC, SPARC and ST20 front ends.
    'Boomerang': {
        'bits': {32},
        'archs': {'x86', 'ppc', 'sparc'},
        'formats': {'elf', 'pe', 'macho'},
    },
}

ELF_MACHINES = {
    2: 'sparc', 3: 'x86', 8: 'mips', 20: 'ppc', 21: 'ppc', 22: 's390', 40: 'arm',
    43: 'sparc', 62: 'x86-64', 183: 'arm64', 243: 'riscv',
}
PE_MACHINES = {0x14c: 'x86', 0x8664: 'x86-64', 0x1c0: 'arm', 0x1c4: 'arm', 0xaa64: 'arm64'}
MACHO_CPUS = {7: 'x86', 0x01000007: 'x86-64', 12: 'arm', 0x0100000c: 'arm64', 18: 'ppc',
              0x01000012: 'ppc'}


def identify(data):
    """Return ``{'format', 'arch', 'bits'}`` from the first bytes of a binary,
    or ``None`` when its format is not recognised."""
    try:
        if data[:4] == b'\x7fELF':
            bits = {1: 32, 2: 64}.get(data[4])
            endian = '<' if data[5] == 1 else '>'
            (machine,) = struct.unpack_from(endian + 'H', data, 18)
            return {'format': 'elf', 'arch': ELF_MACHINES.get(machine, f'elf-{machine}'), 'bits': bits}

        if data[:2] == b'MZ':
            (pe_offset,) = struct.unpack_from('<I', data, 0x3c)
            if data[pe_offset:pe_offset + 4] != b'PE\0\0':
                return {'format': 'dos', 'arch': 'x86', 'bits': 16}
            (machine,) = struct.unpack_from('<H', data, pe_offset + 4)
            (magic,) = struct.unpack_from('<H', data, pe_offset + 24)
            bits = {0x10b: 32, 0x20b: 64}.get(magic)
            return {'format': 'pe', 'arch': PE_MACHINES.get(machine, f'pe-{machine:#x}'), 'bits': bits}

        for magic, endian, bits in ((b'\xce\xfa\xed\xfe', '<', 32), (b'\xcf\xfa\xed\xfe', '<', 64),
                                    (b'\xfe\xed\xfa\xce', '>', 32), (b'\xfe\xed\xfa\xcf', '>', 64)):
            if data[:4] == magic:
                (cpu,) = struct.unpack_from(endian + 'i', data, 4)
                return {'format': 'macho', 'arch': MACHO_CPUS.get(cpu, f'macho-{cpu:#x}'), 'bits': bits}
    except (struct.error, IndexError):
        pass
    return None


def describe(info):
    return f"{info['bits'] or '?'}-bit {info['arch']} {info['format'].upper()}"


def unsupported_reason(decompiler_name, info):
    """Why ``decompiler_name`` cannot handle a binary described by ``info``
    (from ``identify``), or ``None`` if it can, or might."""
    support = SUPPORT.get(decompiler_name)
    if support is None or info is None:
        return None
    checks = (('formats', 'format'), ('archs', 'arch'), ('bits', 'bits'))
    for key, field in checks:
        if key in support and info[field] is not None and info[field] not in support[key]:
            return f"{decompiler_name} does not support {describe(info)} binaries."
    return None


def binary_info(binary):
    """``identify`` the file of a ``Binary``."""
    try:
        with binary.file.open('rb') as f:
            return identify(f.read(64 * 1024))
    except (OSError, ValueError):
        return None
