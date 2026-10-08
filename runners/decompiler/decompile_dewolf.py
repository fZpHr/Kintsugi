import os
import subprocess
import sys
import tempfile
from pathlib import Path


DEWOLF_INSTALL = Path(os.getenv("DEWOLF_INSTALL_PATH", "/home/decompiler_user/dewolf"))
DEWOLF_DECOMPILE_PY = DEWOLF_INSTALL / 'decompile.py'


def main():
    cwd = Path.cwd()
    conts = sys.stdin.buffer.read()
    infile = tempfile.NamedTemporaryFile(dir=cwd, delete=False)
    infile.write(conts)
    infile.flush()

    output_dir = cwd / 'output'
    output_dir.mkdir()
    outfile = tempfile.NamedTemporaryFile(dir=output_dir, delete=False)
    outfile.close()

    decomp = subprocess.run(['pipenv', 'run', 'python', DEWOLF_DECOMPILE_PY, '-o', outfile.name, infile.name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(DEWOLF_INSTALL))
    if decomp.returncode != 0:
        print(f'{decomp.stdout.decode()}\n{decomp.stderr.decode()}')
        sys.exit(1)

    infile.close()

    with open(outfile.name, 'rb') as f:
        sys.stdout.buffer.write(f.read())


def version():
    p = subprocess.check_output(['git', 'describe', '--tags', '--abbrev=0', 'HEAD'], cwd=str(DEWOLF_INSTALL))
    ver = p.strip().decode()
    if ver[0] == 'v':
        ver = ver[1:]
    print(ver)
    p = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=str(DEWOLF_INSTALL))
    hash = p.strip().decode()
    print(hash)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--name':
        print('dewolf')
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == '--url':
        print('https://github.com/fkie-cad/dewolf')
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == '--version':
        version()
        sys.exit(0)

    main()
