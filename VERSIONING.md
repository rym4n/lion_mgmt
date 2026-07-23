# Versioning

Release versions use `MAJOR.MINOR.YYYYMMDDHHmm`. The final component is the
release time in `Asia/Shanghai`, accurate to the minute.

This project keeps the `3.1` prefix. The version number and corresponding Git
or Docker tag therefore look like:

```text
3.1.202607231413
v3.1.202607231413
```

Generate the current release tag with:

```bash
bash scripts/aioe-version.sh generate
```

Validate a release tag with:

```bash
bash scripts/aioe-version.sh validate v3.1.202607231413
```

Legacy `x.y.patch` tags remain historical records but must not be used for new
releases. A project can publish at most one release in the same minute.
