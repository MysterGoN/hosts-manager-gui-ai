# Repository instructions for AI agents

## Bilingual documentation

- Keep `README.md` (Russian) and `README.en.md` (English) synchronized.
- Any change that affects installation, usage, features, settings, build
  instructions, security, or known limitations must update both files in the
  same change.
- Preserve the language switch links at the top of both README files.
- The two files should communicate equivalent information rather than being
  literal word-for-word translations.

## Commits and releases

- Do not create a commit, tag, or push unless the user explicitly requests that
  Git operation. A request to commit does not imply permission to create a tag
  or push, and a request to create a tag does not imply permission to push it.
- Use Conventional Commits for every non-release commit. The commit type must
  either be explicit in the user's request or be agreed with the user before
  committing. If the context does not determine the type unambiguously, propose
  the intended type and wait for confirmation instead of choosing silently.
- Commit types determine the next Commitizen release but do not modify the
  version during an ordinary commit: `feat` requests a minor increment;
  `fix`, `perf`, and `refactor` request a patch increment; `docs`, `test`,
  `build`, `ci`, and `chore` do not request an increment by themselves. A
  breaking marker overrides the normal type increment as described below.
- Keep an ordinary commit separate from a tagged release:
  - An ordinary commit stages only the files in scope and creates a Conventional
    Commit. It must not change the project version, update `CHANGELOG.md`, or
    create a tag unless those changes were explicitly requested.
  - A tagged release requires an explicit request for a release or tag. Commit
    all intended source changes first, ensure the working tree is clean, run
    `make release-preview`, and verify that the proposed version matches the
    user's intent. Then use `make release`; do not reproduce its version,
    changelog, release-commit, or tag operations manually.
  - Commitizen owns the generated release commit type and message
    (`chore(release): ...`), so that generated commit does not require a separate
    type decision.
- Treat a change as breaking only when the user states that it is breaking or
  after explaining the incompatibility and getting agreement. Mark it with `!`
  in the Conventional Commit header or with a `BREAKING CHANGE:` footer.
- The project follows zero-major versioning during development. While the
  version is `0.x.y`, a breaking change increments the minor version and must not
  automatically move the project to `1.0.0`. Each `0.x.0` release may therefore
  be backward-incompatible.
- Never push commits or tags to a remote unless the user explicitly asks for the
  push. Prefer `git push --follow-tags` when an explicitly requested release push
  should publish both its release commit and annotated tag.
