# Aurora U-Boot releases

Aurora's version series starts at `v0.1.0`, independently of the upstream U-Boot version. `main` is the public integration/release branch; `aurora-wip` retains development work and `aurora-stable` preserves its historical state.

1. Integrate the intended development history into `main` without rewriting existing branches.
2. Update `doc/board/apple/aurora-releases/<version>.md` and run the two strict configuration builds. Require a green `Apple builds` CI run for the exact source revision.
3. Create and push an annotated `v<version>` tag on that revision.
4. CI packages both configurations and creates a **draft** GitHub release, preserving any existing draft notes on re-runs and refusing to change published releases.
5. Review the notes, match Aura and board DTBs, and qualify the exact artifacts on the target hardware before publishing.

Pushes to `main` or `aurora-wip`, pull requests, and manual runs build/upload Actions artifacts without creating a release. `tools/aurora_release.py` packages clean-source builds locally. Pass `--compiler` when cross-compiling so the manifest names the actual compiler.

Each build archive includes the nodtb payload, a deterministic gzip payload, the default embedded/control DTB, `.config`, licenses and source/compiler metadata. Debug archives preserve the ELF image, symbols and linker map. Packaging does not deploy or install firmware.
