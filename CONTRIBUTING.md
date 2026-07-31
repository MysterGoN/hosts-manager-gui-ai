# Contributing

## Commit Convention

Use Conventional Commits for commit messages:

```text
<type>(<scope>): <description>
```

The `scope` is optional. Keep the description short, imperative, and lowercase.

Common types:

- `feat`: new user-facing functionality
- `fix`: bug fix
- `refactor`: code change without behavior change
- `test`: tests only
- `docs`: documentation only
- `build`: packaging, dependency, or build changes
- `ci`: CI or automation changes
- `chore`: maintenance that does not fit the other types

Examples:

```text
feat(ui): add host entry dialog
fix(core): preserve selected ip on import
refactor: rename package to hmg
docs: add commit convention
```

Use `make commit` for an interactive Commitizen prompt. After running
`make install-hooks`, the `commit-msg` hook rejects messages that do not follow
the convention.

During initial development the project follows zero-major versioning. A `fix`,
`perf`, or `refactor` commit increments the patch version; `feat` increments the
minor version. Mark a breaking change with `!` or a `BREAKING CHANGE:` footer.
While the version is `0.x.y`, a breaking change increments the minor version
instead of moving the project to `1.0.0`:

```text
feat(core)!: replace the state file format
```

Preview the next release with `make release-preview`. Run `make release` from a
clean working tree to execute the checks, update the version and changelog, and
create an annotated `v*` tag. Push the release commit and tag explicitly with
`git push --follow-tags`.
