# Cursor agent skills

Two skill packs are vendored as **git submodules** and copied into `.cursor/rules/` as `.md` files (see [Cursor setup for agent-skills](https://github.com/addyosmani/agent-skills/blob/main/docs/cursor-setup.md)).

## 1. [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) → `vendor/agent-skills/`

| Rule file | Skill |
|-----------|--------|
| `test-driven-development.md` | TDD / Prove-It |
| `code-review-and-quality.md` | Five-axis review |
| `incremental-implementation.md` | Small vertical slices |

**Update upstream:**

```bash
git submodule update --remote vendor/agent-skills
cp vendor/agent-skills/skills/test-driven-development/SKILL.md .cursor/rules/test-driven-development.md
# …repeat for other skills you use
```

## 2. [michaelsam94/agent-skills](https://github.com/michaelsam94/agent-skills) (Vercel skills fork) → `vendor/michaelsam94-agent-skills/`

Files are prefixed with `vercel-` so names stay clear next to the addyosmani rules.

| Rule file | Skill |
|-----------|--------|
| `vercel-react-best-practices.md` | React / Next.js performance |
| `vercel-web-design-guidelines.md` | Web Interface Guidelines audits (fetches live rules from Vercel) |
| `vercel-react-native-skills.md` | React Native / Expo |
| `vercel-react-view-transitions.md` | View Transition API / Next.js |
| `vercel-composition-patterns.md` | Composition patterns |
| `vercel-deploy-to-vercel.md` | Deploy to Vercel |
| `vercel-vercel-cli-with-tokens.md` | Vercel CLI + tokens |

**Update upstream:**

```bash
git submodule update --remote vendor/michaelsam94-agent-skills
cp vendor/michaelsam94-agent-skills/skills/react-best-practices/SKILL.md .cursor/rules/vercel-react-best-practices.md
# …repeat per skill
```

## Context size

Many rules load a lot of tokens. If responses feel noisy or slow:

- Move rarely used `.md` files out of `.cursor/rules/` into `.cursor/skills-archive/` (or Cursor **Notepads**), or
- Keep only the packs you need in `rules/` and copy others on demand.

## Clone on another machine

```bash
git submodule update --init --recursive
```
