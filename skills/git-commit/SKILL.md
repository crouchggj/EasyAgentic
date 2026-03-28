---
name: git-commit
description: Guidelines for writing clear, consistent git commit messages
trigger: when creating git commits
---

# Git Commit Skill

Use when creating git commits to ensure clear, consistent commit messages.

## Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

## Types

| Type | Description |
|------|-------------|
| feat | New feature |
| fix | Bug fix |
| docs | Documentation only |
| style | Formatting, no code change |
| refactor | Code change without fix/feature |
| perf | Performance improvement |
| test | Adding/updating tests |
| chore | Build, config, dependencies |
| ci | CI/CD changes |
| revert | Revert previous commit |

## Rules

1. **Subject line**
   - Use imperative mood ("add" not "added")
   - No period at end
   - Max 72 characters
   - Start with type prefix

2. **Body** (optional)
   - Separate from subject with blank line
   - Explain what and why, not how
   - Wrap at 72 characters

3. **Footer** (optional)
   - Breaking changes: `BREAKING CHANGE: ...`
   - Close issues: `Closes #123`

## Examples

```
feat: add user authentication

- Implement JWT token validation
- Add login/logout endpoints
- Store tokens in httpOnly cookies

Closes #42
```

```
fix(api): handle null response from server

The API was crashing when server returned null instead of empty array.
Now we default to empty array for safety.
```

```
refactor: extract validation logic into separate module

Move input validation from controllers to dedicated validators
for better separation of concerns and reusability.
```

## Anti-Patterns

- `fix: fixed stuff` - too vague
- `update code` - no type prefix
- `WIP` - should not be committed
- `fix bug` - doesn't explain what or why
- Mixed changes in one commit - should split