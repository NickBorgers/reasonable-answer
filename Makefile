.PHONY: help install test cov doctor audition run serve docker docker-run lint clean

help:
	@echo "install  install dependencies into .venv"
	@echo "test     run the offline test suite (no network, no API keys)"
	@echo "cov      run tests with a coverage report"
	@echo "doctor   check the LiteLLM proxy, resolve the roster, report health"
	@echo "audition measure whether each rostered critic can actually find defects"
	@echo "run      refine a report: make run Q='your question' [SEED=path.md]"
	@echo "serve    run the web interface on http://127.0.0.1:8080"
	@echo "docker   build the container image"

install:
	uv sync --extra web

# --extra web because tests/test_web.py imports fastapi; without it a fresh clone
# fails at collection rather than running the suite.
test:
	uv run --extra web pytest

cov:
	uv run --extra web pytest --cov=reasonable_answer --cov-report=term-missing

doctor:
	uv run ra doctor -v

# Costs |critics| x |fixtures| x repetitions proxy calls. `doctor` only reads the
# cache this leaves behind, so run it deliberately rather than on every change.
audition:
	uv run ra audition -v

run:
	@test -n "$(Q)" || (echo "usage: make run Q='your question' [SEED=path.md]"; exit 2)
	uv run ra run -v -q "$(Q)" $(if $(SEED),--seed $(SEED),)

serve:
	uv run ra serve -v

docker:
	docker build -t reasonable-answer:latest .

docker-run: docker
	docker run --rm -p 127.0.0.1:8080:8080 \
		-v ra-runs:/data/runs \
		-v $(PWD)/config/roster.yaml:/etc/ra/roster.yaml:ro \
		reasonable-answer:latest

clean:
	rm -rf .pytest_cache .coverage **/__pycache__
