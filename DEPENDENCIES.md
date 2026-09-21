# Verifying Python dependencies

The backend has one Python runtime dependency: NumPy 2.5.3. It has no transitive
runtime requirements. [requirements.txt](requirements.txt) is the committed
artifact lock, including the wheel filenames and their SHA-256 digests.

## Supported wheel matrix

| Interpreter | ABI | Linux/glibc x86_64 | Linux/glibc aarch64 |
| --- | --- | --- | --- |
| CPython 3.12 | cp312 | Locked | Locked |
| CPython 3.13 | cp313 | Locked | Locked |
| CPython 3.14 | cp314 | Locked | Locked |

These are the upstream `manylinux_2_27` / `manylinux_2_28` wheels (glibc 2.27+).
Free-threaded interpreters, other Python versions, musl, other architectures and
other operating systems are not supported by this installer. There is no source
build fallback. Backend tests run on all six combinations in GitHub CI; live
desktop integration is tested on x86_64 Omarchy.

## Installation enforcement

`install.py` checks the supported runtime, creates a private virtual environment
using the installed Python's bundled `venv`/`ensurepip`, and runs:

```sh
python -m pip --isolated install --require-hashes --only-binary=:all: \
  --force-reinstall -r requirements.txt
```

- The reviewed Git commit contains the accepted hashes; setup never fetches a
  new hash list from an index or generates a lock on the user's machine.
- A downloaded artifact must match a committed SHA-256 digest before installation.
- Hash checking applies to the entire dependency graph. An added dependency
  without its own pinned version and hashes fails installation.
- `--only-binary=:all:` prevents source builds and build-time dependency downloads.
- `--force-reinstall` verifies the artifact even if an older setup installed the
  same NumPy version without hashes. A hash failure does not uninstall the existing
  package. Setup exits before installing the model, service or desktop integration.
- `--isolated` ignores user pip configuration and pip environment overrides. The
  requirements file also enables hash checking and binary-only selection.

The offline regression tests in [tests/test_dependency_lock.py](tests/test_dependency_lock.py)
exercise real pip: a valid wheel installs, a modified wheel is rejected even when
that version is already installed, missing hashes fail, and a hashed source
archive never runs its build code.

System packages (including Python and whisper.cpp) remain managed by pacman.
Whisper's separately downloaded model retains its existing SHA-256 check.

## Updating the lock

1. Choose a NumPy release and inspect its dependency metadata and published
   artifacts on [PyPI](https://pypi.org/pypi/numpy/2.5.3/json).
2. Download the wheels for every supported interpreter/architecture, compute
   SHA-256 locally, and compare with the published digests. Reject yanked files.
3. Commit the exact filenames and verified hashes in `requirements.txt`. Do not
   add source archive hashes or loosen hash enforcement to work around a mismatch.
4. Run the installer regressions and all six CI jobs. If platform support changes,
   update the installer check, documentation and CI matrix together.

The 2.5.3 lock was generated from the linked PyPI release metadata and independently
checked against all six downloaded wheels. Pip's
[secure installation guide](https://pip.pypa.io/en/stable/topics/secure-installs/)
documents hash checking and binary-only installation.
