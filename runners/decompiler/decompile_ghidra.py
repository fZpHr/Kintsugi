import os
import subprocess
import sys
import tempfile
from pathlib import Path

GHIDRA_INSTALL = Path(os.getenv("GHIDRA_INSTALL_PATH", "/home/decompiler_user/ghidra"))
GHIDRA_HEADLESS = GHIDRA_INSTALL / 'support' / 'analyzeHeadless'

GHIDRA_APP_PROPERTIES = GHIDRA_INSTALL / 'Ghidra' / 'application.properties'

def main():
    cwd = Path.cwd()
    conts = sys.stdin.buffer.read()
    infile = tempfile.NamedTemporaryFile(dir=cwd, delete=False)
    infile.write(conts)
    infile.flush()
    inname = infile.name
    infile.close()

    project_dir = cwd / 'project'
    output_dir = cwd / 'output'
    project_dir.mkdir()
    output_dir.mkdir()

    output_file = output_dir / "out"
    parent_dir = Path(__file__).resolve().parent

    decompile_command = [
        f"{GHIDRA_HEADLESS}",
        str(project_dir),
        "temp",
        "-import",
        inname,
        "-postScript",
        f"{parent_dir}/ghidra_scripts/DecompilerExplorer.java",
        str(output_file)
    ]

    env = os.environ.copy()
    env['PATH'] = f"{parent_dir}/jdk/bin:{env['PATH']}"

    if not output_file.exists():
        decomp = subprocess.run(decompile_command, capture_output=True, env=env, cwd=cwd)
        if decomp.returncode != 0 or not output_file.exists():
            print(f'{decomp.stdout.decode()}\n{decomp.stderr.decode()}')
            sys.exit(1)

    with open(output_file, 'r') as f:
        print(f.read())


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--version':
        version = None
        revision = None
        for line in GHIDRA_APP_PROPERTIES.read_text().splitlines():
            parts = line.split('=')
            if len(parts) < 2:
                continue
            name, val = parts
            if name == 'application.version':
                version = val
                break
        for line in GHIDRA_APP_PROPERTIES.read_text().splitlines():
            parts = line.split('=')
            if len(parts) < 2:
                continue
            name, val = parts
            if name == 'application.revision.ghidra':
                revision = val
                break
        if version is not None and revision is not None:
            print(version)
            print(revision)
        else:
            print("Unknown")
            print("Unknown")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == '--name':
        print('Ghidra')
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == '--url':
        print('https://ghidra-sre.org')
        sys.exit(0)

    main()
