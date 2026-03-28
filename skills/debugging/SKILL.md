---
name: debugging
description: Use when encountering bugs, test failures, or unexpected behavior
trigger: when fixing bugs, test failures, or unexpected behavior
---

# Systematic Debugging Skill

Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes.

## Principles

1. **Reproduce First** - Can you reliably trigger the issue?
2. **Minimize** - What's the smallest case that shows the problem?
3. **Isolate** - Change one thing at a time
4. **Verify** - Confirm the fix addresses root cause

## Process

1. **Observe**
   - What is the actual behavior?
   - What is the expected behavior?
   - What error messages exist?

2. **Hypothesize**
   - List possible causes
   - Rank by likelihood
   - Design tests to rule out

3. **Investigate**
   - Add logging/prints
   - Check assumptions
   - Trace execution flow

4. **Fix**
   - Make minimal change
   - Add test for regression
   - Verify fix works

## Anti-Patterns

- Guessing without evidence
- Changing multiple things
- Ignoring error messages
- "It works on my machine"