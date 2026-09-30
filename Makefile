.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install run test coverage mutations check clean

help:
	@printf '%s\n' 'make install   Install locked dependencies' 'make run       Start the API on 127.0.0.1:8000' 'make test      Run unittest' 'make coverage  Run tests and generate coverage reports' 'make mutations Run the curated mutation smoke suite' 'make check     Run coverage and then mutations' 'make clean     Remove generated reports and archives'

install:
	$(UV) sync --locked

run:
	$(UV) run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000

test:
	$(UV) run --locked python -m unittest discover -s tests -v

coverage:
	$(UV) run --locked python -m coverage run -m unittest discover -s tests -v
	$(UV) run --locked python -m coverage report
	$(UV) run --locked python -m coverage html
	$(UV) run --locked python -m coverage json

mutations:
	$(UV) run --locked python tools/mutation_smoke.py

check:
	$(MAKE) coverage
	$(MAKE) mutations

clean:
	rm -rf reports dist
	rm -f .coverage .coverage.*
