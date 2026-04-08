# Cursor + [agent-skills](https://github.com/addyosmani/agent-skills)

This project vendors [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) as a **git submodule** at `vendor/agent-skills/`.

## What’s in `.cursor/rules/`

Per [docs/cursor-setup.md](https://github.com/addyosmani/agent-skills/blob/main/docs/cursor-setup.md), a small set of skills is copied here so Cursor loads them automatically (avoid loading all 19 at once—context limits).

| Rule file | Skill |
|-----------|--------|
| `test-driven-development.md` | TDD / Prove-It |
| `code-review-and-quality.md` | Five-axis review |
| `incremental-implementation.md` | Small vertical slices |

## Update the upstream skills

```bash
git submodule update --remote vendor/agent-skills
```

Then refresh the copies you care about, for example:

```bash
cp vendor/agent-skills/skills/test-driven-development/SKILL.md .cursor/rules/test-driven-development.md
```

## Add more skills

Copy any `vendor/agent-skills/skills/<name>/SKILL.md` to `.cursor/rules/<name>.md`, or use Cursor **Notepads** for occasional skills (see upstream README).

## Clone on another machine

```bash
git submodule update --init --recursive
```
