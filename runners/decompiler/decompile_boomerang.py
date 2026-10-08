import os
import subprocess
import sys
import tempfile
from pathlib import Path


BOOMERANG_INSTALL = Path(os.getenv("BOOMERANG_INSTALL_PATH", "/usr/bin"))
BOOMERANG_CLI = BOOMERANG_INSTALL / 'boomerang-cli'


def log_problems(log, infile_name):
    """The warnings and errors of a Boomerang log, without its table layout
    (``Level | File | Line | Message``) nor the temporary file name."""
    problems = []
    for line in log.splitlines():
        parts = [part.strip() for part in line.split('|', 3)]
        if len(parts) == 4 and parts[0] in ('Warn', 'Error'):
            problems.append(parts[3].replace(f"'{infile_name}'", 'the binary'))
    return '\n'.join(problems)


def main():
    cwd = Path.cwd()
    conts = sys.stdin.buffer.read()

    # Boomerang only has 32-bit loaders: don't make it fail on a 64-bit ELF.
    if conts[:4] == b'\x7fELF' and conts[4:5] == b'\x02':
        print('only 32-bit binaries are supported, and this is a 64-bit ELF.')
        sys.exit(1)

    infile = tempfile.NamedTemporaryFile(dir=cwd, delete=False)
    infile.write(conts)
    infile.flush()

    decomp = subprocess.run([BOOMERANG_CLI, infile.name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    if decomp.returncode != 0:
        log = f'{decomp.stdout.decode()}\n{decomp.stderr.decode()}'
        print(log_problems(log, infile.name) or log)
        sys.exit(1)

    infile.close()

    outputs = cwd / 'output' / Path(infile.name).name
    for source in outputs.glob('*.c'):
        with open(source, 'rb') as f:
            sys.stdout.buffer.write(f.read())


def version():
    proc = subprocess.run([BOOMERANG_CLI, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # boomerang-cli v0.5.2
    output = proc.stdout.decode()
    assert output.startswith('boomerang-cli ')
    version = output.split(' ')[1]
    assert version.startswith('v')
    version = version[1:]

    print(version)
    print()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--name':
        print('Boomerang')
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == '--url':
        print('https://github.com/BoomerangDecompiler/boomerang')
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == '--version':
        version()
        sys.exit(0)

    main()
