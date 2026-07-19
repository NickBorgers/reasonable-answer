.PHONY: help install test cov doctor run lint clean

help:
	@echo "install  install dependencies into .venv"
	@echo "test     run the offline test suite (no network, no API keys)"
	@echo "cov      run tests with a coverage report"
	@echo "doctor   check the LiteLLM proxy, resolve the roster, report health"
	@echo "run      refine a report: make run Q='your question' [SEED=path.md]"

install:
	uv sync

test:
	uv run pytest

cov:
	uv run pytest --cov=reasonable_answer --cov-report=term-missing

doctor:
	uv run ra doctor -v

run:
	@test -n "$(Q)" || (echo "usage: make run Q='your question' [SEED=path.md]"; exit 2)
	uv run ra run -v -q "$(Q)" $(if $(SEED),--seed $(SEED),)

clean:
	rm -rf .pytest_cache .coverage **/__pycache__
