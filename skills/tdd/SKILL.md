---
name: tdd
description: Test-Driven Development workflow for writing tests before implementation
trigger: when implementing any feature or bugfix
---

# Test-Driven Development Skill

Use when implementing any feature or bugfix, before writing implementation code.

## The Cycle

1. **Red** - Write a failing test
2. **Green** - Write minimal code to pass
3. **Refactor** - Clean up while keeping tests green

## Rules

1. Write no production code without a failing test
2. Write only enough test to fail
3. Write only enough code to pass

## Process

1. **Start with a test**
   ```python
   def test_feature_does_something():
       result = feature()
       assert result == expected
   ```

2. **Run test (should fail)**
   - Confirm test runs
   - Confirm failure reason is correct

3. **Write minimal code**
   - Just enough to pass
   - Don't over-engineer

4. **Run test (should pass)**
   - Green! Now refactor if needed

5. **Refactor**
   - Clean up code
   - Keep tests passing

## Benefits

- Documents intent
- Catches regressions
- Enables refactoring
- Forces modular design