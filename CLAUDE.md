# Global Claude Code Instructions

## Working Style
- State assumptions before editing.
- If the task is ambiguous, ask one clarifying question.
- Turn each task into verifiable goals before coding.
- Prefer the smallest change that solves the problem.
- Touch only files needed for the requested change.
- Do not refactor unrelated code unless asked.
- Before finishing, verify with the narrowest relevant test.

## Conventions
- Code and comments in English
- Immutable/event-sourced types are @dataclass(frozen=True); mutable aggregates are plain @dataclass. Match this when adding types.
- Python >= 3.14
- Use uv for all package management (never pip)
- Use src/ layout (uv init --package)
- Run tests with: uv run pytest
- Run linting with: uv run ruff check .
- Format code with: uv run ruff format.

## Commands
``` bash
    # Install dependencies
    $ uv sync
    
    # Run tests with coverage
    $ uv run pytest
    
    # Run tests without coverage
    $ uv run pytest --no-cov -q
    
    # Run ruff check
    $ uv run ruff check src tests
    
    # Fix code with ruff
    $ uv run ruff check --fix src tests
    
    # Format code with ruff
    $	uv run ruff format src tests
    
    # Run pyright type checking
    $ uv run pyright
    
    # Clean up
    $ rm -rf htmlcov .coverage coverage.json
    $ rm -rf .pytest_cache .ruff_cache .mypy_cache dist build
    $ find . -type d -name __pycache__ -prune -exec rm -rf {} +
```

## Code Quality
- Keep functions focused and small (< 50 lines if possible).
- Match the existing project style before introducing a new pattern.
- Remove imports, variables, or functions made unused by your own changes.
- Avoid speculative abstractions for single-use code.

## Communication
- Be concise.
- Call out trade-offs when multiple reasonable approaches exist.
- Push back if a safer or smaller approach would meet the goal.